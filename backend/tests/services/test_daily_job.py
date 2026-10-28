"""每日任务：按用户逐个开 session，生日 + 节日都能正常生成提醒。

覆盖评审 P0-1（job 没设 RLS 变量）与 P0-2（节日档位 d2 被 CHECK 拒）。
需要真实 Postgres（RLS 只能真库验证）。
"""

from datetime import date, timedelta

from sqlalchemy import select

from app.core.errors import AppError, ErrorCode
from app.jobs.daily import run_once
from app.models.event import Reminder
from app.services import event as event_service
from app.services.person import create_person
from app.services.vision import vision
from tests.conftest import Account, rls_session


async def _person(user: Account, nickname: str = "王姐") -> int:
    async with rls_session(user.id) as session:
        summary = await create_person(session, user.id, nickname=nickname, roles=["ge_jie"])
    assert summary.person.id is not None
    return summary.person.id


async def test_run_once_creates_birthday_and_festival_reminders(
    single_user: Account, monkeypatch
) -> None:
    # 没有模型 Key 时 generate_content 应降级，这里直接模拟"无 Key"，避免测试走真实网络
    async def _no_key(*args, **kwargs):
        raise AppError(ErrorCode.AI_UPSTREAM_ERROR, "no key", status_code=502)

    monkeypatch.setattr(vision, "extract", _no_key)

    person_id = await _person(single_user)
    reference = date(2026, 10, 1)

    # 生日 T-3（d3）+ 节日 T-3（d3），都在扫描窗口内
    async with rls_session(single_user.id) as session:
        await event_service.create_event(
            session,
            single_user.id,
            type="birthday",
            title="王姐生日",
            occurs_on=reference + timedelta(days=3),
            person_id=person_id,
        )
        await event_service.create_event(
            session,
            single_user.id,
            type="festival",
            title="中秋",
            occurs_on=reference + timedelta(days=3),
        )

    await run_once(today=reference)

    async with rls_session(single_user.id) as session:
        reminders = list((await session.execute(select(Reminder))).scalars().all())
        levels = {reminder.level for reminder in reminders}

    # 生日和节日各一条；档位必须是库里允许的 d3（不是被 CHECK 拒的 d2）
    assert len(reminders) == 2
    assert levels == {"d3"}


async def test_run_once_skips_festival_outside_window(single_user: Account) -> None:
    """节日 T-3 才提醒：两天后的节日不该生成提醒。"""
    reference = date(2026, 10, 1)

    async with rls_session(single_user.id) as session:
        await event_service.create_event(
            session,
            single_user.id,
            type="festival",
            title="中秋",
            occurs_on=reference + timedelta(days=2),
        )

    await run_once(today=reference)

    async with rls_session(single_user.id) as session:
        reminders = list((await session.execute(select(Reminder))).scalars().all())

    assert reminders == []
