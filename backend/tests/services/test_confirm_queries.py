"""确认页的查询次数必须与"待定材料条数"无关。

原来 `list_confirmations` 对每条归属待定的材料单独查一次首句（N+1）：
20 条材料 = 21 次查询。这里用 SQLAlchemy 的游标事件数语句，
把"条数涨了查询数不涨"变成断言——不是靠读代码感觉。

数的是 SELECT 条数，不是耗时：耗时在这台机器上不稳定，条数是确定值。
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app.db import engine
from app.models.fact import Fact
from app.models.input import ChatSegment, RawInput
from app.models.person import Person
from app.services import confirm as confirm_service
from tests.conftest import Account, rls_session


class QueryCounter:
    """统计一段代码里执行了多少条 SELECT。"""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def __call__(self, conn: object, cursor: object, statement: str, *args: object) -> None:
        normalized = statement.lstrip().upper()
        if not normalized.startswith("SELECT"):
            return
        # RLS 变量下发也是一条 SELECT set_config(...)，它不是业务查询，排除掉
        if "SET_CONFIG" in normalized:
            return
        self.statements.append(statement)


@pytest.fixture
def counter() -> QueryCounter:
    counter = QueryCounter()
    event.listen(engine.sync_engine, "before_cursor_execute", counter)
    yield counter
    event.remove(engine.sync_engine, "before_cursor_execute", counter)


async def _seed_unassigned(user: Account, count: int) -> None:
    """造 count 条"归属待定"材料，每条带 2 句对话。"""
    async with rls_session(user.id) as session:
        for index in range(count):
            raw_input = RawInput(
                user_id=user.id,
                kind="screenshot",
                status="done",
                person_id=None,
                created_at=datetime.now(UTC),
            )
            session.add(raw_input)
            await session.flush()
            for seq in range(2):
                session.add(
                    ChatSegment(
                        user_id=user.id,
                        raw_input_id=raw_input.id or 0,
                        speaker="other",
                        content=f"第 {index} 条材料的第 {seq} 句",
                        seq=seq,
                    )
                )
        await session.commit()


async def _seed_pending_fact(user: Account, nickname: str, content: str) -> None:
    async with rls_session(user.id) as session:
        person = Person(user_id=user.id, nickname=nickname)
        session.add(person)
        await session.flush()
        session.add(
            Fact(
                user_id=user.id,
                person_id=person.id,
                content=content,
                category="preference",
                confidence=0.6,
                source_type="manual",
                status="pending",
            )
        )
        await session.commit()


async def _count_queries(user: Account) -> int:
    counter = QueryCounter()
    event.listen(engine.sync_engine, "before_cursor_execute", counter)
    try:
        async with rls_session(user.id) as session:
            await confirm_service.list_confirmations(session)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", counter)
    return len(counter.statements)


async def test_query_count_is_constant_in_pending_raw_inputs(single_user: Account) -> None:
    """待定材料从 2 条涨到 8 条，查询条数不能跟着涨。"""
    await _seed_unassigned(single_user, 2)
    small = await _count_queries(single_user)

    await _seed_unassigned(single_user, 6)
    large = await _count_queries(single_user)

    assert large == small, f"待定材料增加后查询数从 {small} 涨到 {large}，N+1 又回来了"
    # 两次查询就够：一次 pending facts，一次首句批量
    assert large <= 3, f"预期 ≤3 条 SELECT，实际 {large} 条"


async def test_first_segment_is_still_the_earliest(single_user: Account) -> None:
    """消 N+1 不能顺手改语义：仍要取 seq 最小那句。"""
    await _seed_unassigned(single_user, 1)

    async with rls_session(single_user.id) as session:
        groups = await confirm_service.list_confirmations(session)

    unassigned = [group for group in groups if group.person_id is None]
    assert unassigned, "归属待定组丢了"
    assert unassigned[0].items[0].content == "第 0 条材料的第 0 句"


async def test_material_without_segments_still_listed(single_user: Account) -> None:
    """没拆出对话的材料也要出现在列表里（占位文案），不能因为是空就消失。"""
    async with rls_session(single_user.id) as session:
        session.add(RawInput(user_id=single_user.id, kind="screenshot", status="done"))
        await session.commit()

    async with rls_session(single_user.id) as session:
        groups = await confirm_service.list_confirmations(session)

    unassigned = [group for group in groups if group.person_id is None]
    assert unassigned
    assert unassigned[0].items[0].content == "（这条材料没识别出对话）"


async def test_pending_facts_and_unassigned_coexist(single_user: Account) -> None:
    """两组同时在：待定组永远第一，事实组按人归好。"""
    await _seed_pending_fact(single_user, "王姐", "喜欢白茶")
    await _seed_unassigned(single_user, 1)

    async with rls_session(single_user.id) as session:
        groups = await confirm_service.list_confirmations(session)

    assert groups[0].person_id is None
    assert groups[0].nickname == "归属待定"
    assert groups[1].nickname == "王姐"
