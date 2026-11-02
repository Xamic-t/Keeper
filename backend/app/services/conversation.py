"""助理对话：一轮问答 + 动作执行。

对应文档：`docs/32-助理整合计划.md`、`ARCHITECTURE` 19.7.10

和 `services/chat.py`（投喂对话框）的分工：
  · `chat.py`    —— 规则路由，两个意图（投喂 / 查人），不调模型，快、稳、便宜
  · `conversation.py` —— 真模型对话，多轮、能执行动作，慢一点，是"助理"

四条设计约束：
  ① **模型不许直接改档案**：`add_fact` 只落 `pending`，用户在确认页点过才算数
  ② **动作走白名单**：只允许建待办 / 建事件 / 记事实，删改一律不做；
     一轮最多 3 个（`MAX_ACTIONS_PER_TURN`），防"一口气建 20 条"
  ③ **上下文用快照 + 最近 N 轮**，不做全量历史：20 人规模够用，也不会把 token 烧光
  ④ **模型失败要可重试**：用户消息先落库再调模型，失败时那条消息还在，
     前端重发即可，不会丢用户刚打的字
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_today
from app.core.errors import AppError, ErrorCode
from app.models.chat import ChatMessage
from app.models.event import Event
from app.models.fact import Fact
from app.models.person import Person
from app.models.todo import Todo
from app.services import event as event_service
from app.services import ingest as ingest_service
from app.services import todo as todo_service
from app.services.ingest import UploadItem
from app.services.vision import vision

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
PROMPT_VERSION = "v1"

# 一条消息最多多少字
MAX_MESSAGE_CHARS = 4000
# 带多少轮历史给模型（一轮 = 用户 + 助理各一条）
HISTORY_TURNS = 8
# 一轮最多执行几个动作
MAX_ACTIONS_PER_TURN = 3
# 快照里各列表的长度上限（人物 20 人规模，全给没关系）
SNAPSHOT_LIMIT = 20

ALLOWED_ACTIONS = {"create_todo", "create_event", "add_fact"}
ALLOWED_EVENT_TYPES = {"birthday", "payment_due", "festival", "custom"}
ALLOWED_CATEGORIES = {"permanent", "preference", "situation", "taboo", "mood", "structure"}


@dataclass(slots=True)
class ActionResult:
    """一个动作的执行结果。前端拿它渲染"✅ 已创建待办 · 查看"。"""

    type: str
    ok: bool
    summary: str
    ref_id: int | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "ok": self.ok,
            "summary": self.summary,
            "ref_id": self.ref_id,
            "reason": self.reason,
        }


@dataclass(slots=True)
class TurnResult:
    """一轮对话的结果。"""

    reply: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    user_message_id: int | None = None
    assistant_message_id: int | None = None
    raw_input_id: int | None = None


# ── 上下文快照 ────────────────────────────────────────────


async def build_snapshot(session: AsyncSession) -> dict[str, Any]:
    """给模型看的最小上下文。

    故意**不带**事实原文：事实可能几十条，塞进去既费 token 又容易让模型拿旧信息
    瞎答。要具体某人的事实，让模型用 `ask` 的方式不行——V1 就是靠这份快照答。
    所以这里给的是"人名 + 标签 + 最近的日子 + 今天待办"，详细事实等用户问具体人时
    由 `answer_query` 那条老路径兜底（本服务也复用它，见 `_person_facts_brief`）。
    """
    today = local_today()

    persons = list(
        (
            await session.execute(
                select(Person).order_by(Person.updated_at.desc()).limit(SNAPSHOT_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    events = list(
        (
            await session.execute(
                select(Event)
                .where(Event.occurs_on.between(today, today + timedelta(days=7)))
                .order_by(Event.occurs_on)
                .limit(SNAPSHOT_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    todos = list(
        (
            await session.execute(
                select(Todo)
                .where(Todo.due_date.is_not(None), Todo.due_date <= today, Todo.status == "open")
                .order_by(Todo.due_date)
                .limit(SNAPSHOT_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    person_names = {person.id: person.nickname for person in persons}

    return {
        "今天": today.isoformat(),
        "人物": [
            {
                "昵称": person.nickname,
                "别名": list(person.aliases or []),
                "最近事实条数": None,  # 占位：不查，避免 N+1
            }
            for person in persons
        ],
        "未来7天事件": [
            {
                "标题": event.title,
                "类型": event.type,
                "日期": event.occurs_on.isoformat(),
                "人": person_names.get(event.person_id or 0, ""),
            }
            for event in events
        ],
        "今天该做的待办": [
            {"标题": todo.title, "截止": todo.due_date.isoformat() if todo.due_date else ""}
            for todo in todos
        ],
    }


def render_prompt(snapshot: dict[str, Any]) -> str:
    """把模板里的 {snapshot} 换成真数据。模板缺了就直接报错，不静默降级。"""
    template_path = PROMPTS_DIR / f"{PROMPT_VERSION}_assistant.md"
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AppError(ErrorCode.INTERNAL_ERROR, "助理提示词缺失", status_code=500) from exc

    # 人物列表里那个占位字段没意义，渲染前剔掉，省 token
    for person in snapshot.get("人物", []):
        person.pop("最近事实条数", None)

    return template.replace("{snapshot}", json.dumps(snapshot, ensure_ascii=False, indent=2))


async def _history(session: AsyncSession, turns: int = HISTORY_TURNS) -> list[ChatMessage]:
    """最近 N 轮消息，按时间正序（模型要正序）。"""
    rows = list(
        (
            await session.execute(
                select(ChatMessage).order_by(ChatMessage.id.desc()).limit(turns * 2)
            )
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


def build_messages(
    system_prompt: str, history: list[ChatMessage], user_text: str
) -> list[dict[str, Any]]:
    """拼成 OpenAI 兼容的 messages 数组。"""
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for item in history:
        # 动作结果不塞回模型：它已经知道结果在 reply 里，重复喂只会让它复读
        messages.append({"role": item.role, "content": item.content or ""})
    messages.append({"role": "user", "content": user_text})
    return messages


# ── 动作执行 ──────────────────────────────────────────────


async def _resolve_person(session: AsyncSession, name: str | None) -> Person | None:
    """按昵称/别名找一个人。找不到返回 None（调用方决定是报错还是忽略）。"""
    if not name:
        return None
    cleaned = name.strip()
    if not cleaned:
        return None
    person = (
        await session.execute(select(Person).where(Person.nickname == cleaned))
    ).scalar_one_or_none()
    if person is not None:
        return person
    # 别名是 JSONB 数组，用 containment 查，避免全表拉到内存
    person = (
        await session.execute(select(Person).where(Person.aliases.contains([cleaned])))
    ).scalar_one_or_none()
    return person


def _parse_date(value: Any) -> date | None:
    """模型给的日期字符串。解析不出就返回 None，绝不猜。"""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


async def _create_todo(
    session: AsyncSession, user_id: int, payload: dict[str, Any]
) -> ActionResult:
    title = str(payload.get("title") or "").strip()
    if not title:
        return ActionResult("create_todo", False, "建待办失败", reason="没给标题")

    person = await _resolve_person(session, payload.get("person"))
    due_on = _parse_date(payload.get("due_on"))
    todo = await todo_service.create_todo(
        session,
        user_id,
        title=title[:100],
        due_date=due_on,
        person_id=person.id if person else None,
        # source 白名单是 template/ai/reminder/manual（todos 表的 ck_todo_source），
        # 助理建的就是"AI 生成的"，用已有的 ai，不为了这一处改表约束
        source="ai",
        ai_reason="助理对话中创建",
    )
    who = f"（{person.nickname}）" if person else ""
    when = f"，{due_on.isoformat()}" if due_on else ""
    return ActionResult(
        "create_todo",
        True,
        f"已创建待办：{title}{who}{when}",
        ref_id=todo.id,
    )


async def _create_event(
    session: AsyncSession, user_id: int, payload: dict[str, Any]
) -> ActionResult:
    title = str(payload.get("title") or "").strip()
    event_type = str(payload.get("event_type") or "").strip()
    occurs_on = _parse_date(payload.get("occurs_on"))

    if not title:
        return ActionResult("create_event", False, "建事件失败", reason="没给标题")
    if event_type not in ALLOWED_EVENT_TYPES:
        return ActionResult(
            "create_event", False, "建事件失败", reason=f"不支持的类型 {event_type}"
        )
    if occurs_on is None:
        return ActionResult("create_event", False, "建事件失败", reason="日期看不懂")

    person = await _resolve_person(session, payload.get("person"))
    if person is None and event_type in {"birthday", "payment_due"}:
        # 生日/结款必须挂在人身上，否则日历上就是一条"某人的生日"但不知道是谁
        return ActionResult("create_event", False, "建事件失败", reason="没认出是哪个人")

    event = await event_service.create_event(
        session,
        user_id,
        type=event_type,
        title=title[:100],
        occurs_on=occurs_on,
        person_id=person.id if person else None,
    )
    who = f"（{person.nickname}）" if person else ""
    return ActionResult(
        "create_event",
        True,
        f"已建事件：{title}{who} · {occurs_on.isoformat()}",
        ref_id=event.id,
    )


async def _add_fact(session: AsyncSession, user_id: int, payload: dict[str, Any]) -> ActionResult:
    """记一条事实——**只落 pending**，进确认队列。

    这是 ARCHITECTURE 17 的硬约束：AI 不许直接写档案。
    模型说得再像真的，也要用户点一次确认。
    """
    content = str(payload.get("content") or "").strip()
    category = str(payload.get("category") or "").strip()
    if not content:
        return ActionResult("add_fact", False, "记事实失败", reason="没给内容")
    if category not in ALLOWED_CATEGORIES:
        category = "situation"

    person = await _resolve_person(session, payload.get("person"))
    if person is None:
        return ActionResult("add_fact", False, "记事实失败", reason="没认出是哪个人")

    fact = Fact(
        user_id=user_id,
        person_id=person.id,
        content=content[:500],
        category=category,
        confidence=0.6,
        source_type="manual",
        status="pending",
    )
    session.add(fact)
    await session.flush()
    return ActionResult(
        "add_fact",
        True,
        f"记下了（待确认）：{person.nickname} · {content[:40]}",
        ref_id=fact.id,
    )


async def execute_actions(
    session: AsyncSession, user_id: int, actions: list[Any]
) -> tuple[list[ActionResult], list[dict[str, Any]]]:
    """执行模型给的动作。返回 (结果, 被拒绝的动作原文)。

    单个动作失败不影响其它动作：模型可能一口气给 3 个，其中一个认不出人，
    另外两个该成还得成。
    """
    results: list[ActionResult] = []
    rejected: list[dict[str, Any]] = []

    for raw in actions[:MAX_ACTIONS_PER_TURN]:
        if not isinstance(raw, dict):
            continue
        action_type = str(raw.get("type") or "")
        if action_type not in ALLOWED_ACTIONS:
            rejected.append(raw)
            logger.warning("assistant rejected action type=%s", action_type)
            continue

        handler = {
            "create_todo": _create_todo,
            "create_event": _create_event,
            "add_fact": _add_fact,
        }[action_type]
        result = await handler(session, user_id, raw)
        results.append(result)
        if not result.ok:
            rejected.append(raw)

    return results, rejected


# ── 一轮对话 ──────────────────────────────────────────────


def _validate(text: str, has_image: bool) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) > MAX_MESSAGE_CHARS:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"一次最多 {MAX_MESSAGE_CHARS} 字",
            status_code=400,
        )
    if not cleaned and not has_image:
        raise AppError(ErrorCode.VALIDATION_ERROR, "说点什么，或者发一张图", status_code=400)
    return cleaned


async def _feed_image(
    session: AsyncSession, user_id: int, image: UploadItem
) -> tuple[int | None, str]:
    """图片走既有投喂流水线，返回 (raw_input_id, 给模型看的描述)。"""
    results = await ingest_service.ingest_images(session, user_id, [image])
    item = results[0]
    if item.error is not None:
        return None, f"（用户发了一张图，但没存下：{item.error}）"
    if item.duplicate:
        return item.raw_input_id, "（用户发了一张之前投过的图，系统识别为重复，未重复抽取）"

    raw_input_id = item.raw_input_id
    if raw_input_id is None:
        return None, "（用户发了一张图，但没存下）"

    # 抽取自己开 session，跑完再回查（和 chat.py 里同一套处理）
    from app.services import extract as extract_service

    await extract_service.extract_from_raw_input(raw_input_id, user_id)
    await session.commit()
    session.expire_all()

    from app.models.input import RawInput

    raw_input = await session.get(RawInput, raw_input_id)
    if raw_input is None:
        return raw_input_id, "（用户发了一张图）"

    facts = list(
        (
            await session.execute(
                select(Fact).where(
                    Fact.source_id == raw_input_id,
                    Fact.source_type.in_(("screenshot", "text")),
                )
            )
        )
        .scalars()
        .all()
    )
    if not facts:
        return raw_input_id, "（用户发了一张截图，但里面没抽出能记的事）"

    lines = [f"· {fact.content}" for fact in facts]
    return raw_input_id, "（用户发了一张截图，抽出来这些，已进确认队列）\n" + "\n".join(lines)


async def run_turn(
    session: AsyncSession,
    user_id: int,
    *,
    text: str = "",
    image: UploadItem | None = None,
) -> TurnResult:
    """一轮对话：落用户消息 → 调模型 → 执行动作 → 落助手消息。"""
    cleaned = _validate(text, image is not None)

    raw_input_id: int | None = None
    image_note = ""
    if image is not None:
        raw_input_id, image_note = await _feed_image(session, user_id, image)

    # 用户消息先落库：模型挂了这条也在，前端重发不用重打
    user_text = cleaned or "（发了一张截图）"
    if image_note:
        user_text = f"{user_text}\n{image_note}".strip()

    user_message = ChatMessage(
        user_id=user_id,
        role="user",
        content=user_text,
        image_raw_input_id=raw_input_id,
    )
    session.add(user_message)
    await session.commit()

    snapshot = await build_snapshot(session)
    history = await _history(session)
    # _history 已经把刚落的这条捞进来，拼 messages 时别重复加
    history = [item for item in history if item.id != user_message.id]

    messages = build_messages(render_prompt(snapshot), history, user_text)

    try:
        reply = await vision.converse(messages)
    except AppError:
        logger.warning("assistant model failed user_id=%s", user_id)
        raise

    data = reply.parsed
    reply_text = str(data.get("reply") or "").strip()
    if not reply_text:
        raise AppError(ErrorCode.AI_UPSTREAM_ERROR, "模型没有给出回复", status_code=502)

    raw_actions = data.get("actions")
    actions = raw_actions if isinstance(raw_actions, list) else []
    results, _rejected = await execute_actions(session, user_id, actions)

    assistant_message = ChatMessage(
        user_id=user_id,
        role="assistant",
        content=reply_text,
        actions=[item.as_dict() for item in results],
    )
    session.add(assistant_message)
    await session.commit()

    return TurnResult(
        reply=reply_text,
        actions=[item.as_dict() for item in results],
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        raw_input_id=raw_input_id,
    )


# ── 历史读写 ──────────────────────────────────────────────


def to_dict(message: ChatMessage) -> dict[str, Any]:
    """对外形状。前端只认这几个字段。"""
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "actions": message.actions or [],
        "image_raw_input_id": message.image_raw_input_id,
        "created_at": message.created_at,
    }


async def load_history(session: AsyncSession, limit: int = 50) -> list[dict[str, Any]]:
    """最近 limit 条，按时间正序返回（前端直接渲染）。"""
    rows = list(
        (await session.execute(select(ChatMessage).order_by(ChatMessage.id.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return [to_dict(item) for item in reversed(rows)]


async def clear_history(session: AsyncSession) -> int:
    """清空当前用户的对话。返回删了几条。"""
    result = await session.execute(delete(ChatMessage))
    await session.commit()
    return int(result.rowcount or 0)
