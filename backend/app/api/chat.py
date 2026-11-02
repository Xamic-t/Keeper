"""对话路由：一条消息进来，一句话回答出去。

对应文档：docs/07-后端设计.md 5.1「对话」

为什么要 multipart 而不是 JSON：
对话要能"边说边甩一张截图"，图只能走 multipart，所以两个字段统一在表单里。
代价是纯文字请求也要用 Form，前端多一层 FormData —— 比开两个接口省事。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.response import DataResponse, ok
from app.deps import CurrentUser, get_current_user, get_session, require_csrf
from app.services import chat as chat_service
from app.services import ingest as ingest_service

router = APIRouter(prefix="/chat", tags=["chat"])


class FactRef(BaseModel):
    id: int | None = None
    content: str
    evidence: str | None = None


class ChatReplyOut(BaseModel):
    reply: str
    intent: str
    person_id: int | None = None
    nickname: str | None = None
    facts: list[FactRef] = []
    raw_input_id: int | None = None
    pending_count: int = 0


@router.post(
    "",
    response_model=DataResponse[ChatReplyOut],
    summary="对话（投喂 / 查询）",
    dependencies=[Depends(require_csrf)],
)
async def chat(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    message: Annotated[str, Form(description="用户说的话，有图时可空")] = "",
    person_id: Annotated[int | None, Form(description="界面上锁定了某个人时传")] = None,
    image: Annotated[UploadFile | None, File(description="jpg / png / webp，≤8MB")] = None,
) -> dict[str, Any]:
    """判意图 → 投喂走 ingest+抽取，查询走 facts/tags/ledger。"""
    text = chat_service.validate_message(message, has_image=image is not None)
    upload = None
    if image is not None:
        limit = settings.max_upload_mb * 1024 * 1024
        upload = ingest_service.build_upload_item(image.filename, await image.read(limit + 1))

    result = await chat_service.reply_to(
        session, user.id, text=text, person_id=person_id, image=upload
    )
    return ok(
        ChatReplyOut(
            reply=result.reply,
            intent=result.intent,
            person_id=result.person_id,
            nickname=result.nickname,
            facts=[FactRef(**item) for item in result.facts],
            raw_input_id=result.raw_input_id,
            pending_count=result.pending_count,
        )
    )
