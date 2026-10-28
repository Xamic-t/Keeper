"""画像：把一堆零散事实，变成"打开档案 10 秒就知道这人是谁"。

对应文档：specs/06-profile.md

三条规则：
  ① 综述给人读，150 字以内，像在描述一个人，不是罗列字段
  ② 事实会过期：权重按半衰期衰减，低于 0.5 的不进综述
  ③ 综述不能每次读都算：用 summary_stale 标记，懒生成，失败就留着旧的
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.fact import Fact
from app.models.person import Person
from app.models.relationship import Relationship
from app.services.vision import vision

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

MAX_SUMMARY_LENGTH = 150
MAX_FACTS_FOR_SUMMARY = 15
WEIGHT_THRESHOLD = 0.5

EMPTY_SUMMARY = "还没有足够信息，先投喂几张聊天截图。"

# 半衰期（天）→ 权重 = 0.5 ** (天数 / 半衰期)
HALF_LIFE: dict[str, int | None] = {
    "permanent": None,
    "taboo": 540,
    "structure": 365,
    "preference": 180,
    "situation": 60,
    "mood": 14,
}


@dataclass(frozen=True, slots=True)
class WeightUpdate:
    """一条事实的权重变化。批量重算时用来打日志（不含隐私内容）。"""

    fact_id: int
    weight: float


def compute_weight(
    half_life_days: int | None, last_confirmed_at: datetime | None, *, today: date | None = None
) -> float:
    """按半衰期算一条事实现在的权重。永久事实恒为 1.0。"""
    if half_life_days is None or half_life_days <= 0:
        return 1.0
    if last_confirmed_at is None:
        return 1.0

    reference = today or datetime.now(UTC).date()
    days = (reference - last_confirmed_at.date()).days
    if days <= 0:
        return 1.0

    weight = 0.5 ** (days / half_life_days)
    return round(min(max(weight, 0.0), 1.0), 3)


async def select_facts_for_summary(session: AsyncSession, person_id: int) -> list[Fact]:
    """挑出能进综述的事实：已确认、权重够、按权重降序，最多 15 条。"""
    stmt = (
        select(Fact)
        .where(
            Fact.person_id == person_id,
            Fact.status == "confirmed",
            Fact.weight > WEIGHT_THRESHOLD,
        )
        .order_by(Fact.weight.desc(), Fact.last_confirmed_at.desc().nulls_last())
        .limit(MAX_FACTS_FOR_SUMMARY)
    )
    return list((await session.execute(stmt)).scalars().all())


def _render_prompt(person: Person, relationship: Relationship | None, facts: list[Fact]) -> str:
    template_path = PROMPTS_DIR / "v1_summary.md"
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("summary prompt missing")
        raise AppError(ErrorCode.INTERNAL_ERROR, "综述模板缺失", status_code=500) from exc

    payload = {
        "nickname": person.nickname,
        "aliases": person.aliases,
        "stage": relationship.stage if relationship else "new",
        "roles": relationship.roles if relationship else [],
        "birthday": relationship.birthday_solar.isoformat()
        if relationship and relationship.birthday_solar
        else None,
        "taboo": relationship.taboo if relationship else [],
        "facts": [{"category": fact.category, "content": fact.content} for fact in facts],
    }
    return (
        template.replace("{nickname}", person.nickname)
        .replace("{aliases}", json.dumps(payload["aliases"], ensure_ascii=False))
        .replace("{stage}", str(payload["stage"]))
        .replace("{roles}", json.dumps(payload["roles"], ensure_ascii=False))
        .replace("{birthday}", str(payload["birthday"] or ""))
        .replace("{taboo}", json.dumps(payload["taboo"], ensure_ascii=False))
        .replace("{facts}", json.dumps(payload["facts"], ensure_ascii=False, indent=2))
    )


def _clean(text: str) -> str:
    """去掉 markdown 痕迹与多余空白，并截断到 150 字。"""
    cleaned = re.sub(r"[#*`>_]", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip('"“”')
    if len(cleaned) > MAX_SUMMARY_LENGTH:
        cleaned = cleaned[:MAX_SUMMARY_LENGTH].rstrip() + "…"
    return cleaned


async def rewrite_summary(session: AsyncSession, person_id: int) -> str:
    """调模型重写综述。失败保留旧综述，stale 保持 True，下次再试。"""
    person = await session.get(Person, person_id)
    if person is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个人", status_code=404)

    relationship = (
        await session.execute(select(Relationship).where(Relationship.person_id == person_id))
    ).scalar_one_or_none()
    facts = await select_facts_for_summary(session, person_id)

    if not facts:
        person.summary = EMPTY_SUMMARY
        person.summary_stale = False
        session.add(person)
        await session.commit()
        return EMPTY_SUMMARY

    prompt = _render_prompt(person, relationship, facts)
    try:
        reply = await vision.complete(prompt=prompt)
        summary = _clean(reply.raw) or EMPTY_SUMMARY
    except AppError as exc:
        # 模型不可用：不覆盖旧综述，也不把 stale 清掉
        logger.warning("summary rewrite failed person_id=%s code=%s", person_id, exc.code)
        return person.summary or ""

    person.summary = summary
    person.summary_stale = False
    session.add(person)
    await session.commit()
    return summary


async def refresh_weights(session: AsyncSession) -> int:
    """每日重算所有已确认事实的权重。不改 last_confirmed_at（那会重置衰减）。"""
    stmt = select(Fact).where(Fact.status == "confirmed", Fact.half_life_days.is_not(None))
    facts = list((await session.execute(stmt)).scalars().all())

    updated = 0
    for fact in facts:
        new_weight = compute_weight(fact.half_life_days, fact.last_confirmed_at)
        if abs(new_weight - fact.weight) < 0.001:
            continue
        fact.weight = new_weight
        session.add(fact)
        updated += 1

    if updated:
        await session.commit()
    return updated


async def rebuild_tags(session: AsyncSession, person_id: int, user_id: int) -> int:
    """从 facts 全量重建该人的标签（数据修复用）。"""
    from app.services.confirm import derive_tags

    stmt = select(Fact).where(Fact.person_id == person_id, Fact.status == "confirmed")
    facts = list((await session.execute(stmt)).scalars().all())

    created = 0
    for fact in facts:
        created += await derive_tags(session, fact, user_id)
    await session.commit()
    return created


async def facts_of(
    session: AsyncSession, person_id: int, *, include_pending: bool = False
) -> list[Fact]:
    """某人已确认的事实，按权重排序。档案页与确认队列都用它。"""
    stmt = select(Fact).where(Fact.person_id == person_id)
    if not include_pending:
        stmt = stmt.where(Fact.status == "confirmed")
    stmt = stmt.order_by(Fact.weight.desc(), Fact.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())


def weight_hint(category: str) -> int | None:
    """给抽取落库用的半衰期。类别不认就按 situation 处理。"""
    return HALF_LIFE.get(category, HALF_LIFE["situation"])


def summarize_facts_for_reminder(facts: list[Fact], limit: int = 6) -> list[dict[str, Any]]:
    """提醒里要引用的事实摘要（隐私：只带内容和类别，不带 id）。"""
    return [
        {"category": fact.category, "content": fact.content}
        for fact in facts[:limit]
        if fact.confidence >= 0.7
    ]
