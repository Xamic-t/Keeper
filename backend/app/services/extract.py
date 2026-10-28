"""抽取：把一张截图 / 一段话，变成待确认的事实、事件、往来。

对应文档：specs/04-extract.md

三步走（识别 → 归属 → 抽取）在 prompt 里完成，代码只负责：
  ① 挑候选人（最近活跃的 20 个）
  ② 调模型、校验输出（不合格的条目直接丢）
  ③ 落库成 pending 态记录 + 写审计

**抽错比不抽更糟**，所以：没有 evidence 的条目一律丢，置信度低的只进确认队列。
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db import SessionLocal
from app.models.event import Event
from app.models.fact import Fact
from app.models.input import ChatSegment, RawInput
from app.models.ledger import Ledger
from app.models.log import ExtractionLog
from app.models.person import Person
from app.models.relationship import Relationship
from app.services import storage
from app.services.vision import vision

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

# category → 半衰期（天）。None = 永久不衰减
HALF_LIFE: dict[str, int | None] = {
    "permanent": None,
    "taboo": 540,
    "structure": 365,
    "preference": 180,
    "situation": 60,
    "mood": 14,
}

FACT_CATEGORIES = set(HALF_LIFE)
EVENT_TYPES = {"birthday", "payment_due", "custom"}
LEDGER_KINDS = {"tip", "order", "gift", "payment", "refund"}

# 低于这个置信度的条目照样入库，但一定进确认队列
CONFIDENCE_FLOOR = 0.7
# 金额 / 生日 / 禁忌这类"错一个就出事"的，无论置信度都要人看
STRICT_CATEGORIES = {"taboo", "permanent"}

MAX_SEGMENTS = 60


@dataclass(slots=True)
class FactDraft:
    content: str
    category: str
    confidence: float
    evidence: str
    half_life_days: int | None


@dataclass(slots=True)
class SegmentDraft:
    speaker: str
    content: str
    said_at: datetime | None = None


@dataclass(slots=True)
class EventDraft:
    type: str
    title: str
    occurs_on: date
    confidence: float
    evidence: str
    lunar: dict[str, Any] | None = None


@dataclass(slots=True)
class LedgerDraft:
    direction: str
    kind: str
    amount: Decimal
    occurred_at: date
    confidence: float
    evidence: str
    note: str | None = None


@dataclass(slots=True)
class ExtractionResult:
    """抽取结果。空结果也是合法结果。"""

    person_id: int | None = None
    is_chat: bool = True
    segments: list[SegmentDraft] = field(default_factory=list)
    facts: list[FactDraft] = field(default_factory=list)
    events: list[EventDraft] = field(default_factory=list)
    ledgers: list[LedgerDraft] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.segments or self.facts or self.events or self.ledgers)


def build_prompt(candidates: list[dict[str, Any]], kind: str) -> str:
    """把候选人和材料类型塞进模板。模板缺失是部署错误，直接 500。"""
    template_path = PROMPTS_DIR / "v1_extract.md"
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("prompt template missing path=%s", template_path.name)
        raise AppError(
            ErrorCode.INTERNAL_ERROR, "抽取模板缺失，请检查部署", status_code=500
        ) from exc

    candidates_json = json.dumps(candidates, ensure_ascii=False, indent=2)
    extra = (
        "候选列表为空——这是第一个联系人，person_id 请返回 null。"
        if not candidates
        else ""
    )
    return (
        template.replace("{candidates}", candidates_json)
        + f"\n\n材料类型：{kind}。{extra}\n"
    )


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, 0.0), 1.0)


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _as_amount(value: Any) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def parse_result(raw: dict[str, Any], candidates: list[dict[str, Any]]) -> ExtractionResult:
    """校验模型输出，丢掉不合格条目。宁可少抽，不可错抽。"""
    candidate_ids = {int(item["id"]) for item in candidates if item.get("id") is not None}

    person_id = raw.get("person_id")
    try:
        person_id = int(person_id) if person_id is not None else None
    except (TypeError, ValueError):
        person_id = None
    if person_id is not None and person_id not in candidate_ids:
        # 模型编了个不在候选里的人 → 归属待定
        logger.warning("model returned unknown person_id, dropped")
        person_id = None

    result = ExtractionResult(person_id=person_id, is_chat=bool(raw.get("is_chat", True)))

    for item in raw.get("segments") or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        speaker = item.get("speaker")
        if not content or speaker not in {"me", "other"}:
            continue
        result.segments.append(
            SegmentDraft(
                speaker=speaker, content=content, said_at=_as_datetime(item.get("said_at"))
            )
        )
    result.segments = result.segments[:MAX_SEGMENTS]

    for item in raw.get("facts") or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        category = str(item.get("category") or "").strip()
        if not content or not evidence or category not in FACT_CATEGORIES:
            continue
        half_life = item.get("half_life_days")
        if not isinstance(half_life, int):
            half_life = HALF_LIFE[category]
        result.facts.append(
            FactDraft(
                content=content,
                category=category,
                confidence=_as_float(item.get("confidence")),
                evidence=evidence,
                half_life_days=half_life,
            )
        )

    for item in raw.get("events") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        occurs_on = _as_date(item.get("occurs_on"))
        event_type = str(item.get("type") or "custom").strip()
        evidence = str(item.get("evidence") or "").strip()
        if not title or occurs_on is None or event_type not in EVENT_TYPES or not evidence:
            continue
        lunar = item.get("lunar") if isinstance(item.get("lunar"), dict) else None
        result.events.append(
            EventDraft(
                type=event_type,
                title=title,
                occurs_on=occurs_on,
                confidence=_as_float(item.get("confidence")),
                evidence=evidence,
                lunar=lunar,
            )
        )

    for item in raw.get("ledgers") or []:
        if not isinstance(item, dict):
            continue
        amount = _as_amount(item.get("amount"))
        occurred_at = _as_date(item.get("occurred_at"))
        direction = str(item.get("direction") or "").strip()
        kind = str(item.get("kind") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if (
            amount is None
            or occurred_at is None
            or direction not in {"in", "out"}
            or kind not in LEDGER_KINDS
            or not evidence
        ):
            continue
        result.ledgers.append(
            LedgerDraft(
                direction=direction,
                kind=kind,
                amount=amount,
                occurred_at=occurred_at,
                confidence=_as_float(item.get("confidence")),
                evidence=evidence,
                note=str(item.get("note") or "").strip() or None,
            )
        )

    return result


async def _candidates(session: AsyncSession) -> list[dict[str, Any]]:
    """最近活跃的 20 个人。名单进 prompt，用来判归属。"""
    stmt = (
        select(Person.id, Person.nickname, Person.aliases)
        .join(Relationship, Relationship.person_id == Person.id)
        .order_by(Relationship.last_interaction_at.desc().nulls_last(), Person.id.desc())
        .limit(20)
    )
    rows = (await session.execute(stmt)).all()
    return [{"id": row[0], "nickname": row[1], "aliases": row[2] or []} for row in rows]


async def _clear_previous(session: AsyncSession, raw_input_id: int) -> None:
    """同一张图重复抽取时先清旧结果，保证幂等。"""
    await session.execute(delete(ChatSegment).where(ChatSegment.raw_input_id == raw_input_id))
    for model in (Fact, Event):
        await session.execute(
            delete(model).where(
                model.source_type == "screenshot",  # type: ignore[attr-defined]
                model.source_id == raw_input_id,
            )
        )
    await session.execute(
        delete(Ledger).where(Ledger.source_type == "screenshot", Ledger.source_id == raw_input_id)
    )


async def persist_candidates(
    session: AsyncSession,
    *,
    user_id: int,
    raw_input_id: int,
    person_id: int | None,
    result: ExtractionResult,
) -> None:
    """把抽取结果写成待确认记录。事实/事件必须挂人，归属不明就先不落。"""
    await _clear_previous(session, raw_input_id)

    for index, segment in enumerate(result.segments):
        session.add(
            ChatSegment(
                user_id=user_id,
                raw_input_id=raw_input_id,
                person_id=person_id,
                speaker=segment.speaker,
                content=segment.content,
                said_at=segment.said_at,
                said_at_inferred=segment.said_at is None,
                seq=index,
            )
        )

    if person_id is None:
        # 归属待定：只留对话原文，等用户在确认队列里指定人
        return

    for fact in result.facts:
        session.add(
            Fact(
                user_id=user_id,
                person_id=person_id,
                content=fact.content,
                category=fact.category,
                half_life_days=fact.half_life_days,
                weight=1.0,
                confidence=fact.confidence,
                source_type="screenshot",
                source_id=raw_input_id,
                evidence=fact.evidence,
                status="pending",
            )
        )

    for event in result.events:
        session.add(
            Event(
                user_id=user_id,
                person_id=person_id,
                type=event.type,
                title=event.title,
                occurs_on=event.occurs_on,
                lunar=event.lunar,
                source_type="screenshot",
                source_id=raw_input_id,
            )
        )

    # 往来 V1 没有 pending 态，直接落库（金额错了用户在往来页删）
    for ledger in result.ledgers:
        session.add(
            Ledger(
                user_id=user_id,
                person_id=person_id,
                direction=ledger.direction,
                kind=ledger.kind,
                amount=ledger.amount,
                occurred_at=ledger.occurred_at,
                note=ledger.note,
                source_type="screenshot",
                source_id=raw_input_id,
            )
        )

    # 有事被确认后综述会过期，这里先标记，等 W7 懒生成
    person = await session.get(Person, person_id)
    if person is not None and (result.facts or result.events):
        person.summary_stale = True
        session.add(person)


async def extract_from_raw_input(raw_input_id: int) -> ExtractionResult:
    """编排整个抽取流程。**自己开 session**，绝不复用请求的。

    任何失败都写进 raw_inputs.status='failed'，不向上抛——后台任务没有请求上下文，
    抛出去只会变成一个没人看的堆栈。
    """
    started = time.perf_counter()
    result = ExtractionResult()

    async with SessionLocal() as session:
        raw_input = await session.get(RawInput, raw_input_id)
        if raw_input is None:
            logger.warning("raw_input missing id=%s", raw_input_id)
            return result

        user_id = raw_input.user_id
        session.info["rls_user_id"] = user_id

        raw_input.status = "processing"
        session.add(raw_input)
        await session.commit()

        try:
            prompt = build_prompt(await _candidates(session), raw_input.kind)
            image = storage.read_file(raw_input.file_path) if raw_input.file_path else None
            reply = await vision.extract(
                prompt=prompt,
                image=image,
                text=raw_input.raw_text or raw_input.ocr_text,
                schema={"type": "object"},
            )
            result = parse_result(reply.parsed, await _candidates(session))

            await persist_candidates(
                session,
                user_id=user_id,
                raw_input_id=raw_input_id,
                person_id=result.person_id,
                result=result,
            )

            raw_input.person_id = result.person_id
            raw_input.status = "done"
            raw_input.error_msg = None
            raw_input.processed_at = datetime.now(UTC)
            session.add(raw_input)

            session.add(
                ExtractionLog(
                    user_id=user_id,
                    raw_input_id=raw_input_id,
                    model_name=reply.model,
                    prompt_version=PROMPT_VERSION,
                    raw_output={"content": reply.raw[:4000]},
                    parsed_output={
                        "person_id": result.person_id,
                        "facts": len(result.facts),
                        "events": len(result.events),
                        "ledgers": len(result.ledgers),
                        "segments": len(result.segments),
                    },
                    confidence_avg=_avg_confidence(result),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            )
            await session.commit()
            logger.info(
                "extract done raw_input_id=%s facts=%s provider=%s",
                raw_input_id,
                len(result.facts),
                reply.provider,
            )
        except AppError as exc:
            await session.rollback()
            await _mark_failed(session, raw_input_id, user_id, exc.code.value, started)
        except Exception as exc:  # noqa: BLE001 — 后台任务不能把异常抛出去
            await session.rollback()
            logger.exception("extract crashed raw_input_id=%s", raw_input_id)
            await _mark_failed(session, raw_input_id, user_id, type(exc).__name__, started)

    return result


def _avg_confidence(result: ExtractionResult) -> float:
    scores = [item.confidence for item in result.facts]
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 3)


async def _mark_failed(
    session: AsyncSession,
    raw_input_id: int,
    user_id: int,
    reason: str,
    started: float,
) -> None:
    """失败也要留痕：raw_inputs 标 failed，审计表照样写一条。"""
    raw_input = await session.get(RawInput, raw_input_id)
    if raw_input is not None:
        raw_input.status = "failed"
        raw_input.error_msg = reason[:500]
        raw_input.processed_at = datetime.now(UTC)
        session.add(raw_input)

    session.add(
        ExtractionLog(
            user_id=user_id,
            raw_input_id=raw_input_id,
            model_name=None,
            prompt_version=PROMPT_VERSION,
            parsed_output={"error": reason},
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    )
    await session.commit()
