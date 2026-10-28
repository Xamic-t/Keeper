"""提醒：在正确的时间，推一条带依据、能直接用的提醒。

对应文档：specs/09-reminder.md

三条规则：
  ① 建议必须带依据（`reasons` 非空），否则这条提醒降级为"只报日期"
  ② 幂等靠数据库唯一约束 (event_id, remind_on, level)，不靠应用层判断
  ③ 推送失败不影响任务：send 只记状态，不抛异常
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.event import Event, Reminder
from app.models.fact import Fact
from app.models.ledger import Ledger
from app.models.person import Person
from app.models.relationship import Relationship
from app.models.todo import Todo
from app.services import notify, profile
from app.services.vision import vision

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
PROMPT_VERSION = "v1"

# 各类型的提醒档位（距当天几天）
# 节日是 T-3 而非 T-2：库里 ck_reminder_level 只允许 d7/d3/d0
LEVELS: dict[str, tuple[int, ...]] = {
    "birthday": (7, 3, 0),
    "payment_due": (3, 0),
    "festival": (3,),
    "custom": (3, 0),
}
LEVEL_NAMES = {7: "d7", 3: "d3", 0: "d0"}
# 扫描窗口：未来 7 天
SCAN_DAYS = 7


@dataclass(slots=True)
class ReminderDraft:
    """一条待写库的提醒。"""

    user_id: int
    event_id: int
    person_id: int | None
    remind_on: date
    level: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScanResult:
    created: int = 0
    sent: int = 0
    failed: int = 0


def _level_name(days: int) -> str:
    return LEVEL_NAMES.get(days, f"d{days}")


def _hits(event_type: str, days_left: int) -> bool:
    """今天是不是该提醒这个事件。"""
    return days_left in LEVELS.get(event_type, ())


async def _person_context(
    session: AsyncSession, person_id: int
) -> tuple[Person | None, Relationship | None, list[Fact], list[Ledger]]:
    person = await session.get(Person, person_id)
    if person is None:
        return None, None, [], []
    relationship = (
        await session.execute(select(Relationship).where(Relationship.person_id == person_id))
    ).scalar_one_or_none()
    facts = await profile.select_facts_for_summary(session, person_id)
    ledger = list(
        (
            await session.execute(
                select(Ledger)
                .where(Ledger.person_id == person_id)
                .order_by(Ledger.occurred_at.desc())
                .limit(6)
            )
        )
        .scalars()
        .all()
    )
    return person, relationship, facts, ledger


def _render_prompt(
    *,
    level: str,
    event: Event,
    days_left: int,
    person: Person,
    relationship: Relationship | None,
    facts: list[Fact],
    ledger: list[Ledger],
) -> str:
    template_path = PROMPTS_DIR / "v1_reminder.md"
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AppError(ErrorCode.INTERNAL_ERROR, "提醒模板缺失", status_code=500) from exc

    payload = {
        "level": level,
        "event_type": event.type,
        "event_title": event.title,
        "event_date": event.occurs_on.isoformat(),
        "days_left": days_left,
        "nickname": person.nickname,
        "stage": relationship.stage if relationship else "new",
        "budget_min": str(relationship.gift_budget_min) if relationship else "",
        "budget_max": str(relationship.gift_budget_max) if relationship else "",
        "taboo": relationship.taboo if relationship else [],
        "summary": person.summary or "",
        "tags": [],
        "memory_facts": [
            {"category": fact.category, "content": fact.content} for fact in facts[:6]
        ],
        "history_ledger": [
            {
                "direction": item.direction,
                "kind": item.kind,
                "amount": str(item.amount),
                "occurred_at": item.occurred_at.isoformat(),
                "note": item.note,
            }
            for item in ledger
        ],
        "knowledge_hint": "私域送礼讲究：不重复、不越界、有由头。",
    }

    text = template
    for key, value in payload.items():
        if isinstance(value, list | dict):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = str(value)
        text = text.replace("{" + key + "}", rendered)
    return text


def _degraded_payload(event: Event, days_left: int) -> dict[str, Any]:
    """模型不可用时的降级内容：只报日期，不编建议。"""
    return {
        "headline": f"{event.title}还有 {days_left} 天",
        "suggestion": "AI 暂时不可用，先自己看看档案里记过什么。",
        "reasons": [f"日历里有这件事：{event.title}（{event.occurs_on.isoformat()}）"],
        "script": "",
        "degraded": True,
    }


async def generate_content(
    session: AsyncSession, event: Event, person_id: int, level: str, days_left: int
) -> dict[str, Any]:
    """生成一条完整提醒：事实陈述 + 建议 + 依据 + 话术。"""
    person, relationship, facts, ledger = await _person_context(session, person_id)
    if person is None:
        return _degraded_payload(event, days_left)

    prompt = _render_prompt(
        level=level,
        event=event,
        days_left=days_left,
        person=person,
        relationship=relationship,
        facts=facts,
        ledger=ledger,
    )

    try:
        reply = await vision.extract(prompt=prompt, schema={"type": "object"})
        data = reply.parsed
    except AppError as exc:
        logger.warning("reminder content degraded event_id=%s code=%s", event.id, exc.code)
        return _degraded_payload(event, days_left)

    reasons = data.get("reasons") or []
    if not isinstance(reasons, list) or not reasons:
        # 没依据的建议一律降级：宁可只说日期，也不给一句没有来由的"建议送茶叶"
        logger.warning("reminder had no reasons event_id=%s", event.id)
        return _degraded_payload(event, days_left)

    return {
        "headline": str(data.get("headline") or event.title),
        "suggestion": str(data.get("suggestion") or ""),
        "reasons": [str(item) for item in reasons][:5],
        "script": str(data.get("script") or ""),
        "degraded": bool(data.get("degraded", False)),
    }


async def upsert_reminder(session: AsyncSession, draft: ReminderDraft) -> tuple[Reminder, bool]:
    """幂等写入。返回 (记录, 是否新建)。已存在且 sent 的不重发。"""
    stmt = select(Reminder).where(
        Reminder.event_id == draft.event_id,
        Reminder.remind_on == draft.remind_on,
        Reminder.level == draft.level,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        if existing.status == "failed":
            existing.payload = draft.payload
            existing.status = "pending"
            session.add(existing)
            await session.commit()
        return existing, False

    reminder = Reminder(
        user_id=draft.user_id,
        event_id=draft.event_id,
        person_id=draft.person_id,
        remind_on=draft.remind_on,
        level=draft.level,
        payload=draft.payload,
        status="pending",
    )
    session.add(reminder)
    try:
        await session.commit()
    except IntegrityError:
        # 并发下两个任务同时写，靠唯一约束兜住
        await session.rollback()
        existing = (await session.execute(stmt)).scalar_one()
        return existing, False
    return reminder, True


async def scan_due_events(session: AsyncSession, today: date) -> list[ReminderDraft]:
    """扫未来 7 天内该提醒的事件，生成草稿。"""
    horizon = today + timedelta(days=SCAN_DAYS)
    stmt = select(Event).where(Event.occurs_on.between(today, horizon), Event.is_done.is_(False))
    events = list((await session.execute(stmt)).scalars().all())

    drafts: list[ReminderDraft] = []
    for event in events:
        days_left = (event.occurs_on - today).days
        if not _hits(event.type, days_left):
            continue
        if event.type in {"birthday", "payment_due", "custom"} and event.person_id is None:
            continue

        level = _level_name(days_left)
        if event.person_id is None:
            payload = _degraded_payload(event, days_left)
        else:
            payload = await generate_content(session, event, event.person_id, level, days_left)

        drafts.append(
            ReminderDraft(
                user_id=event.user_id,
                event_id=event.id or 0,
                person_id=event.person_id,
                remind_on=today,
                level=level,
                payload=payload,
            )
        )
    return drafts


async def send(session: AsyncSession, reminder: Reminder) -> bool:
    """按通道发送。失败只改状态，不抛。"""
    payload = reminder.payload or {}
    message = notify.render(payload, fallback_title="今天有件事")

    channel = await notify.push(session, reminder.user_id, message)
    if channel is None:
        reminder.status = "failed"
        reminder.channel = None
        session.add(reminder)
        await session.commit()
        return False

    reminder.status = "sent"
    reminder.channel = channel
    reminder.sent_at = datetime.now(UTC)
    session.add(reminder)
    await session.commit()
    return True


async def run_daily(session: AsyncSession, today: date | None = None) -> ScanResult:
    """每日任务主体：扫事件 → 写提醒 → 发送。重跑不会重复发。"""
    reference = today or datetime.now(UTC).date()
    result = ScanResult()

    for draft in await scan_due_events(session, reference):
        try:
            reminder, created = await upsert_reminder(session, draft)
            if created:
                result.created += 1
            if reminder.status == "sent":
                continue
            if await send(session, reminder):
                result.sent += 1
            else:
                result.failed += 1
        except Exception:  # noqa: BLE001 — 单条失败不能带崩其他人的提醒
            logger.exception("reminder failed event_id=%s", draft.event_id)
            result.failed += 1
    return result


async def to_todo(session: AsyncSession, reminder_id: int, user_id: int) -> Todo:
    """提醒转待办。重复转返回 409。"""
    reminder = await session.get(Reminder, reminder_id)
    if reminder is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这条提醒", status_code=404)
    if reminder.status not in {"sent", "clicked"}:
        raise AppError(ErrorCode.INVALID_STATE, "这条提醒还没发出去，不能转待办", status_code=400)

    existing = (
        await session.execute(
            select(Todo).where(Todo.source == "reminder", Todo.source_id == reminder_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError(
            ErrorCode.CONFLICT,
            "这条提醒已经转过待办了",
            status_code=409,
            detail={"todo_id": existing.id},
        )

    payload = reminder.payload or {}
    todo = Todo(
        user_id=user_id,
        person_id=reminder.person_id,
        title=str(payload.get("headline") or "处理一条提醒"),
        detail=str(payload.get("suggestion") or ""),
        source="reminder",
        source_id=reminder_id,
        ai_reason="；".join(str(item) for item in (payload.get("reasons") or [])) or None,
        due_date=reminder.remind_on,
        status="open",
    )
    session.add(todo)
    await session.commit()
    return todo


async def regenerate(session: AsyncSession, reminder_id: int) -> Reminder:
    """换一个建议：重跑生成，覆盖 payload。"""
    reminder = await session.get(Reminder, reminder_id)
    if reminder is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这条提醒", status_code=404)

    event = await session.get(Event, reminder.event_id)
    if event is None:
        raise AppError(ErrorCode.NOT_FOUND, "这条提醒对应的事件已删除", status_code=404)

    days_left = max((event.occurs_on - datetime.now(UTC).date()).days, 0)
    if event.person_id is None:
        payload = _degraded_payload(event, days_left)
    else:
        payload = await generate_content(session, event, event.person_id, reminder.level, days_left)
    reminder.payload = payload
    session.add(reminder)
    await session.commit()
    return reminder


async def list_reminders(session: AsyncSession, limit: int = 20) -> list[Reminder]:
    """提醒历史，新的在前。"""
    stmt = select(Reminder).order_by(Reminder.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())
