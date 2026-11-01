"""每日模板待办：并发打开今日页不能生成两遍。

覆盖评审 P2-4。

**没有按评审建议加 `(user_id, source, due_date)` 唯一索引**，因为那会把
模板一天 3 条压成 1 条。改用事务级 advisory lock，见 services/todo.py 的注释。
需要真实 Postgres（advisory lock 是 PG 的能力）。
"""

import asyncio
from datetime import date

from sqlalchemy import select

from app.models.todo import Todo
from app.services import todo as todo_service
from tests.conftest import Account, rls_session


async def _count_template(user_id: int, day: date) -> int:
    async with rls_session(user_id) as session:
        rows = (
            await session.execute(
                select(Todo).where(Todo.source == "template", Todo.due_date == day)
            )
        ).scalars()
        return len(list(rows.all()))


async def test_concurrent_ensure_creates_one_batch(single_user: Account) -> None:
    day = date(2030, 5, 20)  # 用一个未来的日期，不撞生产数据

    async def ensure() -> int:
        async with rls_session(single_user.id) as session:
            return await todo_service.ensure_daily_template(session, single_user.id, day)

    # 两个请求同时进来，模拟"今日页被并行打开"
    first, second = await asyncio.gather(ensure(), ensure())

    expected = len(todo_service.DAILY_TEMPLATE)
    # 一个负责插入，另一个看到已存在返回 0
    assert first + second == expected
    assert await _count_template(single_user.id, day) == expected


async def test_ensure_is_idempotent_when_called_twice(single_user: Account) -> None:
    day = date(2030, 5, 21)

    async with rls_session(single_user.id) as session:
        created = await todo_service.ensure_daily_template(session, single_user.id, day)
    assert created == len(todo_service.DAILY_TEMPLATE)

    async with rls_session(single_user.id) as session:
        again = await todo_service.ensure_daily_template(session, single_user.id, day)
    assert again == 0
    assert await _count_template(single_user.id, day) == len(todo_service.DAILY_TEMPLATE)
