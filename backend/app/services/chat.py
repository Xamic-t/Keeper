"""对话：一条消息进来，一句话回答出去。

对应文档：docs/07-后端设计.md 5.1「对话」

**只做两件事：投喂、查询。** 不做通用聊天（HANDOFF2 W13-B 的边界）。
所以这里没有会话、没有历史、没有记忆——每次请求自带上下文。

三条设计取舍：
  ① 意图判定用确定性规则，不额外调一次模型。判错了用户也能立刻看出来
     （问句走了投喂，回复会明显不对），比让模型再猜一层更可控
  ② 查询**不新做 RAG**，直接查 facts / tags / ledger。20 人规模 SQL 更准更快，
     向量检索是 V2 的事
  ③ 投喂是**同步抽取**（不是 202 排队）：不抽完就没法在一句话里告诉用户抽到了什么
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_today
from app.models.event import Event
from app.models.fact import Fact
from app.models.input import RawInput
from app.models.person import Person
from app.services import extract as extract_service
from app.services import ingest as ingest_service
from app.services import person as person_service
from app.services.ingest import UploadItem

logger = logging.getLogger(__name__)

# 问句特征。**只收明确的疑问词**：
# "她喜欢喝美式" 是陈述句，里面也有"喜欢"，但它必须走投喂——
# 判成查询就等于把这条材料丢了，而丢材料比多一条垃圾材料严重得多。
_QUESTION_MARKS = (
    "？", "?", "吗", "呢", "什么", "啥", "谁", "哪", "多少", "怎么", "为什么", "几点"
)

# 一次回答最多列几条事实。列太多就不叫"一句话回答"了
MAX_FACTS_IN_REPLY = 6


@dataclass
class ChatReply:
    """对话接口的返回体。字段顺序跟着 docs/07 那张响应表走。"""

    reply: str
    intent: str
    person_id: int | None = None
    nickname: str | None = None
    facts: list[dict[str, Any]] = field(default_factory=list)
    raw_input_id: int | None = None
    pending_count: int = 0


def detect_intent(*, text: str, has_image: bool, person_id: int | None) -> str:
    """判定这一句是投喂还是查询。纯函数，方便单独测。

    顺序照着 docs/07 那张表：带图 → 投喂；问句特征 → 查询；
    指定了人 → 投喂；其它 → 投喂（归属交给模型判）。
    """
    if has_image:
        return "feed"
    body = (text or "").strip()
    if not body:
        return "feed"
    if any(mark in body for mark in _QUESTION_MARKS):
        return "query"
    if person_id is not None:
        return "feed"
    return "feed"


async def _all_persons(session: AsyncSession) -> list[Person]:
    """全部人（20 人规模，一次取完做匹配，不用模糊查询）。

    不直接走 `list_persons(keyword=...)`：关键词是整句，ilike 匹配不上，
    只有把候选全取回来做子串判断才准。
    """
    rows = (await session.execute(select(Person).order_by(Person.updated_at.desc()))).scalars()
    return list(rows.all())


async def _match_person(
    session: AsyncSession, text: str, person_id: int | None
) -> Person | None:
    """从一句话里认出说的是谁。用户指定了就用指定的。"""
    if person_id is not None:
        return await session.get(Person, person_id)

    body = text or ""
    people = await _all_persons(session)
    # 先比昵称，再比别名。长的优先，避免"小王"把"小王老师"吃掉
    for person in sorted(people, key=lambda item: len(item.nickname or ""), reverse=True):
        if person.nickname and person.nickname in body:
            return person
    for person in people:
        for alias in person.aliases or []:
            if alias and str(alias) in body:
                return person
    return None


async def _facts_of(session: AsyncSession, person_id: int) -> list[Fact]:
    """已确认、权重够的事实，按权重降序。查询只用这一批——没确认的不算数。"""
    stmt = (
        select(Fact)
        .where(Fact.person_id == person_id, Fact.status == "confirmed")
        .order_by(Fact.weight.desc(), Fact.created_at.desc())
        .limit(MAX_FACTS_IN_REPLY)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _next_event(session: AsyncSession, person_id: int) -> Event | None:
    """这个人下一个还没过的日子（生日、结款这类），查询时顺嘴提一句。"""
    stmt = (
        select(Event)
        .where(Event.person_id == person_id, Event.occurs_on >= local_today())
        .order_by(Event.occurs_on)
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


def _fact_line(fact: Fact) -> dict[str, Any]:
    return {"id": fact.id, "content": fact.content, "evidence": fact.evidence}


async def answer_query(
    session: AsyncSession, *, text: str, person_id: int | None = None
) -> ChatReply:
    """按人检索，拼一句带依据的回答。拼不出来就说拼不出来。"""
    person = await _match_person(session, text, person_id)

    if person is None:
        people = (await _all_persons(session))[:5]
        names = "、".join(item.nickname for item in people) or "（还没有人）"
        return ChatReply(
            reply=f"你问谁？我这儿最近记着：{names}。带上名字问，比如「王姐喜欢什么」。",
            intent="clarify",
        )

    detail = await person_service.get_person_detail(session, person.id)  # type: ignore[arg-type]
    facts = await _facts_of(session, person.id)  # type: ignore[arg-type]
    nickname = person.nickname

    if not facts:
        return ChatReply(
            reply=(
                f"关于{nickname}，我还没有确认过的事实。"
                "投一张聊天截图给我，抽出来的东西你点过确认之后我就能答了。"
            ),
            intent="query",
            person_id=person.id,
            nickname=nickname,
        )

    lines = [f"关于{nickname}，我记得这些："]
    for fact in facts[:MAX_FACTS_IN_REPLY]:
        suffix = f"（依据：{fact.evidence}）" if fact.evidence else ""
        lines.append(f"· {fact.content}{suffix}")

    extras: list[str] = []
    if detail.tags:
        extras.append("标签：" + "、".join(detail.tags))
    upcoming = await _next_event(session, person.id)  # type: ignore[arg-type]
    if upcoming is not None:
        extras.append(f"下一个日子：{upcoming.occurs_on:%m-%d} {upcoming.title}")
    if extras:
        lines.append("；".join(extras) + "。")

    return ChatReply(
        reply="\n".join(lines),
        intent="query",
        person_id=person.id,
        nickname=nickname,
        facts=[_fact_line(fact) for fact in facts],
    )


async def _facts_of_raw_input(session: AsyncSession, raw_input_id: int) -> list[Fact]:
    """这次投喂抽出来的事实（含刚落的待确认）。"""
    stmt = (
        select(Fact)
        .where(Fact.source_id == raw_input_id, Fact.source_type.in_(("screenshot", "text")))
        .order_by(Fact.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _handle_feed(
    session: AsyncSession,
    user_id: int,
    *,
    text: str,
    person_id: int | None,
    image: UploadItem | None,
) -> ChatReply:
    """走投喂流水线，抽完再回话。

    抽取自己开 session（`extract_from_raw_input` 的约定），所以这里先拿到 raw_input，
    再调抽取，最后用本 session 回查事实。
    """
    if image is not None:
        results = await ingest_service.ingest_images(session, user_id, [image], person_id)
        item = results[0]
        if item.error is not None:
            return ChatReply(reply="这张图没存下：" + (item.error or "未知原因"), intent="feed")
        if item.duplicate:
            return ChatReply(
                reply="这张图之前投过，同一张不会重复抽。换一张给我。", intent="feed"
            )
        raw_input_id = item.raw_input_id
    else:
        raw_input = await ingest_service.ingest_text(session, user_id, text, person_id)
        raw_input_id = raw_input.id

    if raw_input_id is None:
        return ChatReply(reply="这条材料没存下，再发一次试试。", intent="feed")

    await extract_service.extract_from_raw_input(raw_input_id, user_id, person_id)

    # 抽取自己开 session 改的 status 和落的事实，本 session 的身份映射里还是旧的：
    # 先收掉当前事务再过期，否则读回来的是"处理中 + 零条事实"。
    await session.commit()
    session.expire_all()

    raw_input = await session.get(RawInput, raw_input_id)
    if raw_input is None:
        return ChatReply(reply="这条材料不见了，再发一次。", intent="feed")
    if raw_input.status == "failed":
        logger.warning("chat extraction failed raw_input_id=%s", raw_input_id)
        return ChatReply(
            reply="抽取出错了，材料还在，去投喂页看看失败原因。", intent="feed",
            raw_input_id=raw_input_id,
        )

    facts = await _facts_of_raw_input(session, raw_input_id)
    nickname = None
    if raw_input.person_id is not None:
        owner = await session.get(Person, raw_input.person_id)
        nickname = owner.nickname if owner else None

    if not facts:
        if nickname is None:
            return ChatReply(
                reply=(
                    "材料收下了，但没认出这是谁，事实先没落。"
                    "去投喂页把它指给某个人，我再抽一遍。"
                ),
                intent="feed",
                raw_input_id=raw_input_id,
            )
        return ChatReply(
            reply=f"这是{nickname}的，但这张里没抽出能记的事。",
            intent="feed",
            person_id=raw_input.person_id,
            nickname=nickname,
            raw_input_id=raw_input_id,
        )

    lines = [f"这是{nickname or '这位'}的，抽出 {len(facts)} 条："]
    lines.extend(f"· {fact.content}" for fact in facts)
    lines.append("都在确认页，点过「对」才算数。")

    return ChatReply(
        reply="\n".join(lines),
        intent="feed",
        person_id=raw_input.person_id,
        nickname=nickname,
        facts=[_fact_line(fact) for fact in facts],
        raw_input_id=raw_input_id,
        pending_count=len(facts),
    )


async def reply_to(
    session: AsyncSession,
    user_id: int,
    *,
    text: str = "",
    person_id: int | None = None,
    image: UploadItem | None = None,
) -> ChatReply:
    """对话入口。判意图 → 分流。"""
    intent = detect_intent(text=text, has_image=image is not None, person_id=person_id)
    if intent == "query":
        return await answer_query(session, text=text, person_id=person_id)
    return await _handle_feed(session, user_id, text=text, person_id=person_id, image=image)
