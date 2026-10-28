"""今日页：一次请求拿全"今天该做什么"。

对应文档：specs/11-today.md

这是一个纯聚合板块：不产生数据，只把别处算好的拼起来。
两条规则：
  ① 一次请求拿全（手机上发 5 个请求拼页面太慢）
  ② 最多 3 条（20 人规模，一屏塞 10 条就是制造焦虑），超出的折成 tasks_more
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event, Reminder
from app.models.fact import Fact
from app.models.ledger import Ledger
from app.models.person import Person
from app.models.todo import Todo
from app.services import todo as todo_service

logger = logging.getLogger(__name__)

TASK_LIMIT = 3
RECENT_LIMIT = 3
TODO_WINDOW_DAYS = 3

# 提醒档位权重：T-0 最急
LEVEL_PRIORITY = {"d0": 0, "d2": 1, "d3": 2, "d7": 3}


@dataclass(slots=True)
class TaskItem:
    """一条待办事项。提醒和待办合成同一个结构给前端。"""

    type: str  # reminder | todo
    title: str
    subtitle: str = ""
    person_id: int | None = None
    source_id: int | None = None
    due: date | None = None
    priority: int = 5
    amount: str | None = None


@dataclass(slots=True)
class TodayView:
    date: date
    tasks: list[TaskItem] = field(default_factory=list)
    tasks_more: int = 0
    pending_count: int = 0
    pending_preview: dict[str, Any] | None = None
    recent: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False


async def collect_tasks(session: AsyncSession, today: date) -> list[TaskItem]:
    """把提醒和待办合成统一的任务结构。"""
    tasks: list[TaskItem] = []

    reminder_rows = (
        await session.execute(
            select(Reminder, Event.title, Person.nickname)
            .join(Event, Event.id == Reminder.event_id)
            .outerjoin(Person, Person.id == Reminder.person_id)
            .where(
                Reminder.status == "sent",
                Reminder.remind_on >= today - timedelta(days=1),
                Reminder.remind_on <= today + timedelta(days=TODO_WINDOW_DAYS),
            )
            .order_by(Reminder.remind_on)
        )
    ).all()

    for reminder, event_title, _nickname in reminder_rows:
        payload = reminder.payload or {}
        tasks.append(
            TaskItem(
                type="reminder",
                title=str(payload.get("headline") or event_title),
                subtitle=str(payload.get("suggestion") or event_title),
                person_id=reminder.person_id,
                source_id=reminder.id,
                due=reminder.remind_on,
                priority=LEVEL_PRIORITY.get(reminder.level, 4),
            )
        )

    todo_rows = (
        await session.execute(
            select(Todo, Person.nickname)
            .outerjoin(Person, Person.id == Todo.person_id)
            .where(
                Todo.status == "open",
                (Todo.due_date.is_(None))
                | (Todo.due_date <= today + timedelta(days=TODO_WINDOW_DAYS)),
            )
            .order_by(Todo.due_date.asc().nulls_last(), Todo.created_at.desc())
        )
    ).all()

    for todo, nickname in todo_rows:
        tasks.append(
            TaskItem(
                type="todo",
                title=todo.title,
                subtitle=nickname or (todo.detail or ""),
                person_id=todo.person_id,
                source_id=todo.id,
                due=todo.due_date,
                # 无截止日的排最后
                priority=6 if todo.due_date is None else 5,
            )
        )

    return tasks


def rank_tasks(tasks: list[TaskItem], limit: int = TASK_LIMIT) -> tuple[list[TaskItem], int]:
    """排序并截断。提醒 > 待办；同档有金额的优先；稳定排序。"""
    ordered = sorted(
        tasks,
        key=lambda item: (item.priority, 0 if item.amount else 1, item.due or date.max),
    )
    return ordered[:limit], max(len(ordered) - limit, 0)


async def _pending_block(session: AsyncSession) -> tuple[int, dict[str, Any] | None]:
    """待确认数量 + 第一条预览。"""
    count = (
        await session.execute(
            select(func.count()).select_from(Fact).where(Fact.status == "pending")
        )
    ).scalar_one()

    if count == 0:
        return 0, None

    preview = (
        await session.execute(
            select(Fact, Person.nickname)
            .join(Person, Person.id == Fact.person_id)
            .where(Fact.status == "pending")
            .order_by(Fact.created_at.desc())
            .limit(1)
        )
    ).first()
    if preview is None:
        return count, None

    fact, nickname = preview
    return count, {"fact_id": fact.id, "nickname": nickname, "content": fact.content}


async def _recent_block(session: AsyncSession) -> list[dict[str, Any]]:
    """最近 3 条动态：事实与往来混排。"""
    facts = (
        await session.execute(
            select(Fact, Person.nickname)
            .join(Person, Person.id == Fact.person_id)
            .where(Fact.status == "confirmed")
            .order_by(Fact.created_at.desc())
            .limit(RECENT_LIMIT)
        )
    ).all()

    items = [
        {
            "kind": "fact",
            "person_id": fact.person_id,
            "nickname": nickname,
            "content": fact.content,
            "at": fact.created_at,
        }
        for fact, nickname in facts
    ]

    if len(items) < RECENT_LIMIT:
        ledgers = (
            await session.execute(
                select(Ledger, Person.nickname)
                .join(Person, Person.id == Ledger.person_id)
                .order_by(Ledger.created_at.desc())
                .limit(RECENT_LIMIT)
            )
        ).all()
        items.extend(
            {
                "kind": "ledger",
                "person_id": ledger.person_id,
                "nickname": nickname,
                "content": f"{'收' if ledger.direction == 'in' else '付'} {ledger.amount} 元",
                "at": ledger.created_at,
            }
            for ledger, nickname in ledgers
        )

    items.sort(key=lambda entry: entry["at"], reverse=True)
    return items[:RECENT_LIMIT]


async def get_today(session: AsyncSession, user_id: int, today: date | None = None) -> TodayView:
    """组装今日页。任何一块失败都不让整页崩，只标 degraded。"""
    reference = today or datetime.now(UTC).date()
    view = TodayView(date=reference)

    try:
        await todo_service.ensure_daily_template(session, user_id, reference)
    except Exception as exc:  # noqa: BLE001 — 模板任务失败不该挡住整页
        logger.warning("daily template failed kind=%s", type(exc).__name__)
        view.degraded = True

    try:
        tasks = await collect_tasks(session, reference)
        view.tasks, view.tasks_more = rank_tasks(tasks)
    except Exception as exc:  # noqa: BLE001
        logger.warning("collect tasks failed kind=%s", type(exc).__name__)
        view.degraded = True

    try:
        view.pending_count, view.pending_preview = await _pending_block(session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pending block failed kind=%s", type(exc).__name__)
        view.degraded = True

    try:
        view.recent = await _recent_block(session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("recent block failed kind=%s", type(exc).__name__)
        view.degraded = True

    return view
