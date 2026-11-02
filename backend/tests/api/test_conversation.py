"""助理对话接口：一轮问答、动作执行、历史、隔离、失败重试。

模型一律 monkeypatch，不花 token、不等网络。这里验的是**我们的逻辑**：
  · 用户消息先落库（模型挂了也不丢字）
  · 动作真的落到了 todos / events / facts（事实只能 pending）
  · 越权读不到别人的对话（RLS）
  · 白名单外 / 超量的动作被拒，不影响其它动作
"""

import base64
import json
import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from tempfile import mkdtemp

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.models.chat import ChatMessage
from app.models.event import Event
from app.models.fact import Fact
from app.models.person import Person
from app.models.todo import Todo
from app.services import conversation as conversation_service
from app.services import extract as extract_service
from app.services.vision import ModelReply, vision
from tests.conftest import Account, csrf_headers, login, new_client, rls_session

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _reply(payload: dict) -> ModelReply:
    return ModelReply(
        parsed=payload, raw=json.dumps(payload, ensure_ascii=False), provider="fake", model="fake"
    )


@pytest.fixture
def fake_model(monkeypatch: pytest.MonkeyPatch) -> dict:
    """默认：只回话，不动数据。用例要动作就改 state['payload']。"""
    state = {"payload": {"reply": "我在。", "actions": []}}

    async def _converse(messages: list[dict], **kwargs: object) -> ModelReply:
        state["messages"] = messages
        return _reply(state["payload"])

    monkeypatch.setattr(vision, "converse", _converse)
    return state


@pytest.fixture(autouse=True)
def isolated_upload_dir(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    root = Path(mkdtemp(prefix="keeper-conv-"))
    monkeypatch.setattr(settings, "data_dir", str(root))
    yield
    shutil.rmtree(root, ignore_errors=True)


async def _person(user: Account, nickname: str = "王姐") -> int:
    async with rls_session(user.id) as session:
        person = Person(user_id=user.id, nickname=nickname)
        session.add(person)
        await session.commit()
        return int(person.id or 0)


async def test_reply_is_persisted_as_two_messages(
    client: httpx.AsyncClient, single_user: Account, fake_model: dict
) -> None:
    fake_model["payload"] = {"reply": "王姐生日还有 3 天。", "actions": []}
    csrf = await login(client, single_user)

    response = await client.post(
        "/api/conversation/messages",
        data={"text": "王姐生日还有几天？"},
        headers=csrf_headers(csrf),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["reply"] == "王姐生日还有 3 天。"
    assert data["actions"] == []

    history = (await client.get("/api/conversation/messages")).json()["data"]
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[0]["content"] == "王姐生日还有几天？"
    assert history[1]["content"] == "王姐生日还有 3 天。"


async def test_system_prompt_carries_today_and_people(
    client: httpx.AsyncClient, single_user: Account, fake_model: dict
) -> None:
    """模型必须拿到"今天是几号"和人物名录，否则它没法把"明天"换算成日期。"""
    await _person(single_user, "王姐")
    csrf = await login(client, single_user)

    await client.post(
        "/api/conversation/messages", data={"text": "明天提醒我"}, headers=csrf_headers(csrf)
    )

    system = fake_model["messages"][0]["content"]
    assert fake_model["messages"][0]["role"] == "system"
    assert "今天" in system
    assert "王姐" in system, "人物名录要进上下文，不然模型认不出人"


async def test_create_todo_action_lands_in_db(
    client: httpx.AsyncClient, single_user: Account, fake_model: dict
) -> None:
    person_id = await _person(single_user)
    fake_model["payload"] = {
        "reply": "好，11-04 给你建了提醒。",
        "actions": [
            {
                "type": "create_todo",
                "title": "给王姐准备生日礼物",
                "due_on": "2026-11-04",
                "person": "王姐",
            }
        ],
    }
    csrf = await login(client, single_user)

    response = await client.post(
        "/api/conversation/messages",
        data={"text": "11 月 4 号王姐生日，帮我建个待办"},
        headers=csrf_headers(csrf),
    )

    assert response.status_code == 200
    actions = response.json()["data"]["actions"]
    assert len(actions) == 1
    assert actions[0]["ok"] is True
    assert "已创建待办" in actions[0]["summary"]

    async with rls_session(single_user.id) as session:
        todo = (await session.execute(select(Todo))).scalars().one()
    assert todo.title == "给王姐准备生日礼物"
    assert todo.due_date == date(2026, 11, 4)
    assert todo.person_id == person_id
    assert todo.source == "ai"


async def test_create_event_birthday_requires_known_person(
    client: httpx.AsyncClient, single_user: Account, fake_model: dict
) -> None:
    """认不出人时生日事件不落库——日历上一条"无名生日"没有意义。"""
    fake_model["payload"] = {
        "reply": "没找到这个人。",
        "actions": [
            {
                "type": "create_event",
                "event_type": "birthday",
                "title": "王姐生日",
                "occurs_on": "2026-11-04",
                "person": "查无此人",
            }
        ],
    }
    csrf = await login(client, single_user)

    response = await client.post(
        "/api/conversation/messages", data={"text": "王姐生日"}, headers=csrf_headers(csrf)
    )

    actions = response.json()["data"]["actions"]
    assert actions[0]["ok"] is False
    assert "没认出" in (actions[0]["reason"] or "")

    async with rls_session(single_user.id) as session:
        assert (await session.execute(select(Event))).scalars().all() == []


async def test_create_event_with_person(
    client: httpx.AsyncClient, single_user: Account, fake_model: dict
) -> None:
    person_id = await _person(single_user)
    fake_model["payload"] = {
        "reply": "记下了，王姐生日 11-04。",
        "actions": [
            {
                "type": "create_event",
                "event_type": "birthday",
                "title": "王姐生日",
                "occurs_on": "2026-11-04",
                "person": "王姐",
            }
        ],
    }
    csrf = await login(client, single_user)

    await client.post(
        "/api/conversation/messages",
        data={"text": "王姐 11 月 4 号生日"},
        headers=csrf_headers(csrf),
    )

    async with rls_session(single_user.id) as session:
        event = (await session.execute(select(Event))).scalars().one()
    assert event.type == "birthday"
    assert event.person_id == person_id
    assert event.occurs_on == date(2026, 11, 4)


async def test_add_fact_only_creates_pending(
    client: httpx.AsyncClient, single_user: Account, fake_model: dict
) -> None:
    """AI 不许直接写档案：记事实只能落 pending，等用户在确认页点过。"""
    await _person(single_user)
    fake_model["payload"] = {
        "reply": "记下了，等你确认。",
        "actions": [
            {
                "type": "add_fact",
                "person": "王姐",
                "content": "女儿今年高考",
                "category": "structure",
            }
        ],
    }
    csrf = await login(client, single_user)

    await client.post(
        "/api/conversation/messages", data={"text": "王姐女儿今年高考"}, headers=csrf_headers(csrf)
    )

    async with rls_session(single_user.id) as session:
        fact = (await session.execute(select(Fact))).scalars().one()
    assert fact.status == "pending"
    assert fact.content == "女儿今年高考"
    assert fact.source_type == "manual"


async def test_unknown_action_is_rejected_silently(
    client: httpx.AsyncClient, single_user: Account, fake_model: dict
) -> None:
    """模型说了"删除王姐"这种白名单外动作 → 不执行、不报错、回复照常。"""
    fake_model["payload"] = {
        "reply": "删人得你自己去档案页点。",
        "actions": [{"type": "delete_person", "person": "王姐"}],
    }
    csrf = await login(client, single_user)

    response = await client.post(
        "/api/conversation/messages", data={"text": "把王姐删了"}, headers=csrf_headers(csrf)
    )

    assert response.status_code == 200
    assert response.json()["data"]["actions"] == []
    assert response.json()["data"]["reply"]


async def test_actions_are_capped_per_turn(
    client: httpx.AsyncClient, single_user: Account, fake_model: dict
) -> None:
    """模型一口气给 5 条待办 → 只执行前 3 条（MAX_ACTIONS_PER_TURN）。"""
    fake_model["payload"] = {
        "reply": "建了三条。",
        "actions": [{"type": "create_todo", "title": f"事{index}"} for index in range(5)],
    }
    csrf = await login(client, single_user)

    response = await client.post(
        "/api/conversation/messages", data={"text": "帮我建五条"}, headers=csrf_headers(csrf)
    )

    assert len(response.json()["data"]["actions"]) == conversation_service.MAX_ACTIONS_PER_TURN
    async with rls_session(single_user.id) as session:
        assert len((await session.execute(select(Todo))).scalars().all()) == 3


async def test_model_failure_keeps_user_message(
    client: httpx.AsyncClient, single_user: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型挂了 → 502，但用户那条消息还在（前端重发不用重打）。"""
    from app.core.errors import AppError, ErrorCode

    async def _fail(messages: list[dict], **kwargs: object) -> ModelReply:
        raise AppError(ErrorCode.AI_UPSTREAM_ERROR, "upstream down", status_code=502)

    monkeypatch.setattr(vision, "converse", _fail)
    csrf = await login(client, single_user)

    response = await client.post(
        "/api/conversation/messages", data={"text": "在吗"}, headers=csrf_headers(csrf)
    )

    assert response.status_code == 502
    history = (await client.get("/api/conversation/messages")).json()["data"]
    assert [item["role"] for item in history] == ["user"]


async def test_empty_message_is_rejected(
    client: httpx.AsyncClient, single_user: Account, fake_model: dict
) -> None:
    csrf = await login(client, single_user)
    response = await client.post(
        "/api/conversation/messages", data={"text": "   "}, headers=csrf_headers(csrf)
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_write_requires_csrf(
    client: httpx.AsyncClient, single_user: Account, fake_model: dict
) -> None:
    await login(client, single_user)
    response = await client.post("/api/conversation/messages", data={"text": "在吗"})
    assert response.status_code == 403


async def test_history_is_isolated_between_users(
    two_users: tuple[Account, Account], fake_model: dict
) -> None:
    """A 的对话 B 一条都看不到（RLS，不是应用层过滤）。"""
    user_a, user_b = two_users

    async with new_client() as client_a, new_client() as client_b:
        csrf_a = await login(client_a, user_a)
        await client_a.post(
            "/api/conversation/messages",
            data={"text": "A 的私密问题"},
            headers=csrf_headers(csrf_a),
        )

        await login(client_b, user_b)
        history_b = (await client_b.get("/api/conversation/messages")).json()["data"]

    assert history_b == []


async def test_clear_history_only_touches_messages(
    client: httpx.AsyncClient, single_user: Account, fake_model: dict
) -> None:
    """清空对话不删已经建出来的待办——用户是想重开话题，不是想丢任务。"""
    fake_model["payload"] = {
        "reply": "好。",
        "actions": [{"type": "create_todo", "title": "别被清掉的事"}],
    }
    csrf = await login(client, single_user)

    await client.post(
        "/api/conversation/messages", data={"text": "建个待办"}, headers=csrf_headers(csrf)
    )

    cleared = await client.delete("/api/conversation/messages", headers=csrf_headers(csrf))
    assert cleared.status_code == 200
    assert cleared.json()["data"]["deleted"] == 2

    assert (await client.get("/api/conversation/messages")).json()["data"] == []
    async with rls_session(single_user.id) as session:
        assert len((await session.execute(select(Todo))).scalars().all()) == 1


async def test_image_message_runs_ingest_pipeline(
    client: httpx.AsyncClient,
    single_user: Account,
    fake_model: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """带图的这一轮要走投喂流水线（不抽取，这里只验链路通+消息落库）。"""

    async def _noop_extract(
        raw_input_id: int, user_id: int, forced_person_id: int | None = None
    ) -> None:
        return None

    monkeypatch.setattr(extract_service, "extract_from_raw_input", _noop_extract)
    csrf = await login(client, single_user)

    response = await client.post(
        "/api/conversation/messages",
        files={"image": ("shot.png", PNG_1PX, "image/png")},
        data={"text": "看看这张"},
        headers=csrf_headers(csrf),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["raw_input_id"] is not None

    async with rls_session(single_user.id) as session:
        message = (
            (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.role == "user")
                    .order_by(ChatMessage.id.desc())
                )
            )
            .scalars()
            .first()
        )
    assert message is not None
    assert message.image_raw_input_id == data["raw_input_id"]
    assert "截图" in (message.content or "")


async def test_history_limit_is_bounded(
    client: httpx.AsyncClient, single_user: Account, fake_model: dict
) -> None:
    await login(client, single_user)
    response = await client.get("/api/conversation/messages", params={"limit": 9999})
    assert response.status_code == 400
