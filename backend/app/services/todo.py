"""待办：把"要做的事"记下来，勾掉。

对应文档：specs/10-todos.md

三条规则：
  ① 待办可以不挂人（"回复昨天的私信"不属于任何人）
  ② 删人不删任务——todos.person_id 是唯一用 SET NULL 的外键
  ③ 待办不设确认队列：错了删掉就行（宪法原则三的例外）
"""

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.person import Person
from app.models.todo import Todo

logger = logging.getLogger(__name__)

STATUSES = {"open", "done", "dropped"}

# 每日固定模板。V2 做成用户可编辑，V1 先写死。
DAILY_TEMPLATE: tuple[tuple[str, str], ...] = (
    ("复盘今日数据", "看一遍今天的互动量、涨粉、成交"),
    ("回复私信", "把还没回的私信清一遍，别过夜"),
    ("更新哥姐动态", "有没有该记的新情况，随手投喂"),
)


async def list_todos(
    session: AsyncSession,
    *,
    status: str | None = "open",
    due_before: date | None = None,
    person_id: int | None = None,
) -> list[Todo]:
    """查待办。空结果返回空列表（200），不是 404。"""
    stmt = select(Todo)
    if status and status != "all":
        stmt = stmt.where(Todo.status == status)
    if due_before is not None:
        stmt = stmt.where(Todo.due_date.is_not(None), Todo.due_date <= due_before)
    if person_id is not None:
        stmt = stmt.where(Todo.person_id == person_id)

    # 没有截止日的排最后
    stmt = stmt.order_by(Todo.due_date.asc().nulls_last(), Todo.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())


async def create_todo(
    session: AsyncSession,
    user_id: int,
    *,
    title: str,
    detail: str | None = None,
    due_date: date | None = None,
    person_id: int | None = None,
    source: str = "manual",
    source_id: int | None = None,
    ai_reason: str | None = None,
) -> Todo:
    """新增一条待办。"""
    if source not in {"template", "ai", "reminder", "manual"}:
        raise AppError(ErrorCode.VALIDATION_ERROR, "待办来源不合法", status_code=400)
    if person_id is not None and await session.get(Person, person_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个人", status_code=404)

    todo = Todo(
        user_id=user_id,
        person_id=person_id,
        title=title.strip(),
        detail=detail,
        source=source,
        source_id=source_id,
        ai_reason=ai_reason,
        due_date=due_date,
        status="open",
    )
    session.add(todo)
    await session.commit()
    return todo


async def update_todo(session: AsyncSession, todo_id: int, changes: dict[str, Any]) -> Todo:
    """改内容或勾选。done 与 open 之间可以来回改（点错了要能改回来）。"""
    todo = await session.get(Todo, todo_id)
    if todo is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这条待办", status_code=404)

    allowed = {"title", "detail", "due_date", "person_id", "status", "ai_reason"}
    for key, value in changes.items():
        if key not in allowed or value is None:
            continue
        if key == "status":
            if value not in STATUSES:
                raise AppError(ErrorCode.VALIDATION_ERROR, "状态不合法", status_code=400)
            todo.status = value
            todo.done_at = datetime.now(UTC) if value == "done" else None
            continue
        if key == "person_id" and await session.get(Person, value) is None:
            raise AppError(ErrorCode.NOT_FOUND, "找不到这个人", status_code=404)
        setattr(todo, key, value)

    session.add(todo)
    await session.commit()
    return todo


async def delete_todo(session: AsyncSession, todo_id: int) -> None:
    """删待办。"""
    todo = await session.get(Todo, todo_id)
    if todo is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这条待办", status_code=404)
    await session.delete(todo)
    await session.commit()


async def ensure_daily_template(session: AsyncSession, user_id: int, today: date) -> int:
    """今天的固定模板任务没生成过就补上。幂等——同一天多次打开今日页只有一份。

    幂等的判据是 (source='template', due_date=今天)，不是 created_at：
    后者带时区，跨时区跑会漏判。
    """
    existing = (
        (
            await session.execute(
                select(Todo).where(
                    Todo.source == "template",
                    Todo.due_date == today,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return 0

    for title, detail in DAILY_TEMPLATE:
        session.add(
            Todo(
                user_id=user_id,
                title=title,
                detail=detail,
                source="template",
                due_date=today,
                status="open",
            )
        )
    await session.commit()
    return len(DAILY_TEMPLATE)
