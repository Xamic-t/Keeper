"""确认队列、画像、抽取解析的 service 单测。

覆盖 specs/05-confirm.md、specs/06-profile.md、specs/04-extract.md 的验收标准。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import AppError, ErrorCode
from app.models.fact import Fact, Tag
from app.services import confirm as confirm_service
from app.services import extract as extract_service
from app.services import profile as profile_service
from app.services.person import create_person
from tests.conftest import Account, rls_session


async def _person(user: Account, nickname: str = "王姐") -> int:
    async with rls_session(user.id) as session:
        summary = await create_person(session, user.id, nickname=nickname, roles=["ge_jie"])
    assert summary.person.id is not None
    return summary.person.id


async def _pending_fact(user: Account, person_id: int, content: str, category: str) -> int:
    async with rls_session(user.id) as session:
        fact = Fact(
            user_id=user.id,
            person_id=person_id,
            content=content,
            category=category,
            confidence=0.9,
            weight=1.0,
            source_type="manual",
            evidence="原文证据",
            status="pending",
        )
        session.add(fact)
        await session.commit()
        assert fact.id is not None
        return fact.id


async def test_confirm_derives_tag(single_user: Account) -> None:
    person_id = await _person(single_user)
    fact_id = await _pending_fact(single_user, person_id, "做建材生意", "structure")

    async with rls_session(single_user.id) as session:
        result = await confirm_service.confirm_fact(session, fact_id, single_user.id)

    assert result.status == "confirmed"
    assert result.cascaded["tags_derived"] == 1

    async with rls_session(single_user.id) as session:
        from sqlalchemy import select

        tags = (
            (await session.execute(select(Tag).where(Tag.person_id == person_id))).scalars().all()
        )
        assert len(tags) == 1
        assert tags[0].key == "industry"
        assert tags[0].value == "建材生意"


async def test_confirm_twice_returns_invalid_state(single_user: Account) -> None:
    person_id = await _person(single_user)
    fact_id = await _pending_fact(single_user, person_id, "喜欢白茶", "preference")

    async with rls_session(single_user.id) as session:
        await confirm_service.confirm_fact(session, fact_id, single_user.id)

    async with rls_session(single_user.id) as session:
        with pytest.raises(AppError) as error:
            await confirm_service.confirm_fact(session, fact_id, single_user.id)

    assert error.value.code is ErrorCode.INVALID_STATE
    assert error.value.status_code == 400


async def test_reject_hides_fact(single_user: Account) -> None:
    person_id = await _person(single_user)
    fact_id = await _pending_fact(single_user, person_id, "今天心情不错", "mood")

    async with rls_session(single_user.id) as session:
        result = await confirm_service.reject_fact(session, fact_id)

    assert result.status == "rejected"

    async with rls_session(single_user.id) as session:
        facts = await profile_service.facts_of(session, person_id)
        assert facts == []


async def test_derive_tags_is_idempotent(single_user: Account) -> None:
    person_id = await _person(single_user)
    fact_id = await _pending_fact(single_user, person_id, "喜欢白茶", "preference")

    async with rls_session(single_user.id) as session:
        await confirm_service.confirm_fact(session, fact_id, single_user.id)
        await profile_service.rebuild_tags(session, person_id, single_user.id)

    async with rls_session(single_user.id) as session:
        from sqlalchemy import select

        tags = (
            (await session.execute(select(Tag).where(Tag.person_id == person_id))).scalars().all()
        )
        assert len(tags) == 1


async def test_batch_confirm_skips_non_pending(single_user: Account) -> None:
    person_id = await _person(single_user)
    first = await _pending_fact(single_user, person_id, "做建材生意", "structure")
    second = await _pending_fact(single_user, person_id, "喜欢白茶", "preference")

    async with rls_session(single_user.id) as session:
        await confirm_service.confirm_fact(session, first, single_user.id)
        result = await confirm_service.batch_confirm(session, [first, second], single_user.id)

    assert result.confirmed == 1
    assert result.skipped == 1
    assert result.skipped_ids == [first]


def test_compute_weight_decays_over_time() -> None:
    confirmed = datetime.now(UTC) - timedelta(days=60)

    # 60 天前确认的 situation（半衰期 60 天）→ 权重 0.5
    assert profile_service.compute_weight(60, confirmed) == 0.5
    # 永久事实不衰减
    assert profile_service.compute_weight(None, confirmed) == 1.0


def test_compute_weight_handles_clock_skew() -> None:
    future = datetime.now(UTC) + timedelta(days=3)

    assert profile_service.compute_weight(60, future) == 1.0


async def test_select_facts_filters_by_weight(single_user: Account) -> None:
    person_id = await _person(single_user)
    fact_id = await _pending_fact(single_user, person_id, "做建材生意", "structure")

    async with rls_session(single_user.id) as session:
        await confirm_service.confirm_fact(session, fact_id, single_user.id)

    # 权重掉到 0.5 以下 → 不该再进综述
    async with rls_session(single_user.id) as session:
        from sqlalchemy import select

        fact = (await session.execute(select(Fact).where(Fact.person_id == person_id))).scalar_one()
        fact.weight = 0.3
        session.add(fact)
        await session.commit()

    async with rls_session(single_user.id) as session:
        facts = await profile_service.select_facts_for_summary(session, person_id)

    assert facts == []


def test_parse_result_drops_items_without_evidence() -> None:
    result = extract_service.parse_result(
        {
            "person_id": 1,
            "segments": [],
            "facts": [
                {"content": "做建材生意", "category": "structure", "confidence": 0.9},
                {
                    "content": "喜欢白茶",
                    "category": "preference",
                    "confidence": 0.9,
                    "evidence": "我喜欢白茶",
                },
            ],
            "events": [],
            "ledgers": [],
        },
        [{"id": 1, "nickname": "王姐", "aliases": []}],
    )

    assert len(result.facts) == 1
    assert result.facts[0].content == "喜欢白茶"


def test_parse_result_drops_unknown_person_id() -> None:
    """模型编了一个不在候选里的人 → 归属置空，等人来定。"""
    result = extract_service.parse_result(
        {"person_id": 999, "facts": [], "events": [], "ledgers": [], "segments": []},
        [{"id": 1, "nickname": "王姐", "aliases": []}],
    )

    assert result.person_id is None


def test_parse_result_keeps_low_confidence_but_marks_it() -> None:
    """低置信度不丢，进确认队列让人看。"""
    result = extract_service.parse_result(
        {
            "person_id": 1,
            "facts": [
                {
                    "content": "好像住在城南",
                    "category": "situation",
                    "confidence": 0.4,
                    "evidence": "我好像在城南",
                }
            ],
            "events": [],
            "ledgers": [],
            "segments": [],
        },
        [{"id": 1, "nickname": "王姐", "aliases": []}],
    )

    assert len(result.facts) == 1
    assert result.facts[0].confidence == 0.4


def test_parse_result_drops_bad_ledger_amount() -> None:
    result = extract_service.parse_result(
        {
            "person_id": 1,
            "facts": [],
            "events": [],
            "segments": [],
            "ledgers": [
                {
                    "direction": "in",
                    "kind": "payment",
                    "amount": -50,
                    "occurred_at": "2026-03-01",
                    "evidence": "转了 50",
                }
            ],
        },
        [{"id": 1, "nickname": "王姐", "aliases": []}],
    )

    assert result.ledgers == []


def test_build_prompt_mentions_candidates() -> None:
    prompt = extract_service.build_prompt(
        [{"id": 1, "nickname": "王姐", "aliases": ["王姐（抖音）"]}], "screenshot"
    )

    assert "王姐" in prompt
    assert "截图" in prompt
