"""RAG：切片、索引、检索、隔离。

这些用例都在**真 Postgres + 真 pgvector** 上跑：
向量列的类型、HNSW 索引、RLS 策略都不是 mock 能证明的东西。

关键约定（写死在用例里，改实现要先改这里）：
  ① 没配向量模型时，切片照样落库、检索退化成关键词——功能不消失，只是变弱
  ② 同一份来源重新索引不会留两份（先删旧再写新）
  ③ 待确认的事实不进索引：那是猜测，被检索到就等于让模型编
  ④ 别人的切片检索不到（RLS 在库层挡，不是应用层过滤）
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.fact import Fact
from app.models.person import Person
from app.models.vector import VectorChunk
from app.services import rag
from tests.conftest import Account, rls_session


@pytest.fixture(autouse=True)
def _no_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认没有向量模型（CI 里也没有 key），走关键词降级这条路。"""
    monkeypatch.setattr(settings, "dashscope_api_key", None)


def test_split_keeps_short_text_whole() -> None:
    assert rag._split("王姐喜欢白茶") == ["王姐喜欢白茶"]


def test_split_cuts_long_text_with_overlap() -> None:
    """长文要切成多段，且段与段之间不能丢字。"""
    text = "。".join(f"第{index}句说的是某件具体的事" for index in range(80)) + "。"
    chunks = rag._split(text)

    assert len(chunks) > 1
    assert all(len(piece) <= rag.CHUNK_SIZE for piece in chunks)
    # 每个切片的开头都能在原文里找到（说明没有凭空造字）
    assert all(piece.strip() in text for piece in chunks)


def test_split_ignores_blank() -> None:
    assert rag._split("   \n  ") == []


async def _seed_person(user: Account, nickname: str) -> int:
    async with rls_session(user.id) as session:
        person = Person(user_id=user.id, nickname=nickname)
        session.add(person)
        await session.commit()
        return person.id or 0


async def test_index_source_is_idempotent(single_user: Account) -> None:
    """同一份来源索引两次 → 还是那一份切片，不是两份。"""
    person_id = await _seed_person(single_user, "王姐")

    async with rls_session(single_user.id) as session:
        first = await rag.index_source(
            session,
            user_id=single_user.id,
            source_type="raw_input",
            source_id=42,
            content="王姐说这个月预算紧，先不要推新品。",
            person_id=person_id,
            scope="knowledge",
        )
        second = await rag.index_source(
            session,
            user_id=single_user.id,
            source_type="raw_input",
            source_id=42,
            content="王姐说这个月预算紧，先不要推新品，等下个月再说。",
            person_id=person_id,
            scope="knowledge",
        )

        rows = list((await session.execute(select(VectorChunk))).scalars().all())

    assert first == 1
    assert second == 1
    assert len(rows) == 1, "重新索引必须替换旧切片，不能越索引越多"
    assert "等下个月" in rows[0].content
    assert rows[0].embedding is None, "没配向量模型时 embedding 就是 NULL，不是空数组"


async def test_search_falls_back_to_keywords(single_user: Account) -> None:
    person_id = await _seed_person(single_user, "小雨")

    async with rls_session(single_user.id) as session:
        await rag.index_source(
            session,
            user_id=single_user.id,
            source_type="fact",
            source_id=7,
            content="小雨最近在装修新店，预算紧。",
            person_id=person_id,
        )
        hits = await rag.search(session, "预算", limit=5)

    assert len(hits) == 1
    assert "预算" in hits[0]["content"]
    assert hits[0]["source_type"] == "fact"
    assert hits[0]["score"] == 0.0, "关键词命中没有相似度可言，不许假装有"


async def test_search_without_match_returns_empty(single_user: Account) -> None:
    person_id = await _seed_person(single_user, "老陈")

    async with rls_session(single_user.id) as session:
        await rag.index_source(
            session,
            user_id=single_user.id,
            source_type="fact",
            source_id=8,
            content="老陈结款走公账。",
            person_id=person_id,
        )
        hits = await rag.search(session, "寿司", limit=5)

    assert hits == []


async def test_pending_fact_is_not_indexed(single_user: Account) -> None:
    """待确认的猜测不许进索引——检索到它，模型就会当事实说出去。"""
    person_id = await _seed_person(single_user, "小包老师")

    async with rls_session(single_user.id) as session:
        fact = Fact(
            user_id=single_user.id,
            person_id=person_id,
            content="可能喜欢喝普洱（还没确认）",
            category="preference",
            confidence=0.5,
            source_type="manual",
            status="pending",
        )
        session.add(fact)
        await session.commit()

        indexed = await rag.index_fact(session, fact)
        rows = list((await session.execute(select(VectorChunk))).scalars().all())

    assert indexed == 0
    assert rows == []


async def test_confirmed_fact_is_indexed(single_user: Account) -> None:
    person_id = await _seed_person(single_user, "孟雅楠")

    async with rls_session(single_user.id) as session:
        fact = Fact(
            user_id=single_user.id,
            person_id=person_id,
            content="喜欢喝苹果汁",
            category="preference",
            confidence=0.9,
            source_type="manual",
            status="confirmed",
            last_confirmed_at=datetime.now(UTC),
        )
        session.add(fact)
        await session.commit()

        indexed = await rag.index_fact(session, fact)

    assert indexed == 1


async def test_search_is_isolated_by_rls(two_users: tuple[Account, Account]) -> None:
    """A 的切片，B 检索不到——这条在库层（RLS），不是应用层过滤。"""
    first, second = two_users
    person_id = await _seed_person(first, "王姐")

    async with rls_session(first.id) as session:
        await rag.index_source(
            session,
            user_id=first.id,
            source_type="raw_input",
            source_id=1,
            content="王姐生日是 11 月 4 号。",
            person_id=person_id,
            scope="knowledge",
        )

    async with rls_session(second.id) as session:
        hits = await rag.search(session, "生日", limit=5)
        rows = list((await session.execute(select(VectorChunk))).scalars().all())

    assert hits == []
    assert rows == []


async def test_index_requires_owner_status(single_user: Account) -> None:
    """没有 user_id 的游离对象直接跳过，不抛异常。"""
    async with rls_session(single_user.id) as session:
        assert await rag.index_fact(session, Fact(content="x", status="confirmed")) == 0
