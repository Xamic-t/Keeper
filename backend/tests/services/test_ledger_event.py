"""往来、日历、农历节日的 service 单测。

覆盖 specs/07-ledger.md 与 specs/08-events.md 的验收标准。
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError, ErrorCode
from app.models.event import Event
from app.models.relationship import Relationship
from app.services import event as event_service
from app.services import festival as festival_service
from app.services import ledger as ledger_service
from app.services.person import create_person
from tests.conftest import Account, rls_session


async def _person(user: Account, nickname: str = "王姐") -> int:
    async with rls_session(user.id) as session:
        summary = await create_person(session, user.id, nickname=nickname, roles=["ge_jie"])
    assert summary.person.id is not None
    return summary.person.id


async def _relationship(session, person_id: int) -> Relationship:
    result = await session.execute(select(Relationship).where(Relationship.person_id == person_id))
    return result.scalar_one()


async def test_create_ledger_recalculates_totals(single_user: Account) -> None:
    person_id = await _person(single_user)

    async with rls_session(single_user.id) as session:
        await ledger_service.create_ledger(
            session,
            single_user.id,
            person_id=person_id,
            direction="in",
            kind="payment",
            amount=Decimal("520.00"),
            occurred_at=date(2026, 3, 1),
        )

    async with rls_session(single_user.id) as session:
        relationship = await _relationship(session, person_id)
        assert relationship.total_in == Decimal("520.00")
        assert relationship.total_out == Decimal("0.00")


async def test_delete_ledger_rolls_totals_back(single_user: Account) -> None:
    person_id = await _person(single_user)

    async with rls_session(single_user.id) as session:
        item = await ledger_service.create_ledger(
            session,
            single_user.id,
            person_id=person_id,
            direction="out",
            kind="gift",
            amount=Decimal("300.00"),
            occurred_at=date(2026, 3, 1),
        )
        ledger_id = item.id

    async with rls_session(single_user.id) as session:
        await ledger_service.delete_ledger(session, ledger_id)

    async with rls_session(single_user.id) as session:
        relationship = await _relationship(session, person_id)
        assert relationship.total_out == Decimal("0.00")


async def test_due_date_creates_event_and_settle_removes_it(single_user: Account) -> None:
    person_id = await _person(single_user)

    async with rls_session(single_user.id) as session:
        item = await ledger_service.create_ledger(
            session,
            single_user.id,
            person_id=person_id,
            direction="out",
            kind="payment",
            amount=Decimal("800.00"),
            occurred_at=date(2026, 3, 1),
            due_date=date(2026, 3, 15),
        )
        ledger_id = item.id

    async with rls_session(single_user.id) as session:
        events = (
            (
                await session.execute(
                    select(Event).where(Event.source_type == "ledger", Event.source_id == ledger_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].type == "payment_due"
        assert events[0].occurs_on == date(2026, 3, 15)

    async with rls_session(single_user.id) as session:
        await ledger_service.update_ledger(session, ledger_id, {"settled_at": date(2026, 3, 14)})

    async with rls_session(single_user.id) as session:
        events = (
            (
                await session.execute(
                    select(Event).where(Event.source_type == "ledger", Event.source_id == ledger_id)
                )
            )
            .scalars()
            .all()
        )
        assert events == []


async def test_ledger_amount_must_be_positive(single_user: Account) -> None:
    person_id = await _person(single_user)

    async with rls_session(single_user.id) as session:
        with pytest.raises(AppError):
            await ledger_service.create_ledger(
                session,
                single_user.id,
                person_id=person_id,
                direction="in",
                kind="payment",
                amount=Decimal("0"),
                occurred_at=date(2026, 3, 1),
            )


async def test_due_date_before_occurred_at_is_rejected(single_user: Account) -> None:
    person_id = await _person(single_user)

    async with rls_session(single_user.id) as session:
        with pytest.raises(AppError) as error:
            await ledger_service.create_ledger(
                session,
                single_user.id,
                person_id=person_id,
                direction="in",
                kind="payment",
                amount=Decimal("100"),
                occurred_at=date(2026, 3, 10),
                due_date=date(2026, 3, 1),
            )
    assert error.value.code is ErrorCode.VALIDATION_ERROR


def test_lunar_to_solar_matches_known_dates() -> None:
    """农历 2026 年正月初一 = 公历 2026-02-17（春节）。"""
    assert festival_service.lunar_to_solar(1, 1, False, 2026) == date(2026, 2, 17)


def test_lunar_leap_month_missing_returns_none() -> None:
    """2026 年没有闰月，要闰月生日就返回 None（跳过，不报错）。"""
    assert festival_service.lunar_to_solar(4, 1, True, 2026) is None


def test_get_festivals_contains_spring_festival() -> None:
    names = {item.name for item in festival_service.get_festivals(2026)}

    assert "春节" in names
    assert "中秋" in names
    assert "冬至" in names


async def test_list_events_returns_no_personal_events_for_empty_range(
    single_user: Account,
) -> None:
    """没有个人事件时，日历里只剩公共节日（不是 404）。"""
    async with rls_session(single_user.id) as session:
        items = await event_service.list_events(session, date(2030, 1, 1), date(2030, 1, 31))

    assert all(item.type == "festival" for item in items)


async def test_create_birthday_event_writes_relationship(single_user: Account) -> None:
    person_id = await _person(single_user)

    async with rls_session(single_user.id) as session:
        await event_service.create_event(
            session,
            single_user.id,
            type="birthday",
            title="王姐生日",
            occurs_on=date(2027, 3, 12),
            person_id=person_id,
            lunar={"month": 2, "day": 13, "is_leap": False},
        )

    async with rls_session(single_user.id) as session:
        relationship = await _relationship(session, person_id)
        assert relationship.birthday_type == "lunar"
        assert relationship.birthday_lunar == {"month": 2, "day": 13, "is_leap": False}


async def test_festival_event_cannot_have_person(single_user: Account) -> None:
    person_id = await _person(single_user)

    async with rls_session(single_user.id) as session:
        with pytest.raises(AppError):
            await event_service.create_event(
                session,
                single_user.id,
                type="festival",
                title="春节",
                occurs_on=date(2027, 2, 1),
                person_id=person_id,
            )


async def test_delete_ledger_missing_returns_404(single_user: Account) -> None:
    async with rls_session(single_user.id) as session:
        with pytest.raises(AppError) as error:
            await ledger_service.delete_ledger(session, 999_999_999)
    assert error.value.code is ErrorCode.NOT_FOUND


async def test_ledger_rows_are_visible_only_to_owner(two_users: tuple[Account, Account]) -> None:
    user_a, user_b = two_users
    person_id = await _person(user_a)

    async with rls_session(user_a.id) as session:
        await ledger_service.create_ledger(
            session,
            user_a.id,
            person_id=person_id,
            direction="in",
            kind="tip",
            amount=Decimal("88"),
            occurred_at=date(2026, 5, 1),
        )

    async with rls_session(user_b.id) as session:
        with pytest.raises(AppError) as error:
            await ledger_service.list_ledger(session, person_id)

    assert error.value.code is ErrorCode.NOT_FOUND


async def test_ledger_row_count_matches(single_user: Account) -> None:
    person_id = await _person(single_user)

    async with rls_session(single_user.id) as session:
        for amount in ("10.00", "20.00", "30.00"):
            await ledger_service.create_ledger(
                session,
                single_user.id,
                person_id=person_id,
                direction="out",
                kind="gift",
                amount=Decimal(amount),
                occurred_at=date(2026, 5, 1),
            )
        rows, total = await ledger_service.list_ledger(session, person_id)

    assert total == 3
    assert len(rows) == 3
