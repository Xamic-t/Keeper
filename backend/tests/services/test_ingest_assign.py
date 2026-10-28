"""归属待定材料指定人：改材料 + 改对话片段归属。

覆盖评审 P1-2 的后端部分（`POST /api/raw-inputs/{id}/assign` 的 service 逻辑）。
需要真实 Postgres。
"""

import pytest
from sqlalchemy import select

from app.core.errors import AppError, ErrorCode
from app.models.fact import Fact
from app.models.input import ChatSegment, RawInput
from app.services import extract as extract_service
from app.services import ingest as ingest_service
from app.services.person import create_person
from app.services.vision import ModelReply, vision
from tests.conftest import Account, rls_session


async def _person(user: Account, nickname: str = "王姐") -> int:
    async with rls_session(user.id) as session:
        summary = await create_person(session, user.id, nickname=nickname, roles=["ge_jie"])
    assert summary.person.id is not None
    return summary.person.id


async def test_assign_person_sets_raw_input_and_segments(single_user: Account) -> None:
    person_id = await _person(single_user)

    async with rls_session(single_user.id) as session:
        raw = RawInput(user_id=single_user.id, kind="screenshot", status="done")
        session.add(raw)
        await session.flush()
        session.add(
            ChatSegment(
                user_id=single_user.id,
                raw_input_id=raw.id,
                person_id=None,
                speaker="me",
                content="在吗",
                seq=0,
            )
        )
        await session.commit()
        raw_id = raw.id

    async with rls_session(single_user.id) as session:
        await ingest_service.assign_person(session, raw_id, single_user.id, person_id)

    async with rls_session(single_user.id) as session:
        raw = await session.get(RawInput, raw_id)
        segment = (
            await session.execute(select(ChatSegment).where(ChatSegment.raw_input_id == raw_id))
        ).scalar_one()
        assert raw.person_id == person_id
        assert segment.person_id == person_id


async def test_assign_person_unknown_person_returns_404(single_user: Account) -> None:
    async with rls_session(single_user.id) as session:
        raw = RawInput(user_id=single_user.id, kind="text", status="done", raw_text="x")
        session.add(raw)
        await session.commit()
        raw_id = raw.id

    async with rls_session(single_user.id) as session:
        with pytest.raises(AppError) as error:
            await ingest_service.assign_person(session, raw_id, single_user.id, 999_999_999)

    assert error.value.code is ErrorCode.NOT_FOUND


async def test_extract_forced_person_overrides_model_guess(
    single_user: Account, monkeypatch
) -> None:
    """模型还是判不出是谁时，强制归属也要生效，事实落到指定的人头上。"""
    person_id = await _person(single_user)

    async with rls_session(single_user.id) as session:
        raw = RawInput(user_id=single_user.id, kind="text", status="done", raw_text="王姐喜欢喝茶")
        session.add(raw)
        await session.commit()
        raw_id = raw.id

    parsed = {
        "person_id": None,  # 模型没判出来
        "is_chat": True,
        "segments": [],
        "facts": [
            {
                "content": "喜欢喝茶",
                "category": "preference",
                "confidence": 0.9,
                "evidence": "他说最近都在喝单丛",
            }
        ],
        "events": [],
        "ledgers": [],
    }

    async def _fake_extract(**kwargs):
        return ModelReply(parsed=parsed, raw="{}", provider="test", model="test")

    monkeypatch.setattr(vision, "extract", _fake_extract)

    await extract_service.extract_from_raw_input(raw_id, single_user.id, forced_person_id=person_id)

    async with rls_session(single_user.id) as session:
        raw = await session.get(RawInput, raw_id)
        facts = list(
            (await session.execute(select(Fact).where(Fact.source_id == raw_id))).scalars().all()
        )
        assert raw.person_id == person_id
        assert len(facts) == 1
        assert facts[0].person_id == person_id
