"""提醒没发出去时，用户仍然要能看到它。

背景（这是今晚最要紧的一个 bug）：
`send()` 在没配推送通道时把 status 写成 `failed`，而今日页只查 `sent`。
结果就是：提醒写了、内容生成了、数据库里有行，但用户在界面上**什么都看不到**，
连"转成待办"都会 400（因为只允许 sent/clicked）。
比不提醒更糟：用户以为没事。

修法：失败一律留 `pending` + `last_error`。语义是"备好了但没发出去"，
今日页照常显示并标「未发送」。
"""

from datetime import date, timedelta

import httpx
import pytest

from app.models.event import Event, Reminder
from app.models.person import Person
from app.services import reminder as reminder_service
from tests.conftest import Account, create_user, csrf_headers, delete_users, login, rls_session


@pytest.fixture
def account() -> Account:
    user = create_user("rm")
    yield user
    delete_users(user.id)


async def _make_reminder(user: Account, *, status: str = "pending", last_error: str | None = None):
    """造一条"今天到期"的提醒。直接写库，不花模型额度。"""
    today = date.today()
    async with rls_session(user.id) as session:
        person = Person(user_id=user.id, nickname="提醒测试")
        session.add(person)
        await session.flush()

        event = Event(
            user_id=user.id,
            person_id=person.id,
            type="birthday",
            title="提醒测试生日",
            occurs_on=today,
            rrule="FREQ=YEARLY",
        )
        session.add(event)
        await session.flush()

        reminder = Reminder(
            user_id=user.id,
            event_id=event.id,
            person_id=person.id,
            remind_on=today,
            level="d0",
            payload={"headline": "明天是提醒测试生日", "suggestion": "提前订个蛋糕"},
            status=status,
            last_error=last_error,
        )
        session.add(reminder)
        await session.commit()
        return reminder.id, event.id


async def test_no_channel_keeps_pending_and_records_reason(account: Account) -> None:
    """没配通道 → pending + last_error，不是 failed。"""
    reminder_id, _ = await _make_reminder(account, status="pending")

    async with rls_session(account.id) as session:
        reminder = await session.get(Reminder, reminder_id)
        assert reminder is not None
        sent_ok = await reminder_service.send(session, reminder)

    assert sent_ok is False

    async with rls_session(account.id) as session:
        fresh = await session.get(Reminder, reminder_id)
        assert fresh is not None
        assert fresh.status == "pending", "失败不能写 failed，否则今日页看不到"
        assert fresh.last_error is not None
        assert "通道" in fresh.last_error


async def test_today_shows_unsent_reminder(client: httpx.AsyncClient, account: Account) -> None:
    await _make_reminder(account, status="pending", last_error="没有配置推送通道")
    await login(client, account)

    response = await client.get("/api/today")
    assert response.status_code == 200

    tasks = response.json()["data"]["tasks"]
    reminders = [task for task in tasks if task["type"] == "reminder"]
    assert reminders, "没发出去的提醒也必须出现在今日页"
    assert reminders[0]["unsent"] is True
    assert "通道" in reminders[0]["unsent_reason"]


async def test_unsent_reminder_can_become_todo(client: httpx.AsyncClient, account: Account) -> None:
    reminder_id, _ = await _make_reminder(account, status="pending")
    csrf_token = await login(client, account)

    response = await client.post(
        f"/api/reminders/{reminder_id}/to-todo", headers=csrf_headers(csrf_token)
    )

    # 关键是"不是 400"。这个路由没有显式 status_code，所以是 200，
    # 不为了好看改成 201（改状态码对前端没有价值，只会让旧调用方多一条分支）
    assert response.status_code == 200
    assert response.json()["data"]["source"] == "reminder"


async def test_sent_reminder_is_not_marked_unsent(
    client: httpx.AsyncClient, account: Account
) -> None:
    """发出去的不该被标成未发送 —— 反例，防止把判断写反。"""
    await _make_reminder(account, status="sent")
    await login(client, account)

    response = await client.get("/api/today")
    reminders = [t for t in response.json()["data"]["tasks"] if t["type"] == "reminder"]
    assert reminders
    assert reminders[0]["unsent"] is False
    assert reminders[0]["unsent_reason"] is None


async def test_old_reminder_outside_window_is_hidden(
    client: httpx.AsyncClient, account: Account
) -> None:
    """窗口在 -1 天到 +3 天之间。上周的 pending 不该一直挂在今日页。"""
    async with rls_session(account.id) as session:
        person = Person(user_id=account.id, nickname="旧提醒")
        session.add(person)
        await session.flush()
        event = Event(
            user_id=account.id,
            person_id=person.id,
            type="birthday",
            title="旧提醒生日",
            occurs_on=date.today() - timedelta(days=30),
        )
        session.add(event)
        await session.flush()
        session.add(
            Reminder(
                user_id=account.id,
                event_id=event.id,
                person_id=person.id,
                remind_on=date.today() - timedelta(days=30),
                level="d0",
                payload={"headline": "很久以前"},
                status="pending",
            )
        )
        await session.commit()

    await login(client, account)
    response = await client.get("/api/today")
    headlines = [t["title"] for t in response.json()["data"]["tasks"]]
    assert "很久以前" not in headlines
