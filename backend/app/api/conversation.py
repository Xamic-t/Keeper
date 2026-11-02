"""助理对话路由：读历史 / 发一条 / 清空。

对应文档：`docs/07` 5.1「助理对话」、`docs/32-助理整合计划.md`

和 `/api/chat` 的区别（两个都保留，别混）：
  · `/api/chat`         —— 投喂对话框，规则路由，两个意图，不调大模型
  · `/api/conversation` —— 助理，多轮模型对话，能执行动作
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.response import DataResponse, ok
from app.deps import CurrentUser, get_current_user, get_session, require_csrf
from app.services import conversation as conversation_service
from app.services import ingest as ingest_service

router = APIRouter(prefix="/conversation", tags=["conversation"])


async def _read_upload(image: UploadFile | None) -> ingest_service.UploadItem | None:
    """读图 + 走投喂那套大小/格式校验。没传图返回 None。"""
    if image is None:
        return None
    limit = settings.max_upload_mb * 1024 * 1024
    return ingest_service.build_upload_item(image.filename, await image.read(limit + 1))


class MessageOut(BaseModel):
    id: int | None
    role: str
    content: str
    actions: list[dict[str, Any]] = []
    image_raw_input_id: int | None = None
    created_at: datetime


class ActionResultOut(BaseModel):
    type: str
    ok: bool
    summary: str
    ref_id: int | None = None
    reason: str | None = None


class TurnOut(BaseModel):
    reply: str
    actions: list[ActionResultOut] = []
    user_message_id: int | None = None
    assistant_message_id: int | None = None
    raw_input_id: int | None = None


class ClearOut(BaseModel):
    deleted: int = Field(description="删掉的消息条数")


@router.get(
    "/messages",
    response_model=DataResponse[list[MessageOut]],
    summary="对话历史（最近 N 条，正序）",
)
async def list_messages(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """刷新页面/换设备后接着聊。RLS 保证只拿到自己的。"""
    rows = await conversation_service.load_history(session, limit=limit)
    return ok([MessageOut(**item) for item in rows])


@router.post(
    "/messages",
    response_model=DataResponse[TurnOut],
    summary="发一条给助理（可附图）",
    dependencies=[Depends(require_csrf)],
)
async def send_message(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    text: Annotated[str, Form(description="用户说的话，有图时可空")] = "",
    image: Annotated[UploadFile | None, File(description="jpg / png / webp，≤8MB")] = None,
    reuse_message_id: Annotated[int | None, Form(description="重试时复用这条消息")] = None,
) -> dict[str, Any]:
    """同步等模型回。失败时 error.detail.user_message_id 可用于原消息重试。"""
    upload = await _read_upload(image)

    result = await conversation_service.run_turn(
        session,
        user.id,
        text=text,
        image=upload,
        reuse_user_message_id=reuse_message_id,
    )
    return ok(
        TurnOut(
            reply=result.reply,
            actions=[ActionResultOut(**item) for item in result.actions],
            user_message_id=result.user_message_id,
            assistant_message_id=result.assistant_message_id,
            raw_input_id=result.raw_input_id,
        )
    )


@router.delete(
    "/messages",
    response_model=DataResponse[ClearOut],
    summary="清空对话历史",
    dependencies=[Depends(require_csrf)],
)
async def clear_messages(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """只删自己的（RLS 保证）。不删已建出来的待办/事件。"""
    deleted = await conversation_service.clear_history(session)
    return ok(ClearOut(deleted=deleted))
