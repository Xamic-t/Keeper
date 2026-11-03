"""`/api/assistant/search`：Harness 的检索工具接口。

三条要守住：
  ① 没有 token 不给查（和别的助理接口同一套认证）
  ② 只返回 token 对应那个人的切片
  ③ `semantic` 字段如实反映"这次是语义还是关键词"——模型据此决定
     能不能断言"你没提过"
"""

from collections.abc import Iterator

import httpx
import pytest
from pydantic import SecretStr

from app.config import settings
from app.models.person import Person
from app.services import rag
from tests.conftest import Account, rls_session

TOKEN = "test-assistant-token-search"


@pytest.fixture
def assistant_as(single_user: Account) -> Iterator[Account]:
    original_token = settings.assistant_token
    original_name = settings.assistant_username
    settings.assistant_token = SecretStr(TOKEN)
    settings.assistant_username = single_user.username
    yield single_user
    settings.assistant_token = original_token
    settings.assistant_username = original_name


@pytest.fixture(autouse=True)
def _no_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dashscope_api_key", None)


async def _seed(user: Account, content: str) -> None:
    async with rls_session(user.id) as session:
        person = Person(user_id=user.id, nickname="王姐")
        session.add(person)
        await session.flush()
        await rag.index_source(
            session,
            user_id=user.id,
            source_type="raw_input",
            source_id=1,
            content=content,
            person_id=person.id,
            scope="knowledge",
        )


async def test_search_requires_token(client: httpx.AsyncClient, assistant_as: Account) -> None:
    response = await client.get("/api/assistant/search", params={"q": "预算"})
    assert response.status_code == 401


async def test_search_returns_hits(client: httpx.AsyncClient, assistant_as: Account) -> None:
    await _seed(assistant_as, "王姐说这个月预算紧，先不要推新品。")

    response = await client.get(
        "/api/assistant/search", params={"q": "预算"}, headers={"X-Assistant-Token": TOKEN}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["query"] == "预算"
    assert data["semantic"] is False, "没配向量模型，要如实说这次是关键词匹配"
    assert len(data["items"]) == 1
    assert "预算" in data["items"][0]["content"]
    assert data["items"][0]["source_type"] == "raw_input"


async def test_search_respects_rls(
    client: httpx.AsyncClient, two_users: tuple[Account, Account]
) -> None:
    """别人的材料查不到——即使关键词一模一样。"""
    owner, other = two_users
    original_token = settings.assistant_token
    original_name = settings.assistant_username
    settings.assistant_token = SecretStr(TOKEN)
    settings.assistant_username = owner.username
    try:
        await _seed(other, "另一个人的预算也很紧")
        response = await client.get(
            "/api/assistant/search", params={"q": "预算"}, headers={"X-Assistant-Token": TOKEN}
        )
    finally:
        settings.assistant_token = original_token
        settings.assistant_username = original_name

    assert response.status_code == 200
    assert response.json()["data"]["items"] == []


async def test_search_rejects_empty_query(client: httpx.AsyncClient, assistant_as: Account) -> None:
    response = await client.get(
        "/api/assistant/search", params={"q": ""}, headers={"X-Assistant-Token": TOKEN}
    )
    assert response.status_code == 400
