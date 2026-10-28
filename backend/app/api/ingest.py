"""投喂路由：上传截图 / 投喂一句话 / 查处理状态 / 取原图。

上传立即返回 202，抽取在 BackgroundTasks 里跑（specs/03-ingest.md 第 2 节）。
"""

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.response import DataResponse, ok
from app.deps import CurrentUser, get_current_user, get_session, require_csrf
from app.services import ingest as ingest_service
from app.services.ingest import UploadItem

router = APIRouter(tags=["ingest"])

# 图片头魔数 → 后缀。不信 content-type，只信字节。
_MAGIC = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
)


class IngestItemOut(BaseModel):
    filename: str
    raw_input_id: int | None = None
    status: str
    duplicate: bool = False
    existing_id: int | None = None
    error: str | None = None


class IngestData(BaseModel):
    items: list[IngestItemOut]
    queued: int = Field(description="新排队等待抽取的条数")


class RawInputOut(BaseModel):
    id: int
    kind: str
    status: str
    person_id: int | None = None
    error_msg: str | None = None
    created_at: datetime
    processed_at: datetime | None = None


class TextIngestIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    person_id: int | None = None


def _sniff_suffix(content: bytes) -> str | None:
    """按魔数判断图片类型。识别不出返回 None。"""
    for magic, suffix in _MAGIC:
        if content.startswith(magic):
            return suffix
    if len(content) > 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    return None


@router.post(
    "/ingest/image",
    response_model=DataResponse[IngestData],
    status_code=status.HTTP_202_ACCEPTED,
    summary="上传截图（1-10 张）",
    dependencies=[Depends(require_csrf)],
)
async def upload_images(
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    files: Annotated[list[UploadFile], File(description="jpg / png / webp，单张 ≤8MB")],
    person_id: Annotated[int | None, Form()] = None,
) -> dict[str, Any]:
    """存盘 + 去重 + 排队。返回每张的结果，不等抽取。"""
    if not files:
        raise AppError(ErrorCode.VALIDATION_ERROR, "至少要传一张图", status_code=400)
    if len(files) > settings.max_upload_count:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"一次最多 {settings.max_upload_count} 张",
            status_code=400,
        )

    limit = settings.max_upload_mb * 1024 * 1024
    items: list[UploadItem] = []
    for upload in files:
        content = await upload.read()
        if len(content) > limit:
            raise AppError(
                ErrorCode.FILE_TOO_LARGE,
                f"单张不能超过 {settings.max_upload_mb}MB",
                status_code=413,
                detail={"max_mb": settings.max_upload_mb},
            )
        suffix = _sniff_suffix(content)
        if suffix is None:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "只支持 jpg / png / webp",
                status_code=400,
                detail={"filename": upload.filename},
            )
        items.append(
            UploadItem(filename=upload.filename or "upload", content=content, suffix=suffix)
        )

    results = await ingest_service.ingest_images(session, user.id, items, person_id)

    queued = 0
    for item in results:
        if item.raw_input_id is not None and not item.duplicate and item.error is None:
            background.add_task(ingest_service.run_extraction, item.raw_input_id, user.id)
            queued += 1

    return ok(
        IngestData(
            items=[IngestItemOut(**asdict(item)) for item in results],
            queued=queued,
        )
    )


@router.post(
    "/ingest/text",
    response_model=DataResponse[IngestData],
    status_code=status.HTTP_202_ACCEPTED,
    summary="一句话投喂",
    dependencies=[Depends(require_csrf)],
)
async def upload_text(
    payload: TextIngestIn,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """文本投喂同样排队抽取，立刻返回。"""
    raw_input = await ingest_service.ingest_text(session, user.id, payload.text, payload.person_id)
    background.add_task(ingest_service.run_extraction, raw_input.id, user.id)
    return ok(
        IngestData(
            items=[
                IngestItemOut(
                    filename="text",
                    raw_input_id=raw_input.id,
                    status=raw_input.status,
                )
            ],
            queued=1,
        )
    )


@router.get("/raw-inputs", response_model=DataResponse[list[RawInputOut]], summary="最近投喂")
async def list_raw_inputs(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 20,
) -> dict[str, Any]:
    """投喂页用它显示每条材料的处理状态。"""
    rows = await ingest_service.list_raw_inputs(session, limit)
    return ok(
        [
            RawInputOut(
                id=row.id or 0,
                kind=row.kind,
                status=row.status,
                person_id=row.person_id,
                error_msg=row.error_msg,
                created_at=row.created_at,
                processed_at=row.processed_at,
            )
            for row in rows
        ]
    )


@router.get(
    "/raw-inputs/{raw_input_id}",
    response_model=DataResponse[RawInputOut],
    summary="查处理状态",
)
async def get_raw_input(
    raw_input_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """前端上传后轮询这个接口，看抽取成没成。"""
    raw_input = await ingest_service.get_raw_input(session, raw_input_id)
    return ok(
        RawInputOut(
            id=raw_input.id or 0,
            kind=raw_input.kind,
            status=raw_input.status,
            person_id=raw_input.person_id,
            error_msg=raw_input.error_msg,
            created_at=raw_input.created_at,
            processed_at=raw_input.processed_at,
        )
    )


@router.get("/raw-inputs/{raw_input_id}/file", summary="取原图")
async def get_raw_input_file(
    raw_input_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    """返回原图。用户关掉"保留原图"后文件已删，这里 404。"""
    raw_input = await ingest_service.get_raw_input(session, raw_input_id)
    if not raw_input.file_path or not Path(raw_input.file_path).exists():
        raise AppError(ErrorCode.NOT_FOUND, "原图已删除", status_code=404)
    return FileResponse(raw_input.file_path)
