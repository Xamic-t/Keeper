"""往来：记清楚"我给他、他给我，多少钱，什么时候"。

对应文档：specs/07-ledger.md

三条规则：
  ① 金额永远正数，方向靠 direction（负数会让求和和比较都变复杂）
  ② 累计用 SUM 重算，绝不做增量加减（增量会漂）
  ③ 有结款日的往来同步成日历事件，结清就删掉事件
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.event import Event
from app.models.ledger import Ledger
from app.models.person import Person
from app.models.relationship import Relationship

logger = logging.getLogger(__name__)

LEDGER_KINDS = {"tip", "order", "gift", "payment", "refund"}


async def list_ledger(
    session: AsyncSession,
    person_id: int,
    *,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Ledger], int]:
    """某人的往来明细，按发生时间倒序。"""
    if await session.get(Person, person_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个人", status_code=404)

    count_stmt = (
        select(func.count()).select_from(Ledger).where(Ledger.person_id == person_id)
    )
    stmt = (
        select(Ledger)
        .where(Ledger.person_id == person_id)
        .order_by(Ledger.occurred_at.desc(), Ledger.id.desc())
        .offset(offset)
        .limit(limit)
    )
    total = (await session.execute(count_stmt)).scalar_one()
    rows = list((await session.execute(stmt)).scalars().all())
    return rows, total


async def recalc_totals(session: AsyncSession, person_id: int) -> None:
    """从 ledger 重算 total_in / total_out。**只实现这一份**，confirm 也调它。"""
    stmt = (
        select(Ledger.direction, func.coalesce(func.sum(Ledger.amount), 0))
        .where(Ledger.person_id == person_id)
        .group_by(Ledger.direction)
    )
    totals = {"in": Decimal("0"), "out": Decimal("0")}
    for direction, amount in (await session.execute(stmt)).all():
        if direction in totals:
            totals[direction] = Decimal(amount or 0)

    relationship = (
        await session.execute(
            select(Relationship).where(Relationship.person_id == person_id)
        )
    ).scalar_one_or_none()
    if relationship is None:
        return  # 数据不一致交给读档案时报错，这里不重复抛

    relationship.total_in = totals["in"]
    relationship.total_out = totals["out"]
    session.add(relationship)


async def sync_due_event(session: AsyncSession, ledger: Ledger) -> None:
    """把结款日同步到日历。没有结款日或已结清 → 删事件。"""
    stmt = select(Event).where(
        Event.source_type == "ledger", Event.source_id == ledger.id
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()

    if ledger.due_date is None or ledger.settled_at is not None:
        if existing is not None:
            await session.execute(delete(Event).where(Event.id == existing.id))
        return

    person = await session.get(Person, ledger.person_id)
    title = f"{person.nickname if person else '某人'}的款到期"

    if existing is None:
        session.add(
            Event(
                user_id=ledger.user_id,
                person_id=ledger.person_id,
                type="payment_due",
                title=title,
                occurs_on=ledger.due_date,
                source_type="ledger",
                source_id=ledger.id,
            )
        )
        return

    existing.title = title
    existing.occurs_on = ledger.due_date
    existing.person_id = ledger.person_id
    session.add(existing)


async def create_ledger(
    session: AsyncSession,
    user_id: int,
    *,
    person_id: int,
    direction: str,
    kind: str,
    amount: Decimal,
    occurred_at: date,
    due_date: date | None = None,
    note: str | None = None,
) -> Ledger:
    """新增一笔往来，同步累计和日历事件。"""
    if await session.get(Person, person_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个人", status_code=404)
    if kind not in LEDGER_KINDS:
        raise AppError(ErrorCode.VALIDATION_ERROR, "往来类型不合法", status_code=400)
    if due_date is not None and due_date < occurred_at:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "结款日不能早于发生日", status_code=400
        )

    ledger = Ledger(
        user_id=user_id,
        person_id=person_id,
        direction=direction,
        kind=kind,
        amount=amount,
        occurred_at=occurred_at,
        due_date=due_date,
        note=note,
        source_type="manual",
    )
    session.add(ledger)
    await session.flush()

    await recalc_totals(session, person_id)
    await sync_due_event(session, ledger)
    await session.commit()
    return ledger


async def update_ledger(
    session: AsyncSession,
    ledger_id: int,
    changes: dict[str, Any],
) -> Ledger:
    """改一笔往来。改完重算累计、同步事件。"""
    ledger = await session.get(Ledger, ledger_id)
    if ledger is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这笔往来", status_code=404)

    allowed = {"direction", "kind", "amount", "occurred_at", "due_date", "settled_at", "note"}
    for key, value in changes.items():
        if key in allowed:
            setattr(ledger, key, value)
    if ledger.kind not in LEDGER_KINDS:
        raise AppError(ErrorCode.VALIDATION_ERROR, "往来类型不合法", status_code=400)
    if ledger.due_date is not None and ledger.due_date < ledger.occurred_at:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "结款日不能早于发生日", status_code=400
        )

    session.add(ledger)
    await recalc_totals(session, ledger.person_id)
    await sync_due_event(session, ledger)
    await session.commit()
    return ledger


async def delete_ledger(session: AsyncSession, ledger_id: int) -> None:
    """删一笔往来。删完重算累计，并连带删掉它的结款事件。"""
    ledger = await session.get(Ledger, ledger_id)
    if ledger is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这笔往来", status_code=404)

    person_id = ledger.person_id
    await session.execute(
        delete(Event).where(Event.source_type == "ledger", Event.source_id == ledger_id)
    )
    await session.execute(delete(Ledger).where(Ledger.id == ledger_id))
    await session.flush()
    await recalc_totals(session, person_id)
    await session.commit()
