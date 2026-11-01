"""重抽幂等：只清待确认的事实，不碰已确认的。

覆盖评审 P2-2（`extract.py:_clear_previous` 没过滤 status）。
这个 bug 在当前调用路径上不会触发（重复图在 ingest 阶段就被 sha256 拦住了），
但一旦加"手动重抽"，用户确认过的条目会被静默删掉——所以用测试把它钉住。
需要真实 Postgres。
"""

from sqlalchemy import select

from app.models.fact import Fact
from app.models.input import ChatSegment, RawInput
from app.services import extract as extract_service
from app.services.person import create_person
from tests.conftest import Account, rls_session


async def _seed(user: Account) -> tuple[int, int]:
    """造一条材料：1 条已确认事实 + 1 条待确认事实 + 1 段对话。"""
    async with rls_session(user.id) as session:
        summary = await create_person(session, user.id, nickname="王姐", roles=["ge_jie"])
        person_id = summary.person.id
        assert person_id is not None

        raw = RawInput(user_id=user.id, kind="screenshot", status="done")
        session.add(raw)
        await session.flush()

        session.add_all(
            [
                Fact(
                    user_id=user.id,
                    person_id=person_id,
                    content="已确认：她只喝手冲",
                    category="preference",
                    confidence=0.9,
                    source_type="screenshot",
                    source_id=raw.id,
                    status="confirmed",
                ),
                Fact(
                    user_id=user.id,
                    person_id=person_id,
                    content="待确认：她可能养猫",
                    category="situation",
                    confidence=0.6,
                    source_type="screenshot",
                    source_id=raw.id,
                    status="pending",
                ),
                ChatSegment(
                    user_id=user.id,
                    raw_input_id=raw.id,
                    person_id=person_id,
                    speaker="me",
                    content="在吗",
                    seq=0,
                ),
            ]
        )
        await session.commit()
        return raw.id, person_id


async def test_clear_previous_keeps_confirmed_facts(single_user: Account) -> None:
    raw_id, _ = await _seed(single_user)

    async with rls_session(single_user.id) as session:
        await extract_service._clear_previous(session, raw_id)
        await session.commit()

    async with rls_session(single_user.id) as session:
        facts = (
            (
                await session.execute(
                    select(Fact).where(Fact.source_id == raw_id).order_by(Fact.id)
                )
            )
            .scalars()
            .all()
        )
        # 已确认的那条必须还在，待确认的那条必须清掉
        assert [fact.status for fact in facts] == ["confirmed"]
        assert facts[0].content == "已确认：她只喝手冲"

        segments = (
            await session.execute(select(ChatSegment).where(ChatSegment.raw_input_id == raw_id))
        ).all()
        # 对话片段没有确认态，整批清掉
        assert segments == []
