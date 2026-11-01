"""对话：意图判定 + 查询回答 + 投喂回话。

覆盖 `docs/07-后端设计.md` 5.1「对话」那三条硬规则：
  · 只做投喂和查询，不猜别的意图
  · 查询复用已确认的事实，且带依据
  · 判不出是谁就如实说，不编

需要真实 Postgres。投喂路径用 monkeypatch 替掉模型，不花 API 额度。
"""

import pytest
from sqlalchemy import select

from app.models.fact import Fact
from app.services import chat as chat_service
from app.services.person import create_person
from app.services.vision import ModelReply, vision
from tests.conftest import Account, rls_session


@pytest.mark.parametrize(
    ("text", "has_image", "person_id", "expected"),
    [
        # 带图一定是投喂，哪怕文字像个问句
        ("她喜欢什么", True, None, "feed"),
        # 问句特征
        ("孟雅楠喜欢什么", False, None, "query"),
        ("她生日几号？", False, None, "query"),
        ("这人是谁", False, None, "query"),
        # 陈述句 + 指定了人 = 投喂
        ("她只喝手冲", False, 7, "feed"),
        # 陈述句 + 没指人，也走投喂（归属交给模型判）
        ("她只喝手冲", False, None, "feed"),
    ],
)
def test_detect_intent(text: str, has_image: bool, person_id: int | None, expected: str) -> None:
    assert chat_service.detect_intent(
        text=text, has_image=has_image, person_id=person_id
    ) == expected


async def _person_with_fact(user: Account) -> int:
    async with rls_session(user.id) as session:
        summary = await create_person(session, user.id, nickname="孟雅楠", roles=["ge_jie"])
        person_id = summary.person.id
    assert person_id is not None

    async with rls_session(user.id) as session:
        session.add(
            Fact(
                user_id=user.id,
                person_id=person_id,
                content="最近开始特别爱喝苹果汁",
                category="preference",
                confidence=0.9,
                source_type="screenshot",
                evidence="我现在好喜欢喝",
                status="confirmed",
                weight=0.9,
            )
        )
        await session.commit()
    return person_id


async def test_query_answers_with_facts_and_evidence(single_user: Account) -> None:
    person_id = await _person_with_fact(single_user)

    async with rls_session(single_user.id) as session:
        result = await chat_service.answer_query(session, text="孟雅楠喜欢什么")

    assert result.intent == "query"
    assert result.person_id == person_id
    assert "苹果汁" in result.reply
    # 业务原则 2：建议必须带依据
    assert "依据" in result.reply
    assert result.facts and result.facts[0]["evidence"] == "我现在好喜欢喝"


async def test_query_ignores_unconfirmed_facts(single_user: Account) -> None:
    """没点过确认的事实不能拿来回答——那是"猜"。"""
    async with rls_session(single_user.id) as session:
        summary = await create_person(
            session, single_user.id, nickname="小包老师", roles=["customer"]
        )
        person_id = summary.person.id
    assert person_id is not None

    async with rls_session(single_user.id) as session:
        session.add(
            Fact(
                user_id=single_user.id,
                person_id=person_id,
                content="可能在减肥",
                category="situation",
                confidence=0.5,
                source_type="screenshot",
                status="pending",
            )
        )
        await session.commit()

    async with rls_session(single_user.id) as session:
        result = await chat_service.answer_query(session, text="小包老师最近怎么样")

    assert result.person_id == person_id
    assert result.facts == []
    assert "没有确认过的事实" in result.reply


async def test_query_without_known_person_asks_who(single_user: Account) -> None:
    async with rls_session(single_user.id) as session:
        result = await chat_service.answer_query(session, text="张三喜欢什么")

    assert result.intent == "clarify"
    assert result.person_id is None
    assert "问谁" in result.reply


async def test_feed_text_returns_extracted_facts(single_user: Account, monkeypatch) -> None:
    person_id = await _person_with_fact(single_user)

    parsed = {
        "person_id": person_id,
        "is_chat": True,
        "segments": [],
        "facts": [
            {
                "content": "下周要去杭州出差",
                "category": "situation",
                "confidence": 0.9,
                "evidence": "我下周三去杭州",
            }
        ],
        "events": [],
        "ledgers": [],
    }

    async def _fake_extract(**kwargs):
        return ModelReply(parsed=parsed, raw="{}", provider="test", model="test")

    monkeypatch.setattr(vision, "extract", _fake_extract)

    async with rls_session(single_user.id) as session:
        result = await chat_service.reply_to(
            session, single_user.id, text="孟雅楠说她下周三去杭州", person_id=person_id
        )

    assert result.intent == "feed"
    assert result.person_id == person_id
    assert result.pending_count == 1
    assert "杭州" in result.reply
    # 投喂是同步抽取，返回时材料必须已经抽完，不是 202 排队
    assert result.raw_input_id is not None

    async with rls_session(single_user.id) as session:
        facts = list(
            (
                await session.execute(select(Fact).where(Fact.source_id == result.raw_input_id))
            )
            .scalars()
            .all()
        )
        assert len(facts) == 1
        assert facts[0].person_id == person_id


async def test_feed_without_identity_does_not_invent(single_user: Account, monkeypatch) -> None:
    """模型判不出归属 → 事实一条不落，回复如实说。这对应业务原则 3。"""
    parsed = {
        "person_id": None,
        "is_chat": True,
        "segments": [],
        "facts": [
            {"content": "喜欢喝美式", "category": "preference", "confidence": 0.8, "evidence": "x"}
        ],
        "events": [],
        "ledgers": [],
    }

    async def _fake_extract(**kwargs):
        return ModelReply(parsed=parsed, raw="{}", provider="test", model="test")

    monkeypatch.setattr(vision, "extract", _fake_extract)

    async with rls_session(single_user.id) as session:
        result = await chat_service.reply_to(session, single_user.id, text="她喜欢喝美式")

    assert result.intent == "feed"
    assert result.person_id is None
    assert "没认出" in result.reply
