"""助理只读接口：token 认证 + 只读 + RLS 不被绕过。

对应 `ARCHITECTURE` 19.7.4 的三条前提。三条各自有一个用例，缺一条就等于没守住。

需要真实 Postgres（RLS 只能在真库上验）。
"""

from collections.abc import Iterator

import httpx
import pytest
from pydantic import SecretStr

from app.config import settings
from app.services.person import create_person
from tests.conftest import Account, create_user, delete_users, rls_session

TOKEN = "test-assistant-token-9371"


@pytest.fixture
def assistant_as(single_user: Account) -> Iterator[Account]:
    """把助理 token 指到这个账号，测完还原配置。"""
    original_token = settings.assistant_token
    original_name = settings.assistant_username
    settings.assistant_token = SecretStr(TOKEN)
    settings.assistant_username = single_user.username
    yield single_user
    settings.assistant_token = original_token
    settings.assistant_username = original_name


async def _person(user: Account, nickname: str) -> int:
    async with rls_session(user.id) as session:
        summary = await create_person(session, user.id, nickname=nickname, roles=["ge_jie"])
    assert summary.person.id is not None
    return summary.person.id


async def test_unconfigured_returns_503(client: httpx.AsyncClient) -> None:
    """没配 token 时是"没启用"，不是"密码错"——别说成 401 让人去查密码。"""
    original = settings.assistant_token
    settings.assistant_token = None
    try:
        response = await client.get(
            "/api/assistant/today", headers={"X-Assistant-Token": "whatever"}
        )
    finally:
        settings.assistant_token = original
    assert response.status_code == 503


async def test_wrong_token_returns_401(
    client: httpx.AsyncClient, assistant_as: Account
) -> None:
    response = await client.get("/api/assistant/today", headers={"X-Assistant-Token": "wrong"})
    assert response.status_code == 401
    response = await client.get("/api/assistant/today")
    assert response.status_code == 401


async def test_today_works_with_token(
    client: httpx.AsyncClient, assistant_as: Account
) -> None:
    await _person(assistant_as, "孟雅楠")

    response = await client.get(
        "/api/assistant/today", headers={"X-Assistant-Token": TOKEN}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert "date" in data
    assert isinstance(data["tasks"], list)


async def test_persons_only_returns_token_owner_data(
    two_users: tuple[Account, Account]
) -> None:
    """token 只绕过登录，绕不过 RLS —— 别人的数据一条都不能出现。"""
    user_a, user_b = two_users
    await _person(user_a, "我自己的人")
    await _person(user_b, "别人的客户")

    original_token = settings.assistant_token
    original_name = settings.assistant_username
    settings.assistant_token = SecretStr(TOKEN)
    settings.assistant_username = user_a.username
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app()), base_url="http://testserver"
        ) as http:
            response = await http.get(
                "/api/assistant/persons", headers={"X-Assistant-Token": TOKEN}
            )
    finally:
        settings.assistant_token = original_token
        settings.assistant_username = original_name

    assert response.status_code == 200
    names = [item["nickname"] for item in response.json()["data"]["items"]]
    assert "我自己的人" in names
    assert "别人的客户" not in names


async def test_facts_only_confirmed(
    client: httpx.AsyncClient, assistant_as: Account
) -> None:
    """事实接口只回已确认的：没点过确认的东西不能拿去回答。"""
    person_id = await _person(assistant_as, "王姐")

    response = await client.get(
        f"/api/assistant/persons/{person_id}/facts", headers={"X-Assistant-Token": TOKEN}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["nickname"] == "王姐"
    assert data["items"] == []


async def test_no_write_endpoints(client: httpx.AsyncClient, assistant_as: Account) -> None:
    """助理在"写操作要不要进确认队列"有答案之前，只能读。"""
    person_id = await _person(assistant_as, "小包老师")
    for method, path in (
        ("post", "/api/assistant/persons"),
        ("patch", f"/api/assistant/persons/{person_id}"),
        ("delete", f"/api/assistant/persons/{person_id}"),
    ):
        response = await client.request(
            method.upper(), path, headers={"X-Assistant-Token": TOKEN}
        )
        assert response.status_code in (404, 405), f"{method} {path} 不该存在"


def _app():
    from app.main import app

    return app


def test_create_user_role_helper_still_works() -> None:
    """顺手确认 conftest 的 role 参数没把默认值改坏。"""
    account = create_user("z")
    try:
        assert account.id > 0
    finally:
        delete_users(account.id)


async def test_ledger_requires_person_and_returns_totals(
    client: httpx.AsyncClient, assistant_as: Account
) -> None:
    """ledger 必须带 person_id；不带返回 400，带上才能回答"我跟这个人还差多少"。"""
    from datetime import date
    from decimal import Decimal

    from app.services import ledger as ledger_service

    person_id = await _person(assistant_as, "往来客户")
    async with rls_session(assistant_as.id) as session:
        await ledger_service.create_ledger(
            session,
            assistant_as.id,
            person_id=person_id,
            direction="out",
            kind="gift",
            amount=Decimal("300.00"),
            occurred_at=date.today(),
            note="助理读往来用例",
        )

    missing = await client.get(
        "/api/assistant/ledger", headers={"X-Assistant-Token": TOKEN}
    )
    assert missing.status_code == 400

    response = await client.get(
        f"/api/assistant/ledger?person_id={person_id}",
        headers={"X-Assistant-Token": TOKEN},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["person_id"] == person_id
    assert data["total_out"] in {"300.00", "300.000"}
    assert data["recent"][0]["note"] == "助理读往来用例"
