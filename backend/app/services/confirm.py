"""确认队列：AI 抽出来的东西，人点一遍 ✓ / ✗ 才进档案。

对应文档：specs/05-confirm.md

三条规则：
  ① AI 不许直接写档案（生日、金额、禁忌抽错比没有系统更糟）
  ② 每张卡片必须带出处（evidence + 原图 URL）
  ③ 确认后要级联：派生标签 → 重算累计 → 标记综述过期
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.fact import Fact, Tag
from app.models.input import ChatSegment, RawInput
from app.models.person import Person
from app.services import ledger as ledger_service

logger = logging.getLogger(__name__)

# 事实类别 → 标签 key（tags 表的 CHECK 白名单只有这 6 个）
CATEGORY_TO_KEY = {
    "structure": "industry",
    "preference": "preference",
    "situation": "situation",
}

# 半衰期短的事实派生出来的标签也要过期，否则"最近在装修"会挂一辈子
TAG_TTL_DAYS = {
    "situation": 60,
    "mood": 14,
}

# 这些前缀在派生标签时剥掉，剩下的才像"标签值"
_VALUE_PREFIXES = ("喜欢", "爱喝", "爱吃", "爱", "常喝", "偏好", "从事", "做的", "做", "在")

MAX_TAG_LENGTH = 100


@dataclass(slots=True)
class ConfirmResult:
    fact_id: int
    status: str
    cascaded: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BatchResult:
    confirmed: int = 0
    skipped: int = 0
    skipped_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class ConfirmationItem:
    id: int
    content: str
    category: str
    confidence: float
    evidence: str | None
    raw_input_id: int | None
    created_at: datetime
    # fact = 已归属的事实卡片；unassigned = 归属待定的材料（id 是 raw_input.id）
    kind: str = "fact"


@dataclass(slots=True)
class ConfirmationGroup:
    person_id: int | None
    nickname: str
    items: list[ConfirmationItem]


def _tag_value(content: str) -> str | None:
    """从事实内容里抠出一个短标签值。规则版，抽不出就返回 None。"""
    text = content.strip()
    for prefix in _VALUE_PREFIXES:
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix) :]
            break
    for separator in ("，", ",", "。", "；", ";", "、"):
        text = text.split(separator)[0]
    text = text.strip()
    if not text or len(text) < 2:
        return None
    return text[:MAX_TAG_LENGTH]


async def derive_tags(session: AsyncSession, fact: Fact, user_id: int) -> int:
    """按事实的 category 派生标签。重复的忽略，抽不出值就跳过。"""
    key = CATEGORY_TO_KEY.get(fact.category)
    if key is None:
        # permanent / taboo / mood 不建标签：生日进关系表，禁忌单独存，情绪不值得沉淀
        return 0

    value = _tag_value(fact.content)
    if value is None:
        return 0

    # structure 里提到家属的，归到 family 而不是 industry
    if key == "industry" and any(
        word in fact.content for word in ("女儿", "儿子", "孩子", "老公", "老婆", "爸", "妈")
    ):
        key = "family"

    expires_at = None
    if fact.category in TAG_TTL_DAYS:
        expires_at = datetime.now(UTC) + timedelta(days=TAG_TTL_DAYS[fact.category])

    await session.execute(
        insert(Tag)
        .values(
            user_id=user_id,
            person_id=fact.person_id,
            key=key,
            value=value,
            source_fact_id=fact.id,
            expires_at=expires_at,
            confidence=fact.confidence,
        )
        .on_conflict_do_nothing(index_elements=["person_id", "key", "value"])
    )
    return 1


async def confirm_fact(session: AsyncSession, fact_id: int, user_id: int) -> ConfirmResult:
    """确认一条事实并触发级联。已确认过的再点一次报 400，防止误点。"""
    fact = await session.get(Fact, fact_id)
    if fact is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这条待确认", status_code=404)
    if fact.status != "pending":
        raise AppError(
            ErrorCode.INVALID_STATE,
            "这条已经处理过了",
            status_code=400,
            detail={"status": fact.status},
        )

    fact.status = "confirmed"
    fact.last_confirmed_at = datetime.now(UTC)
    session.add(fact)

    tags_derived = await derive_tags(session, fact, user_id)

    # 涉及金额的事实（极少见）顺手重算一次累计，保证 total_* 不会落后
    totals_recalculated = False
    if fact.category == "permanent" and "元" in fact.content:
        await ledger_service.recalc_totals(session, fact.person_id)
        totals_recalculated = True

    person = await session.get(Person, fact.person_id)
    summary_stale = False
    if person is not None:
        person.summary_stale = True
        session.add(person)
        summary_stale = True

    await session.commit()
    return ConfirmResult(
        fact_id=fact_id,
        status="confirmed",
        cascaded={
            "tags_derived": tags_derived,
            "totals_recalculated": totals_recalculated,
            "summary_stale": summary_stale,
        },
    )


async def reject_fact(session: AsyncSession, fact_id: int) -> ConfirmResult:
    """驳回一条。数据保留（用来改进抽取），但档案页不展示。"""
    fact = await session.get(Fact, fact_id)
    if fact is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这条待确认", status_code=404)
    if fact.status != "pending":
        raise AppError(ErrorCode.INVALID_STATE, "这条已经处理过了", status_code=400)

    fact.status = "rejected"
    session.add(fact)
    await session.commit()
    return ConfirmResult(fact_id=fact_id, status="rejected", cascaded={})


async def reassign_fact(
    session: AsyncSession,
    fact_id: int,
    user_id: int,
    *,
    person_id: int | None = None,
    content: str | None = None,
    category: str | None = None,
) -> ConfirmResult:
    """改归属 / 改内容后确认。改归属要重新派生标签。"""
    fact = await session.get(Fact, fact_id)
    if fact is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这条待确认", status_code=404)

    if person_id is not None:
        if await session.get(Person, person_id) is None:
            raise AppError(ErrorCode.NOT_FOUND, "找不到这个人", status_code=404)
        fact.person_id = person_id
    if content is not None:
        fact.content = content
    if category is not None:
        fact.category = category

    fact.status = "confirmed"
    fact.last_confirmed_at = datetime.now(UTC)
    session.add(fact)

    tags_derived = await derive_tags(session, fact, user_id)
    person = await session.get(Person, fact.person_id)
    if person is not None:
        person.summary_stale = True
        session.add(person)

    await session.commit()
    return ConfirmResult(
        fact_id=fact_id,
        status="confirmed",
        cascaded={
            "tags_derived": tags_derived,
            "totals_recalculated": False,
            "summary_stale": True,
        },
    )


async def batch_confirm(session: AsyncSession, fact_ids: list[int], user_id: int) -> BatchResult:
    """批量确认。某条不是 pending 就跳过，不整体失败。"""
    result = BatchResult()
    for fact_id in fact_ids:
        try:
            await confirm_fact(session, fact_id, user_id)
            result.confirmed += 1
        except AppError:
            result.skipped += 1
            result.skipped_ids.append(fact_id)
    return result


async def list_confirmations(session: AsyncSession) -> list[ConfirmationGroup]:
    """待确认条目，按人分组。归属待定的材料单独一组。"""
    stmt = (
        select(Fact, Person.nickname)
        .join(Person, Person.id == Fact.person_id)
        .where(Fact.status == "pending")
        .order_by(Fact.confidence.asc(), Fact.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()

    grouped: dict[int, ConfirmationGroup] = {}
    for fact, nickname in rows:
        group = grouped.setdefault(
            fact.person_id,
            ConfirmationGroup(person_id=fact.person_id, nickname=nickname, items=[]),
        )
        group.items.append(
            ConfirmationItem(
                id=fact.id or 0,
                content=fact.content,
                category=fact.category,
                confidence=fact.confidence,
                evidence=fact.evidence,
                raw_input_id=fact.source_id,
                created_at=fact.created_at,
            )
        )

    # 归属待定：识别出来是聊天，但没判出是谁 → 用对话原文让用户指人
    pending_person = (
        (
            await session.execute(
                select(RawInput)
                .where(RawInput.person_id.is_(None), RawInput.status == "done")
                .order_by(RawInput.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

    if pending_person:
        items: list[ConfirmationItem] = []
        for raw_input in pending_person:
            segment = (
                await session.execute(
                    select(ChatSegment)
                    .where(ChatSegment.raw_input_id == raw_input.id)
                    .order_by(ChatSegment.seq)
                    .limit(1)
                )
            ).scalar_one_or_none()
            items.append(
                ConfirmationItem(
                    id=raw_input.id or 0,
                    content=segment.content if segment else "（这条材料没识别出对话）",
                    category="unassigned",
                    confidence=0.0,
                    evidence=None,
                    raw_input_id=raw_input.id,
                    created_at=raw_input.created_at,
                    kind="unassigned",
                )
            )
        grouped[0] = ConfirmationGroup(person_id=None, nickname="归属待定", items=items)

    return list(grouped.values())
