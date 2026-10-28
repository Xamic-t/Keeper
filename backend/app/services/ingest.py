"""投喂：接收截图 / 一句话，去重落库，排队等抽取。

对应文档：specs/03-ingest.md

三条规则：
  ① 上传**立即返回**，抽取在后台跑（用户下播后要批量传 10 张，不能等）
  ② 同一张图传两次要拦住（sha256 + UNIQUE(user_id, file_hash)）
  ③ 原图可能被删（用户关"保留原图"开关），所以抽取要能从 ocr_text 走
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.input import RawInput
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
            status="pending",
        )
        session.add(raw_input)
        await session.flush()  # 拿 id 才能命名文件

        try:
            raw_input.file_path = storage.save_upload(
                user_id, raw_input.id or 0, item.content, item.suffix
            )
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

    await session.commit()
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


async def run_extraction(raw_input_id: int, user_id: int) -> None:
    """后台任务入口。异常在里面全部吃掉，这里只做转调。

    `user_id` 要跟着走：后台任务自己开 session，RLS 变量得有人告诉它是谁的活。
    """
    await extract.extract_from_raw_input(raw_input_id, user_id)
