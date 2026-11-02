"""五张表的跨账号隔离。

以前只有 persons / assistant 的 RLS 有用例，facts / tags / events / reminders /
notification_channels 全靠"查询时没写 user_id，数据库会挡"这个信念。
这里每张表造一条 A 的数据，再用 B 的身份从接口和 RLS session 各查一次。

不是测 404 就完事：列表接口漏数据时是 200 + 空数组，更容易被忽略。
"""

from datetime import date

from sqlalchemy import select

from app.models.event import Event, Reminder
from app.models.fact import Fact, Tag
from app.models.notification import NotificationChannel
from app.models.person import Person
from tests.conftest import Account, login, new_client, rls_session


async def _seed(user: Account) -> dict[str, int]:
    async with rls_session(user.id) as session:
        person = Person(user_id=user.id, nickname="隔离甲")
        session.add(person)
        await session.flush()

        fact = Fact(
            user_id=user.id,
            person_id=person.id,
            content="A 的私密事实",
            category="preference",
            confidence=0.9,
            source_type="manual",
            status="confirmed",
        )
        session.add(fact)
        await session.flush()

        tag = Tag(user_id=user.id, person_id=person.id, key="preference", value="白茶")
        session.add(tag)

        event = Event(
            user_id=user.id,
            person_id=person.id,
            type="custom",
            title="A 的私密事件",
            occurs_on=date(2027, 1, 10),
        )
        session.add(event)
        await session.flush()

        reminder = Reminder(
            user_id=user.id,
            event_id=event.id,
            person_id=person.id,
            remind_on=date(2027, 1, 10),
            level="d0",
            status="sent",
            payload={"headline": "A 的提醒"},
        )
        session.add(reminder)
        await session.flush()

        channel = NotificationChannel(
            user_id=user.id,
            kind="pushplus",
            name="A 的通道",
            config={"token": "secret-token"},
        )
        session.add(channel)
        await session.commit()

        return {
            "person": person.id,
            "fact": fact.id,
            "event": event.id,
            "reminder": reminder.id,
            "channel": channel.id,
        }


async def test_b_cannot_read_a_rows_through_rls(two_users: tuple[Account, Account]) -> None:
    user_a, user_b = two_users
    ids = await _seed(user_a)

    async with rls_session(user_b.id) as session:
        for model in (Fact, Tag, Event, Reminder, NotificationChannel):
            rows = (
                await session.execute(select(model).where(model.user_id == user_a.id))
            ).scalars().all()
            assert list(rows) == [], f"{model.__tablename__} 泄露了 A 的行"

    assert ids["person"] > 0


async def test_b_cannot_read_a_facts_tags_events_reminders_channels(
    two_users: tuple[Account, Account],
) -> None:
    user_a, user_b = two_users
    ids = await _seed(user_a)

    async with new_client() as client:
        await login(client, user_b)

        facts = await client.get(f"/api/persons/{ids['person']}/facts")
        assert facts.status_code == 200
        assert facts.json()["data"] == []

        tags = await client.get(f"/api/tags?person_id={ids['person']}")
        assert tags.status_code == 200
        assert tags.json()["data"] == []

        events = await client.get("/api/events?from=2027-01-01&to=2027-01-31")
        assert events.status_code == 200
        assert all(item["title"] != "A 的私密事件" for item in events.json()["data"])

        reminders = await client.get("/api/reminders")
        assert reminders.status_code == 200
        assert all(item["id"] != ids["reminder"] for item in reminders.json()["data"])

        channels = await client.get("/api/notification-channels")
        assert channels.status_code == 200
        assert channels.json()["data"] == []


async def test_b_cannot_write_a_fact_or_delete_channel(
    two_users: tuple[Account, Account],
) -> None:
    user_a, user_b = two_users
    ids = await _seed(user_a)

    async with new_client() as client:
        csrf = await login(client, user_b)
        headers = {"X-CSRF-Token": csrf}

        patch = await client.patch(
            f"/api/facts/{ids['fact']}", json={"content": "B 改的"}, headers=headers
        )
        assert patch.status_code == 404

        delete = await client.delete(
            f"/api/notification-channels/{ids['channel']}", headers=headers
        )
        assert delete.status_code == 404


async def test_b_gets_empty_zip(two_users: tuple[Account, Account]) -> None:
    """导出也必须隔离，否则一次 ZIP 下载就把所有人的档案带走。"""
    user_a, user_b = two_users
    await _seed(user_a)

    async with new_client() as client:
        await login(client, user_b)
        response = await client.get("/api/export")

    assert response.status_code == 200
    assert b"A \xe7\x9a\x84\xe7\xa7\x81\xe5\xaf\x86" not in response.content
