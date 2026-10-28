"""日历事件：查询时展开重复规则与农历生日。

对应文档：specs/08-events.md

要点：
  · 库里一条 `rrule` 事件可能在范围内出现多次，展开在**读**的时候做
  · 农历生日每年公历日期不同，只能逐年算（见 festival.py）
  · 节日是公共的，不属于任何用户，直接合并进结果
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from dateutil.rrule import rrulestr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.event import Event
from app.models.person import Person
from app.models.relationship import Relationship
from app.services import festival as festival_service

logger = logging.getLogger(__name__)

EVENT_TYPES = {"birthday", "festival", "payment_due", "custom"}
# 单次查询最多展开多少条，防一个 frec=DAILY 的事件把响应撑爆
MAX_ITEMS = 500


@dataclass(frozen=True, slots=True)
class CalendarItem:
    """日历上的一格。前端按 date 分组渲染。"""

    day: date
    title: str
    type: str
    person_id: int | None = None
    event_id: int | None = None
    lunar: bool = False


def _expand_rrule(event: Event, start: date, end: date) -> list[date]:
    """把 rrule 展开到 [start, end]。规则非法就当没有，不让整页挂掉。"""
    if not event.rrule:
        return [event.occurs_on]

    try:
        rule = rrulestr(event.rrule, dtstart=datetime.combine(event.occurs_on, datetime.min.time()))
        days = [
            item.date()
            for item in rule.between(
                datetime.combine(start, datetime.min.time()),
                datetime.combine(end, datetime.max.time()),
                inc=True,
            )
        ]
    except (ValueError, TypeError) as exc:
        logger.warning("bad rrule event_id=%s kind=%s", event.id, type(exc).__name__)
        return []

    return days[:MAX_ITEMS]


async def expand_events(session: AsyncSession, start: date, end: date) -> list[CalendarItem]:
    """范围内的事件 + 农历生日 + 节日，按日期排序。"""
    stmt = select(Event).where((Event.occurs_on.between(start, end)) | (Event.rrule.is_not(None)))
    events = list((await session.execute(stmt)).scalars().all())

    items: list[CalendarItem] = []
    for event in events:
        if event.type == "birthday" and event.lunar:
            items.extend(_expand_lunar_birthday(event, start, end))
            continue

        for day in _expand_rrule(event, start, end):
            if start <= day <= end:
                items.append(
                    CalendarItem(
                        day=day,
                        title=event.title,
                        type=event.type,
                        person_id=event.person_id,
                        event_id=event.id,
                        lunar=bool(event.lunar),
                    )
                )

    # 只查一次关系表，批量判断谁家生日是农历补充进私人日历
    items.extend(await _relationship_birthdays(session, start, end))

    for item in festival_service.get_festivals(start.year):
        if start <= item.day <= end:
            items.append(CalendarItem(day=item.day, title=item.name, type="festival"))
    if end.year != start.year:
        for item in festival_service.get_festivals(end.year):
            if start <= item.day <= end:
                items.append(CalendarItem(day=item.day, title=item.name, type="festival"))

    items.sort(key=lambda entry: (entry.day, entry.type, entry.title))
    return items[:MAX_ITEMS]


def _expand_lunar_birthday(event: Event, start: date, end: date) -> list[CalendarItem]:
    """农历生日：范围内的每一年都换算一次。"""
    lunar = event.lunar or {}
    month = int(lunar.get("month") or 0)
    day = int(lunar.get("day") or 0)
    is_leap = bool(lunar.get("is_leap", False))
    if not month or not day:
        return []

    items: list[CalendarItem] = []
    for year in range(start.year, end.year + 1):
        converted = festival_service.lunar_to_solar(month, day, is_leap, year)
        if converted is not None and start <= converted <= end:
            items.append(
                CalendarItem(
                    day=converted,
                    title=event.title,
                    type="birthday",
                    person_id=event.person_id,
                    event_id=event.id,
                    lunar=True,
                )
            )
    return items


async def _relationship_birthdays(
    session: AsyncSession, start: date, end: date
) -> list[CalendarItem]:
    """关系里存了农历生日、但用户没手动建事件时，也要出现在日历上。"""
    stmt = (
        select(Relationship, Person.nickname)
        .join(Person, Person.id == Relationship.person_id)
        .where(Relationship.birthday_lunar.is_not(None))
    )
    rows = (await session.execute(stmt)).all()

    items: list[CalendarItem] = []
    for relationship, nickname in rows:
        lunar = relationship.birthday_lunar or {}
        month = int(lunar.get("month") or 0)
        day = int(lunar.get("day") or 0)
        if not month or not day:
            continue
        for year in range(start.year, end.year + 1):
            converted = festival_service.lunar_to_solar(
                month, day, bool(lunar.get("is_leap", False)), year
            )
            if converted is not None and start <= converted <= end:
                items.append(
                    CalendarItem(
                        day=converted,
                        title=f"{nickname}生日（农历）",
                        type="birthday",
                        person_id=relationship.person_id,
                        lunar=True,
                    )
                )
    return items


async def list_events(session: AsyncSession, start: date, end: date) -> list[CalendarItem]:
    """日历查询。空范围返回空列表（200），不是 404。"""
    if end < start:
        raise AppError(ErrorCode.VALIDATION_ERROR, "结束日期不能早于开始日期", status_code=400)
    if (end - start).days > 366:
        raise AppError(ErrorCode.VALIDATION_ERROR, "一次最多查一年", status_code=400)
    return await expand_events(session, start, end)


async def create_event(
    session: AsyncSession,
    user_id: int,
    *,
    type: str,
    title: str,
    occurs_on: date,
    person_id: int | None = None,
    rrule: str | None = None,
    lunar: dict[str, Any] | None = None,
) -> Event:
    """新增事件。生日必须挂人，节日不能挂人。"""
    if type not in EVENT_TYPES:
        raise AppError(ErrorCode.VALIDATION_ERROR, "事件类型不合法", status_code=400)
    if type == "birthday" and person_id is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "生日必须指定是谁", status_code=400)
    if type == "festival" and person_id is not None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "节日不能挂在人身上", status_code=400)
    if person_id is not None and await session.get(Person, person_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个人", status_code=404)

    event = Event(
        user_id=user_id,
        person_id=person_id,
        type=type,
        title=title,
        occurs_on=occurs_on,
        rrule=rrule,
        lunar=lunar,
        source_type="manual",
    )
    session.add(event)

    # 农历生日顺手回写关系表，档案页和日历用同一份数据
    if type == "birthday" and person_id is not None and lunar:
        relationship = (
            await session.execute(select(Relationship).where(Relationship.person_id == person_id))
        ).scalar_one_or_none()
        if relationship is not None:
            relationship.birthday_lunar = lunar
            relationship.birthday_type = "lunar"
            session.add(relationship)

    await session.commit()
    return event


async def update_event(session: AsyncSession, event_id: int, changes: dict[str, Any]) -> Event:
    """改事件。只改传了的字段。"""
    event = await session.get(Event, event_id)
    if event is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个事件", status_code=404)

    allowed = {"type", "title", "occurs_on", "rrule", "lunar", "person_id", "is_done"}
    for key, value in changes.items():
        if key in allowed:
            setattr(event, key, value)
    if event.type == "festival" and event.person_id is not None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "节日不能挂在人身上", status_code=400)
    if event.type == "birthday" and event.person_id is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "生日必须指定是谁", status_code=400)

    session.add(event)
    await session.commit()
    return event


async def delete_event(session: AsyncSession, event_id: int) -> None:
    """删事件。提醒靠外键级联一起删。"""
    event = await session.get(Event, event_id)
    if event is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个事件", status_code=404)
    await session.delete(event)
    await session.commit()


def month_range(anchor: date) -> tuple[date, date]:
    """给前端的默认范围：anchor 所在月的整月。"""
    first = anchor.replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return first, next_month - timedelta(days=1)
