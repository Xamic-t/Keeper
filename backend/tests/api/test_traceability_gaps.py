"""评审追溯矩阵指出的后端契约测试缺口。

这里不是重复 service 单测，而是钉住「文档有 / 前端已调用 / 但之前没有测试」的边界：
提醒 done/regenerate、综述重写、标签筛选、确认改归属。
"""

from datetime import date, timedelta

import httpx
import pytest

from app.models.event import Event, Reminder
from app.models.fact import Fact, Tag
from app.models.person import Person
from app.services import profile as profile_service
from tests.conftest import Account, create_user, csrf_headers, delete_users, login, rls_session


@pytest.fixture
def account() -> Account:
    user = create_user("tg")
    yield user
    delete_users(user.id)


async def _person(user: Account, nickname: str) -> int:
    async with rls_session(user.id) as session:
        person = Person(user_id=user.id, nickname=nickname)
        session.add(person)
        await session.commit()
        assert person.id is not None
        return person.id


async def _custom_reminder(user: Account) -> int:
    """不挂人的自定义事件 → regenerate 走降级路径，不花模型额度。"""
    async with rls_session(user.id) as session:
        event = Event(
            user_id=user.id,
            type="custom",
            title="评审会",
            occurs_on=date.today() + timedelta(days=2),
        )
        session.add(event)
        await session.flush()
        reminder = Reminder(
            user_id=user.id,
            event_id=event.id,
            remind_on=event.occurs_on,
            level="d0",
            payload={"headline": "旧建议", "suggestion": "旧内容"},
            status="pending",
        )
        session.add(reminder)
        await session.commit()
        assert reminder.id is not None
        return reminder.id


async def test_reminder_done_is_idempotent(client: httpx.AsyncClient, account: Account) -> None:
    reminder_id = await _custom_reminder(account)
    csrf = await login(client, account)
    headers = csrf_headers(csrf)

    first = await client.post(f"/api/reminders/{reminder_id}/done", headers=headers)
    second = await client.post(f"/api/reminders/{reminder_id}/done", headers=headers)

    assert first.status_code == 200
    assert first.json()["data"]["status"] == "clicked"
    assert second.status_code == 200
    assert second.json()["data"]["status"] == "clicked"

    async with rls_session(account.id) as session:
        fresh = await session.get(Reminder, reminder_id)
        assert fresh is not None
        assert fresh.status == "clicked"


async def test_reminder_regenerate_replaces_payload(
    client: httpx.AsyncClient, account: Account
) -> None:
    reminder_id = await _custom_reminder(account)
    csrf = await login(client, account)

    response = await client.post(
        f"/api/reminders/{reminder_id}/regenerate", headers=csrf_headers(csrf)
    )

    assert response.status_code == 200
    payload = response.json()["data"]["payload"]
    assert payload["headline"] != "旧建议"
    assert payload["degraded"] is True

    async with rls_session(account.id) as session:
        fresh = await session.get(Reminder, reminder_id)
        assert fresh is not None
        assert fresh.payload is not None
        assert fresh.payload["degraded"] is True


async def test_summary_rewrite_endpoint(
    client: httpx.AsyncClient, account: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    person_id = await _person(account, "综述测试")

    async def fake_rewrite(session, person_id_arg: int) -> str:
        assert person_id_arg == person_id
        return "这是重写后的综述"

    monkeypatch.setattr(profile_service, "rewrite_summary", fake_rewrite)
    csrf = await login(client, account)

    response = await client.post(
        f"/api/persons/{person_id}/summary/rewrite", headers=csrf_headers(csrf)
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "person_id": person_id,
        "summary": "这是重写后的综述",
        "stale": True,
    }


async def test_tags_filter_by_key(client: httpx.AsyncClient, account: Account) -> None:
    person_id = await _person(account, "标签测试")
    async with rls_session(account.id) as session:
        session.add(Tag(user_id=account.id, person_id=person_id, key="preference", value="白茶"))
        await session.commit()

    await login(client, account)
    response = await client.get("/api/tags?key=preference")

    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 1
    assert rows[0]["person_id"] == person_id
    assert rows[0]["value"] == "白茶"


async def test_confirmation_patch_reassigns_and_confirms(
    client: httpx.AsyncClient, account: Account
) -> None:
    first_id = await _person(account, "归属甲")
    second_id = await _person(account, "归属乙")
    async with rls_session(account.id) as session:
        fact = Fact(
            user_id=account.id,
            person_id=first_id,
            content="原始内容",
            category="structure",
            confidence=0.7,
            source_type="manual",
        )
        session.add(fact)
        await session.commit()
        assert fact.id is not None
        fact_id = fact.id

    csrf = await login(client, account)
    response = await client.patch(
        f"/api/confirmations/{fact_id}",
        json={"person_id": second_id, "content": "改后的内容", "category": "preference"},
        headers=csrf_headers(csrf),
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "confirmed"

    async with rls_session(account.id) as session:
        fresh = await session.get(Fact, fact_id)
        assert fresh is not None
        assert fresh.person_id == second_id
        assert fresh.content == "改后的内容"
        assert fresh.category == "preference"
        assert fresh.status == "confirmed"
