"""提醒路由：历史列表 / 转待办 / 换一个建议。

对应文档：specs/09-reminder.md 5.6
"""

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.todos import TodoOut
from app.core.response import DataResponse, ok
from app.deps import CurrentUser, get_current_user, get_session, require_csrf
from app.services import reminder as reminder_service

router = APIRouter(prefix="/reminders", tags=["reminders"])


class ReminderOut(BaseModel):
    """提醒记录。payload 里是生成好的完整内容（不可变快照）。"""

    id: int
    event_id: int
    person_id: int | None = None
    remind_on: date
    level: str
    channel: str | None = None
    payload: dict[str, Any] | None = None
    status: str
    sent_at: datetime | None = None
    created_at: datetime


@router.get("", response_model=DataResponse[list[ReminderOut]], summary="提醒历史")
async def list_reminders(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 20,
) -> dict[str, Any]:
    """新的在前。"""
    items = await reminder_service.list_reminders(session, limit)
    return ok(
        [
            ReminderOut(
                id=item.id or 0,
                event_id=item.event_id,
                person_id=item.person_id,
                remind_on=item.remind_on,
                level=item.level,
                channel=item.channel,
                payload=item.payload,
                status=item.status,
                sent_at=item.sent_at,
                created_at=item.created_at,
            )
            for item in items
        ]
    )


@router.post(
    "/{reminder_id}/to-todo",
    response_model=DataResponse[TodoOut],
    summary="提醒转待办",
    dependencies=[Depends(require_csrf)],
)
async def to_todo(
    reminder_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """转成待办。重复转返回 409。"""
    todo = await reminder_service.to_todo(session, reminder_id, user.id)
    return ok(
        TodoOut(
            id=todo.id or 0,
            person_id=todo.person_id,
            title=todo.title,
            detail=todo.detail,
            source=todo.source,
            source_id=todo.source_id,
            ai_reason=todo.ai_reason,
            due_date=todo.due_date,
            status=todo.status,
            done_at=todo.done_at,
            created_at=todo.created_at,
        )
    )


@router.post(
    "/{reminder_id}/regenerate",
    response_model=DataResponse[ReminderOut],
    summary="换一个建议",
    dependencies=[Depends(require_csrf)],
)
async def regenerate(
    reminder_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """重新生成建议并覆盖 payload。"""
    item = await reminder_service.regenerate(session, reminder_id)
    return ok(
        ReminderOut(
            id=item.id or 0,
            event_id=item.event_id,
            person_id=item.person_id,
            remind_on=item.remind_on,
            level=item.level,
            channel=item.channel,
            payload=item.payload,
            status=item.status,
            sent_at=item.sent_at,
            created_at=item.created_at,
        )
    )
