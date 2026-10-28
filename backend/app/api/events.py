"""日历路由：查 / 建 / 改 / 删。

对应文档：specs/08-events.md 5.6
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import DataResponse, ok
from app.deps import CurrentUser, get_current_user, get_session, require_csrf
from app.services import event as event_service

router = APIRouter(prefix="/events", tags=["events"])


class CalendarItemOut(BaseModel):
    day: date
    title: str
    type: str
    person_id: int | None = None
    event_id: int | None = None
    lunar: bool = False


class LunarIn(BaseModel):
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=30)
    is_leap: bool = False


class EventCreateIn(BaseModel):
    type: str = Field(pattern="^(birthday|festival|payment_due|custom)$")
    title: str = Field(min_length=1, max_length=100)
    occurs_on: date
    person_id: int | None = None
    rrule: str | None = Field(default=None, max_length=200)
    lunar: LunarIn | None = None


class EventUpdateIn(BaseModel):
    type: str | None = Field(default=None, pattern="^(birthday|festival|payment_due|custom)$")
    title: str | None = Field(default=None, min_length=1, max_length=100)
    occurs_on: date | None = None
    person_id: int | None = None
    rrule: str | None = Field(default=None, max_length=200)
    lunar: LunarIn | None = None
    is_done: bool | None = None


@router.get("", response_model=DataResponse[list[CalendarItemOut]], summary="日历范围查询")
async def list_events(
    session: Annotated[AsyncSession, Depends(get_session)],
    from_: Annotated[date | None, Query(alias="from", description="开始日期 YYYY-MM-DD")] = None,
    to: Annotated[date | None, Query(description="结束日期 YYYY-MM-DD")] = None,
) -> dict[str, Any]:
    """返回范围内的事件 + 农历生日 + 节日，按日期排序。空范围返回 200 + 空数组。"""
    start = from_ or date.today().replace(day=1)
    end = to or event_service.month_range(start)[1]
    items = await event_service.list_events(session, start, end)
    return ok(
        [
            CalendarItemOut(
                day=item.day,
                title=item.title,
                type=item.type,
                person_id=item.person_id,
                event_id=item.event_id,
                lunar=item.lunar,
            )
            for item in items
        ]
    )


@router.post(
    "",
    response_model=DataResponse[dict],
    status_code=status.HTTP_201_CREATED,
    summary="新增事件",
    dependencies=[Depends(require_csrf)],
)
async def create_event(
    payload: EventCreateIn,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """生日必须挂人，节日不能挂人。农历生日会同步写进关系表。"""
    event = await event_service.create_event(
        session,
        user.id,
        type=payload.type,
        title=payload.title,
        occurs_on=payload.occurs_on,
        person_id=payload.person_id,
        rrule=payload.rrule,
        lunar=payload.lunar.model_dump() if payload.lunar else None,
    )
    return ok(
        {
            "id": event.id,
            "type": event.type,
            "title": event.title,
            "occurs_on": event.occurs_on,
            "person_id": event.person_id,
            "rrule": event.rrule,
            "lunar": event.lunar,
        }
    )


@router.patch(
    "/{event_id}",
    response_model=DataResponse[dict],
    summary="改事件",
    dependencies=[Depends(require_csrf)],
)
async def update_event(
    event_id: int,
    payload: EventUpdateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """只改传了的字段。"""
    changes = payload.model_dump(exclude_unset=True)
    if isinstance(changes.get("lunar"), dict):
        changes["lunar"] = payload.lunar.model_dump() if payload.lunar else None
    event = await event_service.update_event(session, event_id, changes)
    return ok(
        {
            "id": event.id,
            "type": event.type,
            "title": event.title,
            "occurs_on": event.occurs_on,
            "person_id": event.person_id,
            "rrule": event.rrule,
            "lunar": event.lunar,
            "is_done": event.is_done,
        }
    )


@router.delete(
    "/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删事件",
    dependencies=[Depends(require_csrf)],
)
async def delete_event(
    event_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """删事件，相关提醒级联删除。"""
    await event_service.delete_event(session, event_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
