"""今日页路由：一次请求拿全。

对应文档：specs/11-today.md
"""

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import DataResponse, ok
from app.deps import CurrentUser, get_current_user, get_session
from app.services import today as today_service

router = APIRouter(prefix="/today", tags=["today"])


class TaskItemOut(BaseModel):
    type: str
    title: str
    subtitle: str = ""
    person_id: int | None = None
    source_id: int | None = None
    due: date | None = None
    priority: int = 5


class PendingPreviewOut(BaseModel):
    fact_id: int
    nickname: str
    content: str


class RecentOut(BaseModel):
    kind: str
    person_id: int
    nickname: str
    content: str
    at: datetime


class TodayOut(BaseModel):
    date: date
    tasks: list[TaskItemOut]
    tasks_more: int
    pending_count: int
    pending_preview: PendingPreviewOut | None = None
    recent: list[RecentOut]
    degraded: bool = False


@router.get("", response_model=DataResponse[TodayOut], summary="今日页")
async def get_today(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """一次请求返回今日页全部数据：任务、待确认数量、最近动态。"""
    view = await today_service.get_today(session, user.id)
    preview = view.pending_preview
    return ok(
        TodayOut(
            date=view.date,
            tasks=[
                TaskItemOut(
                    type=item.type,
                    title=item.title,
                    subtitle=item.subtitle,
                    person_id=item.person_id,
                    source_id=item.source_id,
                    due=item.due,
                    priority=item.priority,
                )
                for item in view.tasks
            ],
            tasks_more=view.tasks_more,
            pending_count=view.pending_count,
            pending_preview=(PendingPreviewOut(**preview) if preview else None),
            recent=[RecentOut(**item) for item in view.recent],
            degraded=view.degraded,
        )
    )
