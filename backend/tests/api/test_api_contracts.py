"""接口契约回归：把"有路由但没被 API 测试碰过"的常用读写补齐。

覆盖对象是 HTTP 层（状态码、响应字段、副作用），service 的细逻辑仍由 services/ 下的用例负责。
"""

import tempfile
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from app.models.event import Event, Reminder
from app.models.fact import Fact
from app.models.input import RawInput
from app.models.person import Person
from app.models.relationship import Relationship
from tests.conftest import Account, create_user, csrf_headers, delete_users, login, rls_session


@pytest.fixture
def account() -> Account:
    user = create_user("ac")
    yield user
    delete_users(user.id)


@pytest.fixture
def workspace() -> Iterator[Path]:
    """自己的临时目录（pytest tmp_path 在这台机器上会撞沙箱 shim）。"""
    path = Path(tempfile.mkdtemp(prefix="keeper-contracts-"))
    yield path
    for child in path.iterdir():
        child.unlink()
    path.rmdir()


async def _person(user: Account, nickname: str = "契约甲") -> int:
    async with rls_session(user.id) as session:
        person = Person(user_id=user.id, nickname=nickname)
        session.add(person)
        await session.flush()
        session.add(Relationship(user_id=user.id, person_id=person.id))
        await session.commit()
        assert person.id is not None
        return person.id


async def test_password_change_then_old_password_fails(
    client: httpx.AsyncClient, account: Account
) -> None:
    csrf = await login(client, account)
    old_password = account.password

    changed = await client.post(
        "/api/auth/password",
        json={"old_password": old_password, "new_password": "New-pass-4471"},
        headers=csrf_headers(csrf),
    )
    assert changed.status_code == 200

    logged_out = await client.post("/api/auth/logout", headers=csrf_headers(csrf))
    assert logged_out.status_code == 200

    old = await client.post(
        "/api/auth/login", json={"username": account.username, "password": old_password}
    )
    assert old.status_code == 401

    new = await client.post(
        "/api/auth/login", json={"username": account.username, "password": "New-pass-4471"}
    )
    assert new.status_code == 200
    # 还原，避免影响账号 fixture 以外的复用
    fresh_csrf = new.json()["data"]["csrf_token"]
    restored = await client.post(
        "/api/auth/password",
        json={"old_password": "New-pass-4471", "new_password": old_password},
        headers=csrf_headers(fresh_csrf),
    )
    assert restored.status_code == 200


async def test_person_update_relationship_timeline_export(
    client: httpx.AsyncClient, account: Account
) -> None:
    person_id = await _person(account)
    csrf = await login(client, account)
    headers = csrf_headers(csrf)

    identity = await client.patch(
        f"/api/persons/{person_id}",
        json={"nickname": "契约乙", "note": "改过备注"},
        headers=headers,
    )
    assert identity.status_code == 200
    assert identity.json()["data"]["nickname"] == "契约乙"

    relationship = await client.patch(
        f"/api/persons/{person_id}/relationship",
        json={
            "stage": "core",
            "taboo": ["体重"],
            "birthday_type": "solar",
            "birthday_solar": "1990-02-14",
        },
        headers=headers,
    )
    assert relationship.status_code == 200
    assert relationship.json()["data"]["stage"] == "core"
    assert relationship.json()["data"]["stage_source"] == "manual"

    detail = await client.get(f"/api/persons/{person_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["nickname"] == "契约乙"
    assert detail.json()["data"]["relationship"]["taboo"] == ["体重"]

    timeline = await client.get(f"/api/persons/{person_id}/timeline")
    assert timeline.status_code == 200
    assert isinstance(timeline.json()["data"], list)

    exported = await client.get(f"/api/persons/{person_id}/export")
    assert exported.status_code == 200
    assert exported.json()["data"]["person"]["nickname"] == "契约乙"


async def test_events_crud(client: httpx.AsyncClient, account: Account) -> None:
    csrf = await login(client, account)
    headers = csrf_headers(csrf)
    day = date.today() + timedelta(days=5)

    created = await client.post(
        "/api/events",
        json={"type": "custom", "title": "契约事件", "occurs_on": day.isoformat()},
        headers=headers,
    )
    assert created.status_code == 201
    event_id = created.json()["data"]["id"]

    listing = await client.get(f"/api/events?from={day}&to={day}")
    assert listing.status_code == 200
    assert any(item["title"] == "契约事件" for item in listing.json()["data"])

    patched = await client.patch(
        f"/api/events/{event_id}",
        json={"title": "契约事件（改）", "occurs_on": (day + timedelta(days=1)).isoformat()},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["title"] == "契约事件（改）"

    deleted = await client.delete(f"/api/events/{event_id}", headers=headers)
    assert deleted.status_code == 204


async def test_ledger_crud_recalculates_totals(client: httpx.AsyncClient, account: Account) -> None:
    person_id = await _person(account)
    csrf = await login(client, account)
    headers = csrf_headers(csrf)
    today = date.today()

    created = await client.post(
        "/api/ledger",
        json={
            "person_id": person_id,
            "direction": "out",
            "kind": "gift",
            "amount": "100.00",
            "occurred_at": today.isoformat(),
            "note": "契约往来",
        },
        headers=headers,
    )
    assert created.status_code == 201
    ledger_id = created.json()["data"]["id"]

    listing = await client.get(f"/api/persons/{person_id}/ledger")
    assert listing.status_code == 200
    assert len(listing.json()["data"]) == 1

    patched = await client.patch(
        f"/api/ledger/{ledger_id}", json={"amount": "120.00"}, headers=headers
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["amount"] == "120.00"

    detail = await client.get(f"/api/persons/{person_id}")
    assert detail.json()["data"]["relationship"]["total_out"] == "120.00"

    deleted = await client.delete(f"/api/ledger/{ledger_id}", headers=headers)
    assert deleted.status_code == 204
    detail = await client.get(f"/api/persons/{person_id}")
    assert detail.json()["data"]["relationship"]["total_out"] == "0.00"


async def test_todos_crud(client: httpx.AsyncClient, account: Account) -> None:
    csrf = await login(client, account)
    headers = csrf_headers(csrf)

    created = await client.post(
        "/api/todos",
        json={"title": "契约待办", "due_date": None, "detail": "备注"},
        headers=headers,
    )
    assert created.status_code == 201
    todo_id = created.json()["data"]["id"]

    listing = await client.get("/api/todos?status=open")
    assert listing.status_code == 200
    assert any(item["title"] == "契约待办" for item in listing.json()["data"])

    done = await client.patch(f"/api/todos/{todo_id}", json={"status": "done"}, headers=headers)
    assert done.status_code == 200
    assert done.json()["data"]["status"] == "done"
    assert done.json()["data"]["done_at"] is not None

    deleted = await client.delete(f"/api/todos/{todo_id}", headers=headers)
    assert deleted.status_code == 204
    listing = await client.get("/api/todos?status=all")
    assert all(item["id"] != todo_id for item in listing.json()["data"])


async def test_raw_input_list_get_and_file(
    client: httpx.AsyncClient, account: Account, workspace: Path
) -> None:
    image = workspace / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n fake-bytes")
    async with rls_session(account.id) as session:
        raw = RawInput(
            user_id=account.id,
            kind="screenshot",
            file_path=str(image),
            original_filename="shot.png",
            status="done",
        )
        session.add(raw)
        await session.commit()
        raw_id = raw.id

    await login(client, account)
    listing = await client.get("/api/raw-inputs?limit=10")
    assert listing.status_code == 200
    assert any(item["id"] == raw_id for item in listing.json()["data"])

    one = await client.get(f"/api/raw-inputs/{raw_id}")
    assert one.status_code == 200
    assert one.json()["data"]["original_filename"] == "shot.png"

    file = await client.get(f"/api/raw-inputs/{raw_id}/file")
    assert file.status_code == 200
    assert file.content == image.read_bytes()


async def test_confirmations_list_and_fact_delete(
    client: httpx.AsyncClient, account: Account
) -> None:
    person_id = await _person(account)
    async with rls_session(account.id) as session:
        pending = Fact(
            user_id=account.id,
            person_id=person_id,
            content="契约待确认",
            category="preference",
            confidence=0.8,
            source_type="manual",
            status="pending",
        )
        confirmed = Fact(
            user_id=account.id,
            person_id=person_id,
            content="契约已确认",
            category="preference",
            confidence=0.9,
            source_type="manual",
            status="confirmed",
        )
        session.add(pending)
        session.add(confirmed)
        await session.commit()
        pending_id, confirmed_id = pending.id, confirmed.id

    csrf = await login(client, account)
    groups = await client.get("/api/confirmations")
    assert groups.status_code == 200
    assert groups.json()["data"][0]["person_id"] == person_id
    assert any(item["id"] == pending_id for item in groups.json()["data"][0]["items"])

    deleted = await client.delete(f"/api/facts/{confirmed_id}", headers=csrf_headers(csrf))
    assert deleted.status_code == 204
    facts = await client.get(f"/api/persons/{person_id}/facts")
    assert all(item["id"] != confirmed_id for item in facts.json()["data"])


async def test_person_delete_removes_detail(client: httpx.AsyncClient, account: Account) -> None:
    person_id = await _person(account, "待删除")
    csrf = await login(client, account)

    deleted = await client.delete(f"/api/persons/{person_id}", headers=csrf_headers(csrf))
    assert deleted.status_code == 204

    detail = await client.get(f"/api/persons/{person_id}")
    assert detail.status_code == 404


async def test_persons_list_and_today_aggregate(
    client: httpx.AsyncClient, account: Account
) -> None:
    person_id = await _person(account, "列表契约")
    csrf = await login(client, account)
    headers = csrf_headers(csrf)

    await client.post(
        "/api/todos",
        json={
            "title": "今日契约待办",
            "person_id": person_id,
            "due_date": date.today().isoformat(),
        },
        headers=headers,
    )

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["data"]["username"] == account.username

    listing = await client.get("/api/persons?size=10&keyword=列表契约")
    assert listing.status_code == 200
    assert listing.json()["meta"]["total"] == 1
    assert listing.json()["data"][0]["id"] == person_id

    today = await client.get("/api/today")
    assert today.status_code == 200
    assert any(task["title"] == "今日契约待办" for task in today.json()["data"]["tasks"])


async def test_reminder_to_todo_and_duplicate_conflict(
    client: httpx.AsyncClient, account: Account
) -> None:
    async with rls_session(account.id) as session:
        person = Person(user_id=account.id, nickname="提醒契约")
        session.add(person)
        await session.flush()
        event = Event(
            user_id=account.id,
            person_id=person.id,
            type="custom",
            title="提醒转待办",
            occurs_on=date.today(),
        )
        session.add(event)
        await session.flush()
        reminder = Reminder(
            user_id=account.id,
            event_id=event.id,
            person_id=person.id,
            remind_on=date.today(),
            level="d0",
            status="pending",
            payload={"headline": "该联系了", "suggestion": "发条消息"},
        )
        session.add(reminder)
        await session.commit()
        reminder_id = reminder.id

    csrf = await login(client, account)
    headers = csrf_headers(csrf)

    first = await client.post(f"/api/reminders/{reminder_id}/to-todo", headers=headers)
    assert first.status_code == 200
    assert first.json()["data"]["title"] == "该联系了"

    second = await client.post(f"/api/reminders/{reminder_id}/to-todo", headers=headers)
    assert second.status_code == 409


async def test_confirmation_reject(client: httpx.AsyncClient, account: Account) -> None:
    person_id = await _person(account)
    async with rls_session(account.id) as session:
        fact = Fact(
            user_id=account.id,
            person_id=person_id,
            content="这条要驳回",
            category="mood",
            confidence=0.4,
            source_type="manual",
            status="pending",
        )
        session.add(fact)
        await session.commit()
        fact_id = fact.id

    csrf = await login(client, account)
    response = await client.post(f"/api/confirmations/{fact_id}/reject", headers=csrf_headers(csrf))

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "rejected"


async def test_chat_requires_text_or_image(client: httpx.AsyncClient, account: Account) -> None:
    csrf = await login(client, account)

    response = await client.post("/api/chat", data={"message": "   "}, headers=csrf_headers(csrf))

    assert response.status_code == 400
    assert "至少说一句" in response.json()["error"]["message"]


async def test_admin_list_hides_password(client: httpx.AsyncClient) -> None:
    owner = create_user("al", role="owner")
    try:
        await login(client, owner)
        response = await client.get("/api/admin/users")
        assert response.status_code == 200
        assert any(item["username"] == owner.username for item in response.json()["data"])
        assert "password" not in response.text
    finally:
        delete_users(owner.id)


async def test_timeline_limit_is_capped(client: httpx.AsyncClient, account: Account) -> None:
    """`?limit=99999` 必须被拒，而不是把一个人全部历史拉出来。

    状态码按 ARCHITECTURE §15.4：Pydantic 校验失败统一 400 + VALIDATION_ERROR
    （不是框架默认的 422），这样前端只用一套错误处理。
    """
    person_id = await _person(account)
    await login(client, account)

    too_big = await client.get(f"/api/persons/{person_id}/timeline", params={"limit": 99999})
    assert too_big.status_code == 400
    assert too_big.json()["error"]["code"] == "VALIDATION_ERROR"

    zero = await client.get(f"/api/persons/{person_id}/timeline", params={"limit": 0})
    assert zero.status_code == 400

    # 封顶值本身必须能用，别把自己封死了
    boundary = await client.get(f"/api/persons/{person_id}/timeline", params={"limit": 200})
    assert boundary.status_code == 200
