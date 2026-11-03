"""管理后台概览：账号状态 + 每个账号的行为量。

三个必须钉住的点：
  ① 只有 owner 能看（member 403）——这是后台，不是业务页
  ② 数字要是**真的**：跨账号聚合不能只数自己（RLS 会挡住，容易写成"看起来有数其实全 0"）
  ③ **只出数量，不出内容**：后台是看"用了多少"，不是看"记了什么"
"""

from datetime import date

import httpx
import pytest

from app.models.fact import Fact
from app.models.person import Person
from app.models.todo import Todo
from tests.conftest import Account, create_user, csrf_headers, delete_users, login, rls_session


@pytest.fixture
def owner_account() -> Account:
    user = create_user("ao", role="owner")
    yield user
    delete_users(user.id)


async def _seed(user: Account, *, persons: int, facts: int, todos: int) -> None:
    async with rls_session(user.id) as session:
        for index in range(persons):
            person = Person(user_id=user.id, nickname=f"{user.username}-人{index}")
            session.add(person)
            await session.flush()
            for fact_index in range(facts):
                session.add(
                    Fact(
                        user_id=user.id,
                        person_id=person.id,
                        content=f"私密内容 {index}-{fact_index}",
                        category="preference",
                        confidence=0.8,
                        source_type="manual",
                        status="confirmed" if fact_index == 0 else "pending",
                    )
                )
        for index in range(todos):
            session.add(
                Todo(
                    user_id=user.id,
                    title=f"{user.username}-待办{index}",
                    source="manual",
                    status="open",
                    due_date=date(2026, 11, 3),
                )
            )
        await session.commit()


async def test_member_cannot_open_admin_overview(
    client: httpx.AsyncClient, single_user: Account
) -> None:
    await login(client, single_user)
    response = await client.get("/api/admin/overview")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_overview_counts_every_account_not_just_own(
    client: httpx.AsyncClient, owner_account: Account
) -> None:
    """跨账号聚合：别人的数据也要数到，不能只数 owner 自己的。"""
    member = create_user("ao-m")
    try:
        await _seed(member, persons=2, facts=2, todos=3)
        await _seed(owner_account, persons=1, facts=1, todos=1)

        await login(client, owner_account)
        response = await client.get("/api/admin/overview")
        assert response.status_code == 200
        data = response.json()["data"]

        by_name = {row["username"]: row for row in data["users"]}
        assert by_name[member.username]["persons"] == 2
        assert by_name[member.username]["facts"] == 4
        assert by_name[member.username]["todos_open"] == 3
        # 每人 2 条事实里 1 条 pending
        assert by_name[member.username]["pending_confirmations"] == 2

        assert by_name[owner_account.username]["persons"] == 1
        assert data["totals"]["persons"] >= 3
        assert data["accounts"]["total"] >= 2
        assert data["system"]["migration"], "要能看到当前迁移版本"
    finally:
        delete_users(member.id)


async def test_overview_never_returns_content(
    client: httpx.AsyncClient, owner_account: Account
) -> None:
    """后台只看数量：响应里不许出现事实正文 / 待办标题。"""
    member = create_user("ao-x")
    try:
        await _seed(member, persons=1, facts=1, todos=1)
        await login(client, owner_account)
        raw = (await client.get("/api/admin/overview")).text
        assert "私密内容" not in raw, "事实正文泄漏到后台接口了"
        assert "待办0" not in raw, "待办标题泄漏到后台接口了"
    finally:
        delete_users(member.id)


async def test_overview_marks_disabled_account(
    client: httpx.AsyncClient, owner_account: Account
) -> None:
    member = create_user("ao-d")
    try:
        await _seed(member, persons=1, facts=1, todos=0)
        csrf = await login(client, owner_account)

        disabled = await client.patch(
            f"/api/admin/users/{member.id}",
            json={"is_active": False},
            headers=csrf_headers(csrf),
        )
        assert disabled.status_code == 200

        data = (await client.get("/api/admin/overview")).json()["data"]
        row = next(item for item in data["users"] if item["username"] == member.username)
        assert row["is_active"] is False
        assert data["accounts"]["disabled"] >= 1
    finally:
        delete_users(member.id)


async def test_overview_requires_login(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/admin/overview")
    assert response.status_code == 401
