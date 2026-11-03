"""`/api/assistant/actions`：Harness 路径上的写操作。

为什么这个接口值得单独一套用例：它是"模型能改数据"的**唯一入口**，
比只读接口危险一个量级。三条必须钉死：
  ① 白名单之外的动作一律拒绝（不让模型删人、改别人数据）
  ② 认不出人就不建（宁可反问，不要建一条挂在空气上的待办）
  ③ 记事实只落 pending（AI 不许直接写档案）
"""

from collections.abc import Iterator

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.config import settings
from app.models.fact import Fact
from app.models.todo import Todo
from app.services.person import create_person
from tests.conftest import Account, rls_session

TOKEN = "test-assistant-token-actions"


@pytest.fixture
def assistant_as(single_user: Account) -> Iterator[Account]:
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


def _headers() -> dict[str, str]:
    return {"X-Assistant-Token": TOKEN}


async def test_create_todo(client: httpx.AsyncClient, assistant_as: Account) -> None:
    await _person(assistant_as, "王姐")

    response = await client.post(
        "/api/assistant/actions",
        json={
            "type": "create_todo",
            "title": "给王姐准备生日礼物",
            "person": "王姐",
            "due_on": "2026-11-05",
        },
        headers=_headers(),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is True
    assert "给王姐准备生日礼物" in data["summary"]
    async with rls_session(assistant_as.id) as session:
        todos = list((await session.execute(select(Todo))).scalars().all())
    assert len(todos) == 1
    assert todos[0].source == "ai"


async def test_unknown_person_does_not_create(
    client: httpx.AsyncClient, assistant_as: Account
) -> None:
    """认不出人就 ok=false + 原因——模型据此反问，而不是建一条挂空的待办。"""
    response = await client.post(
        "/api/assistant/actions",
        json={"type": "create_todo", "title": "寄样品", "person": "查无此人"},
        headers=_headers(),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is False
    assert data["reason"]
    async with rls_session(assistant_as.id) as session:
        assert list((await session.execute(select(Todo))).scalars().all()) == []


async def test_fact_goes_to_pending(client: httpx.AsyncClient, assistant_as: Account) -> None:
    """ "记事实"只能进待确认队列——AI 不许直接写档案（§19.7.10）。"""
    await _person(assistant_as, "小雨")

    response = await client.post(
        "/api/assistant/actions",
        json={
            "type": "add_fact",
            "person": "小雨",
            "content": "最近在装修新店",
            "category": "situation",
        },
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["data"]["ok"] is True
    async with rls_session(assistant_as.id) as session:
        facts = list((await session.execute(select(Fact))).scalars().all())
    assert len(facts) == 1
    assert facts[0].status == "pending"


async def test_action_outside_whitelist_is_rejected(
    client: httpx.AsyncClient, assistant_as: Account
) -> None:
    """模型说得出"删掉这个人"，但接口不做。"""
    response = await client.post(
        "/api/assistant/actions",
        json={"type": "delete_person", "person": "王姐"},
        headers=_headers(),
    )

    assert response.status_code == 400


async def test_actions_require_token(client: httpx.AsyncClient, assistant_as: Account) -> None:
    response = await client.post(
        "/api/assistant/actions", json={"type": "create_todo", "title": "x"}
    )
    assert response.status_code == 401


async def test_person_from_other_account_is_not_visible(
    client: httpx.AsyncClient, two_users: tuple[Account, Account]
) -> None:
    """拿别人的昵称建待办 → 认不出（RLS 让他根本看不到那个人）。"""
    owner, other = two_users
    await _person(other, "别人家的人")

    original_token = settings.assistant_token
    original_name = settings.assistant_username
    settings.assistant_token = SecretStr(TOKEN)
    settings.assistant_username = owner.username
    try:
        response = await client.post(
            "/api/assistant/actions",
            json={"type": "create_todo", "title": "寄样品", "person": "别人家的人"},
            headers=_headers(),
        )
    finally:
        settings.assistant_token = original_token
        settings.assistant_username = original_name

    assert response.status_code == 200
    assert response.json()["data"]["ok"] is False
