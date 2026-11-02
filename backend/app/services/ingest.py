"""投喂：接收截图 / 一句话，去重落库，排队等抽取。

对应文档：specs/03-ingest.md

三条规则：
  ① 上传**立即返回**，抽取在后台跑（用户下播后要批量传 10 张，不能等）
  ② 同一张图传两次要拦住（sha256 + UNIQUE(user_id, file_hash)）
  ③ 原图可能被删（用户关"保留原图"开关），所以抽取要能从 ocr_text 走
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import AppError, ErrorCode
from app.models.fact import Fact
from app.models.input import ChatSegment, RawInput
from app.models.person import Person
from app.services import extract, storage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UploadItem:
    """一张待处理的图。api 层负责把 UploadFile 读成 bytes。"""

    filename: str
    content: bytes
    suffix: str


@dataclass(slots=True)
class IngestResult:
    """单张图的处理结果。"""

    filename: str
    raw_input_id: int | None
    status: str
    duplicate: bool = False
    existing_id: int | None = None
    error: str | None = None


def validate_image_count(count: int) -> None:
    """一次投喂的图片数量校验。"""
    if count == 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "至少要传一张图", status_code=400)
    if count > settings.max_upload_count:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"一次最多 {settings.max_upload_count} 张",
            status_code=400,
        )


def build_upload_item(filename: str | None, content: bytes) -> UploadItem:
    """校验大小与真实格式，生成上传项。"""
    limit = settings.max_upload_mb * 1024 * 1024
    if len(content) > limit:
        raise AppError(
            ErrorCode.FILE_TOO_LARGE,
            f"单张不能超过 {settings.max_upload_mb}MB",
            status_code=413,
            detail={"max_mb": settings.max_upload_mb},
        )
    suffix = storage.sniff_image_suffix(content)
    if suffix is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "只支持 jpg / png / webp",
            status_code=400,
            detail={"filename": filename},
        )
    return UploadItem(filename=filename or "upload", content=content, suffix=suffix)


async def _find_duplicate(session: AsyncSession, content_hash: str) -> RawInput | None:
    """查同用户下是否已有同一张图（RLS 自动限本用户）。"""
    stmt = select(RawInput).where(RawInput.file_hash == content_hash).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()


async def ingest_images(
    session: AsyncSession,
    user_id: int,
    items: list[UploadItem],
    person_id: int | None = None,
) -> list[IngestResult]:
    """批量接收截图。全部重复也是正常结果（200），不是错误。"""
    if person_id is not None and await session.get(Person, person_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个人", status_code=404)

    results: list[IngestResult] = []
    # 已经落盘的文件路径。commit 失败时要把它们删掉——否则磁盘上留一堆
    # 没有任何 raw_inputs 行指向的截图，既占空间，也不符合"删就删干净"的承诺。
    written_paths: list[str] = []

    for item in items:
        content_hash = storage.compute_hash(item.content)
        duplicate = await _find_duplicate(session, content_hash)
        if duplicate is not None:
            results.append(
                IngestResult(
                    filename=item.filename,
                    raw_input_id=duplicate.id,
                    status=duplicate.status,
                    duplicate=True,
                    existing_id=duplicate.id,
                )
            )
            continue

        raw_input = RawInput(
            user_id=user_id,
            person_id=person_id,
            kind="screenshot",
            file_hash=content_hash,
            # 截断到列宽，中文文件名按字符数算足够宽松
            original_filename=(item.filename or "").strip()[:255] or None,
            status="pending",
        )
        session.add(raw_input)
        await session.flush()  # 拿 id 才能命名文件

        try:
            raw_input.file_path = storage.save_upload(
                user_id, raw_input.id or 0, item.content, item.suffix
            )
            written_paths.append(raw_input.file_path)
        except OSError as exc:
            # 单张存盘失败不影响其他张
            logger.warning("save upload failed user_id=%s error=%s", user_id, type(exc).__name__)
            raw_input.status = "failed"
            raw_input.error_msg = "文件写入失败"
            results.append(
                IngestResult(
                    filename=item.filename,
                    raw_input_id=raw_input.id,
                    status="failed",
                    error="文件写入失败",
                )
            )
            continue

        results.append(
            IngestResult(
                filename=item.filename,
                raw_input_id=raw_input.id,
                status=raw_input.status,
            )
        )

    try:
        await session.commit()
    except Exception:
        # commit 失败 = 这批 raw_inputs 一行都没进去，刚落盘的图全是孤儿。
        # 先回滚（连接可能处于失败事务里），再删文件，最后照常抛出去，
        # 让调用方看到真实错误，而不是"上传成功但库里查不到"。
        await session.rollback()
        for path in written_paths:
            storage.delete_file(path)
        logger.warning("ingest commit failed, cleaned %d orphan files", len(written_paths))
        raise
    return results


async def ingest_text(
    session: AsyncSession,
    user_id: int,
    text: str,
    person_id: int | None = None,
) -> RawInput:
    """存一句用户输入的话，等抽取。"""
    if person_id is not None and await session.get(Person, person_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个人", status_code=404)

    raw_input = RawInput(
        user_id=user_id,
        person_id=person_id,
        kind="text",
        raw_text=text,
        status="pending",
    )
    session.add(raw_input)
    await session.commit()
    return raw_input


async def list_raw_inputs(session: AsyncSession, limit: int = 20) -> list[RawInput]:
    """最近投喂的材料，新的在前。投喂页用它显示处理状态。"""
    stmt = select(RawInput).order_by(RawInput.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def get_raw_input(session: AsyncSession, raw_input_id: int) -> RawInput:
    """查处理状态。取不到（不存在或不是自己的）一律 404。"""
    raw_input = await session.get(RawInput, raw_input_id)
    if raw_input is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这条材料", status_code=404)
    return raw_input


async def assign_person(
    session: AsyncSession, raw_input_id: int, user_id: int, person_id: int
) -> RawInput:
    """把归属待定的材料指给一个人：改材料 + 改它下面的对话片段。重抽由调用方触发。"""
    raw_input = await get_raw_input(session, raw_input_id)
    if await session.get(Person, person_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个人", status_code=404)

    raw_input.person_id = person_id
    session.add(raw_input)
    await session.execute(
        update(ChatSegment)
        .where(ChatSegment.raw_input_id == raw_input_id)
        .values(person_id=person_id)
    )
    await session.commit()
    return raw_input


async def dismiss(session: AsyncSession, raw_input_id: int) -> RawInput:
    """把一条材料标成"忽略"。归属待定的材料以前只能干看着，没有任何处置手段。

    顺手把它下面的待确认事实一起驳回：用户的意思是"这条材料别管了"，
    留着几条没有归属的待确认事实只会一直堵在确认页。
    """
    raw_input = await get_raw_input(session, raw_input_id)
    if raw_input.status == "ignored":
        return raw_input

    raw_input.status = "ignored"
    raw_input.error_msg = None
    session.add(raw_input)

    await session.execute(
        update(Fact)
        .where(
            Fact.source_type == "screenshot",
            Fact.source_id == raw_input_id,
            Fact.status == "pending",
        )
        .values(status="rejected")
    )
    await session.commit()
    return raw_input


async def run_extraction(
    raw_input_id: int, user_id: int, forced_person_id: int | None = None
) -> None:
    """后台任务入口。异常在里面全部吃掉，这里只做转调。

    `user_id` 要跟着走：后台任务自己开 session，RLS 变量得有人告诉它是谁的活。
    `forced_person_id`：归属待定被指定人后重抽，强制归属不再让模型猜。
    """
    await extract.extract_from_raw_input(raw_input_id, user_id, forced_person_id)
