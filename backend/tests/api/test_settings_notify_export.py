"""设置、推送通道、导出的基本闭环。

这些接口以前完全没有用例，属于"写完了但从没被机器碰过"的部分。
只覆盖用户真正会做的动作，不追行覆盖率。
"""

import io
import json
import zipfile

import httpx
import pytest

from app.models.person import Person
from app.models.relationship import Relationship
from app.services import notify as notify_service
from tests.conftest import Account, create_user, csrf_headers, delete_users, login, rls_session


@pytest.fixture
def account() -> Account:
    user = create_user("st")
    yield user
    delete_users(user.id)


async def test_settings_toggle_persists(client: httpx.AsyncClient, account: Account) -> None:
    csrf = await login(client, account)
    headers = csrf_headers(csrf)

    initial = await client.get("/api/settings")
    assert initial.status_code == 200
    assert initial.json()["data"]["keep_original_images"] is True

    changed = await client.patch(
        "/api/settings", json={"keep_original_images": False}, headers=headers
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["keep_original_images"] is False

    again = await client.get("/api/settings")
    assert again.json()["data"]["keep_original_images"] is False


async def test_channel_requires_matching_secret(
    client: httpx.AsyncClient, account: Account
) -> None:
    csrf = await login(client, account)
    headers = csrf_headers(csrf)

    wecom = await client.post(
        "/api/notification-channels",
        json={"kind": "wecom", "name": "群"},
        headers=headers,
    )
    assert wecom.status_code == 400
    assert "webhook" in wecom.json()["error"]["message"]

    pushplus = await client.post(
        "/api/notification-channels",
        json={"kind": "pushplus", "name": "一对一"},
        headers=headers,
    )
    assert pushplus.status_code == 400
    assert "token" in pushplus.json()["error"]["message"]


async def test_channel_list_hides_secret(client: httpx.AsyncClient, account: Account) -> None:
    csrf = await login(client, account)
    headers = csrf_headers(csrf)

    created = await client.post(
        "/api/notification-channels",
        json={"kind": "pushplus", "name": "一对一", "token": "do-not-leak"},
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["data"]["has_secret"] is True
    assert "do-not-leak" not in created.text

    listing = await client.get("/api/notification-channels")
    assert listing.status_code == 200
    assert len(listing.json()["data"]) == 1
    assert "do-not-leak" not in listing.text


async def test_channel_test_and_delete(
    client: httpx.AsyncClient, account: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """测试推送不真发网络请求，把通道层换掉。"""
    csrf = await login(client, account)
    headers = csrf_headers(csrf)

    created = await client.post(
        "/api/notification-channels",
        json={"kind": "wecom", "name": "群", "webhook": "https://example.invalid/hook"},
        headers=headers,
    )
    channel_id = created.json()["data"]["id"]

    async def fake_send(channel, message) -> bool:
        assert channel.id == channel_id
        assert "测试消息" in message.text
        return True

    monkeypatch.setattr(notify_service, "send_via", fake_send)

    tested = await client.post(f"/api/notification-channels/{channel_id}/test", headers=headers)
    assert tested.status_code == 200
    assert tested.json()["data"]["ok"] is True

    deleted = await client.delete(f"/api/notification-channels/{channel_id}", headers=headers)
    assert deleted.status_code == 204

    listing = await client.get("/api/notification-channels")
    assert listing.json()["data"] == []


async def test_export_zip_contains_only_own_person(
    client: httpx.AsyncClient, account: Account
) -> None:
    async with rls_session(account.id) as session:
        person = Person(user_id=account.id, nickname="导出/测试")
        session.add(person)
        await session.flush()
        session.add(Relationship(user_id=account.id, person_id=person.id))
        await session.commit()

    await login(client, account)
    response = await client.get("/api/export")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert "index.csv" in names
        assert "README.txt" in names
        person_files = [name for name in names if name.startswith("persons/")]
        assert len(person_files) == 1
        # 昵称里的 / 不能变成 ZIP 里的目录（Zip Slip）
        assert person_files[0].count("/") == 1
        data = json.loads(archive.read(person_files[0]))
        assert data["person"]["nickname"] == "导出/测试"


async def test_settings_patch_requires_csrf(client: httpx.AsyncClient, account: Account) -> None:
    """写设置必须过 CSRF，且和全项目用同一种挂法（router dependencies）。

    以前这里是 `_ : Annotated[None, Depends(require_csrf)] = None`——功能上没错，
    但和其它写接口写法不一致：看路由签名的人会以为"这个接口不需要 CSRF"，
    新接口照抄就容易漏。统一挂到 `dependencies=[Depends(require_csrf)]`。
    """
    await login(client, account)

    blocked = await client.patch("/api/settings", json={"keep_original_images": False})
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "FORBIDDEN"

    unchanged = await client.get("/api/settings")
    assert unchanged.json()["data"]["keep_original_images"] is True
