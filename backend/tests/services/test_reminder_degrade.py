"""提醒内容的三条降级线：模型挂了、模型没给依据、没有推送通道。

为什么单独测：这三条都是"出问题时才走"的路径，正常演示永远碰不到。
一旦哪条写错，用户看到的是"明天生日，建议送茶叶"却没有一句依据，
或者更糟——什么都没收到还以为没事。

规则来自 ARCHITECTURE 17 与 specs/09：**宁可只说日期，也不给没有来由的建议**。
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.errors import AppError, ErrorCode
from app.models.event import Reminder
from app.models.notification import NotificationChannel
from app.services import notify
from app.services import reminder as reminder_service
from app.services.event import create_event
from app.services.person import create_person
from app.services.vision import ModelReply, vision
from tests.conftest import Account, rls_session


def _reply(parsed: dict[str, object]) -> ModelReply:
    return ModelReply(parsed=parsed, raw="{}", provider="fake", model="fake-model")


async def _seed(user: Account, *, days_left: int = 3) -> tuple[int, int]:
    """一个人 + 一个生日事件，返回 (person_id, event_id)。"""
    async with rls_session(user.id) as session:
        summary = await create_person(session, user.id, nickname="王姐", roles=["ge_jie"])
        person_id = summary.person.id or 0
        event = await create_event(
            session,
            user.id,
            type="birthday",
            title="王姐生日",
            occurs_on=date(2026, 11, 6),
            person_id=person_id,
        )
        return person_id, event.id or 0


async def _event(user: Account, event_id: int):
    async with rls_session(user.id) as session:
        from app.models.event import Event

        return await session.get(Event, event_id)


async def test_model_failure_falls_back_to_calendar_only(
    single_user: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型不可用 → 降级内容只报日期，但仍要有一条「依据」（日历本身）。"""
    person_id, event_id = await _seed(single_user)
    event = await _event(single_user, event_id)

    async def _fail(**kwargs: object) -> ModelReply:
        raise AppError(ErrorCode.AI_UPSTREAM_ERROR, "upstream down", status_code=502)

    monkeypatch.setattr(vision, "extract", _fail)

    async with rls_session(single_user.id) as session:
        payload = await reminder_service.generate_content(
            session, event, person_id, "d3", days_left=3
        )

    assert payload["degraded"] is True
    assert payload["reasons"], "降级也要有依据，不能给一句没由来的建议"
    assert "还有 3 天" in payload["headline"]
    assert payload["script"] == ""


async def test_model_without_reasons_is_downgraded(
    single_user: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型给了建议但没给依据 → 视为不可信，整条降级。"""
    person_id, event_id = await _seed(single_user)
    event = await _event(single_user, event_id)

    async def _no_reasons(**kwargs: object) -> ModelReply:
        return _reply({"headline": "王姐生日", "suggestion": "送白茶"})

    monkeypatch.setattr(vision, "extract", _no_reasons)

    async with rls_session(single_user.id) as session:
        payload = await reminder_service.generate_content(
            session, event, person_id, "d3", days_left=3
        )

    assert payload["degraded"] is True
    assert "送白茶" not in payload["suggestion"], "没有依据的建议不许发出去"


async def test_model_with_reasons_is_kept(
    single_user: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """有依据 → 正常采用，reasons 最多留 5 条（推送消息不能太长）。"""
    person_id, event_id = await _seed(single_user)
    event = await _event(single_user, event_id)

    async def _ok(**kwargs: object) -> ModelReply:
        return _reply(
            {
                "headline": "王姐生日还有 3 天",
                "suggestion": "送一盒白茶",
                "reasons": [f"依据{i}" for i in range(8)],
                "script": "王姐，下周您生日，我给您留了盒白茶",
            }
        )

    monkeypatch.setattr(vision, "extract", _ok)

    async with rls_session(single_user.id) as session:
        payload = await reminder_service.generate_content(
            session, event, person_id, "d3", days_left=3
        )

    assert payload["degraded"] is False
    assert payload["suggestion"] == "送一盒白茶"
    assert len(payload["reasons"]) == 5
    assert payload["script"].startswith("王姐")


async def _make_reminder(user: Account, person_id: int, event_id: int) -> Reminder:
    async with rls_session(user.id) as session:
        reminder = Reminder(
            user_id=user.id,
            event_id=event_id,
            person_id=person_id,
            remind_on=date(2026, 11, 3),
            level="d3",
            status="pending",
            payload={
                "headline": "王姐生日",
                "suggestion": "送茶",
                "reasons": ["日历里有这件事"],
            },
        )
        session.add(reminder)
        await session.commit()
        return reminder


@pytest.fixture
def no_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    """把"没配通道"钉死，别让本机 .env 里的 webhook 影响用例。"""

    async def _none(session: object, user_id: int) -> list[NotificationChannel]:
        return []

    monkeypatch.setattr(notify, "effective_channels", _none)


async def test_send_without_channel_stays_pending(single_user: Account, no_channels: None) -> None:
    """没配通道：状态留 pending + 写清原因，**不能写 failed**。

    理由：今日页只显示发出去了的提醒；写成 failed 就等于
    "既不推送也不显示"，用户会以为没事，其实生日就在明天。
    """
    person_id, event_id = await _seed(single_user)
    reminder = await _make_reminder(single_user, person_id, event_id)

    async with rls_session(single_user.id) as session:
        sent = await reminder_service.send(session, reminder)

    assert sent is False
    async with rls_session(single_user.id) as session:
        stored = (
            await session.execute(select(Reminder).where(Reminder.id == reminder.id))
        ).scalar_one()
        assert stored.status == "pending"
        assert stored.channel is None
        assert "没有配置推送通道" in (stored.last_error or "")


async def test_send_failure_keeps_reason_and_no_fake_sent(
    single_user: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配了通道但发不出去：状态仍 pending，原因写清，不伪造 sent。"""
    person_id, event_id = await _seed(single_user)
    reminder = await _make_reminder(single_user, person_id, event_id)
    channel = NotificationChannel(
        id=1, user_id=single_user.id, kind="wecom", name="企微", priority=1, config={"webhook": "x"}
    )

    async def _channels(session: object, user_id: int) -> list[NotificationChannel]:
        return [channel]

    async def _false_push(session: object, user_id: int, message: object) -> None:
        return None

    monkeypatch.setattr(notify, "effective_channels", _channels)
    monkeypatch.setattr(notify, "push", _false_push)

    async with rls_session(single_user.id) as session:
        assert await reminder_service.send(session, reminder) is False

    async with rls_session(single_user.id) as session:
        stored = (
            await session.execute(select(Reminder).where(Reminder.id == reminder.id))
        ).scalar_one()
        assert stored.status == "pending"
        assert "所有通道都发送失败" in (stored.last_error or "")


async def test_send_success_marks_sent_with_channel(
    single_user: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成功路径也要钉住：status=sent、channel=实际通道名、sent_at 有值。"""
    person_id, event_id = await _seed(single_user)
    reminder = await _make_reminder(single_user, person_id, event_id)
    channel = NotificationChannel(
        id=1, user_id=single_user.id, kind="wecom", name="企微", priority=1, config={"webhook": "x"}
    )

    async def _channels(session: object, user_id: int) -> list[NotificationChannel]:
        return [channel]

    async def _push_ok(session: object, user_id: int, message: object) -> str:
        return "企微"

    monkeypatch.setattr(notify, "effective_channels", _channels)
    monkeypatch.setattr(notify, "push", _push_ok)

    async with rls_session(single_user.id) as session:
        assert await reminder_service.send(session, reminder) is True

    async with rls_session(single_user.id) as session:
        stored = (
            await session.execute(select(Reminder).where(Reminder.id == reminder.id))
        ).scalar_one()
        assert stored.status == "sent"
        assert stored.channel == "企微"
        assert stored.sent_at is not None
        assert stored.last_error is None


async def test_env_webhook_is_used_when_no_channel_in_db(
    single_user: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """库表为空时退回 .env 的 webhook：单人部署不必先建通道。"""
    monkeypatch.setattr(settings, "wecom_webhook", "https://example.invalid/hook")
    monkeypatch.setattr(settings, "pushplus_token", None)

    async with rls_session(single_user.id) as session:
        channels = await notify.effective_channels(session, single_user.id)

    assert [channel.kind for channel in channels] == ["wecom"]
    assert channels[0].name == "环境变量·企微"


async def test_no_env_no_channel_means_empty(
    single_user: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两边都没有 → 空列表（不是 None，调用方不用判空两种形态）。"""
    monkeypatch.setattr(settings, "wecom_webhook", None)
    monkeypatch.setattr(settings, "pushplus_token", None)

    async with rls_session(single_user.id) as session:
        assert await notify.effective_channels(session, single_user.id) == []


async def test_scan_window_uses_business_timezone_day(single_user: Account) -> None:
    """T-3 才生成提醒；T-4 与 T-2 都不该生成（档位表钉住，防止有人把窗口扩大）。"""
    person_id, event_id = await _seed(single_user, days_left=4)

    async with rls_session(single_user.id) as session:
        drafts = await reminder_service.scan_due_events(session, date(2026, 11, 2))

    assert drafts == [], "T-4 不该提醒"

    async with rls_session(single_user.id) as session:
        drafts = await reminder_service.scan_due_events(session, date(2026, 11, 3))

    assert [draft.level for draft in drafts] == ["d3"]
    assert drafts[0].event_id == event_id
    assert drafts[0].person_id == person_id
    assert drafts[0].remind_on == date(2026, 11, 3)


async def test_completed_event_is_not_scanned(single_user: Account) -> None:
    """已办的事件不再提醒——否则用户点完"已完成"第二天又被推一次。"""
    from app.models.event import Event

    _, event_id = await _seed(single_user, days_left=3)
    async with rls_session(single_user.id) as session:
        event = await session.get(Event, event_id)
        event.is_done = True
        session.add(event)
        await session.commit()

    async with rls_session(single_user.id) as session:
        drafts = await reminder_service.scan_due_events(session, date(2026, 11, 3))

    assert drafts == []


def test_level_names_are_within_allowed_check_values() -> None:
    """档位必须落在 reminders 表的 CHECK 白名单里（d0/d1/d2/d3/d7）。"""
    assert set(reminder_service.LEVEL_NAMES.values()) <= {"d0", "d1", "d2", "d3", "d7"}


def test_scan_horizon_is_seven_days() -> None:
    """扫描窗口是 7 天：改这个值会让"提前一周提醒"的承诺失效。"""
    assert reminder_service.SCAN_DAYS == 7
    assert timedelta(days=reminder_service.SCAN_DAYS) == timedelta(days=7)
