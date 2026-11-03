"""助理只读接口：给 DeepSeek Harness 侧车调。

对应文档：`ARCHITECTURE` 19.7、`docs/07` 5.1「助理」

三条硬规则（19.7.4），破了就伤到主架构：
  ① 业务真相只在 Postgres —— 这里只读，不缓存、不落副本
  ② 只读 + service token —— 不传用户密码，也没有任何写接口
  ③ 侧车挂了不影响主流程 —— 主流程一条都不依赖这些接口

**为什么单独一组，不复用现成接口**：现成接口要 cookie 会话 + CSRF，侧车没有浏览器；
而且现成接口返回的字段是给前端页面用的，塞给模型会带一堆噪音。
这里返回的是"给模型看的最小集"，字段少、名字直白。

token 只绕过"登录"这一步，绕不过数据隔离：解析出 `ASSISTANT_USERNAME` 之后，
照样用带 RLS 变量的 session 查库。
"""

import secrets
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.response import DataResponse, ok
from app.db import SessionLocal
from app.models.ledger import Ledger
from app.services import assistant as assistant_service
from app.services import assistant_token
from app.services import conversation as conversation_service
from app.services import embedding as embedding_service
from app.services import ledger as ledger_service
from app.services import person as person_service
from app.services import profile as profile_service
from app.services import rag as rag_service
from app.services import today as today_service
from app.services.person import PersonDetailData

router = APIRouter(prefix="/assistant", tags=["assistant"])


# ── 认证 ─────────────────────────────────────────────────
async def assistant_session(
    token: Annotated[str | None, Header(alias="X-Assistant-Token")] = None,
) -> AsyncGenerator[AsyncSession]:
    """校验 token，然后给一个**带 RLS 变量**的 session。

    先开一个不带变量的系统 session 去查 users（users 表没开 RLS），
    拿到 user_id 之后再开业务 session —— 和 `deps.get_session` 是同一套做法。
    """
    # 两种令牌，优先"用户级短期令牌"：
    #   ① v1.<uid>.<exp>.<sig> —— 主服务每轮签给 Harness 子进程，绑定具体用户
    #   ② 全局 ASSISTANT_TOKEN —— 老路径，代表 ASSISTANT_USERNAME 那一个人
    # 没有 ① 的话，tester 让助理建待办会建到 Admin 名下（越权），见 docs/38 A2。
    scoped_user_id = assistant_token.verify(token) if token else None

    if scoped_user_id is not None:
        user_id = scoped_user_id
    else:
        expected = settings.assistant_token
        if expected is None:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "助理接口未启用：没有配置 ASSISTANT_TOKEN",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        # 常量时间比对，避免用响应时间猜 token
        if not token or not secrets.compare_digest(token, expected.get_secret_value()):
            raise AppError(
                ErrorCode.UNAUTHENTICATED,
                "助理 token 不对",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        async with SessionLocal() as probe:
            user = await assistant_service.resolve_user(probe)
            user_id = user.id or 0

    async with SessionLocal() as session:
        session.info["rls_user_id"] = user_id
        yield session


Session = Annotated[AsyncSession, Depends(assistant_session)]


# ── 响应模型（给模型看的最小集）────────────────────────────
class TaskOut(BaseModel):
    kind: str
    title: str
    why: str = ""
    person_id: int | None = None
    due: str | None = None


class TodayOut(BaseModel):
    date: str
    tasks: list[TaskOut]
    pending_count: int
    degraded: bool = False


class PersonBrief(BaseModel):
    id: int
    nickname: str
    stage: str
    tags: list[str] = []
    last_interaction_at: str | None = None


class PersonListOut(BaseModel):
    total: int
    items: list[PersonBrief]


class PersonDateOut(BaseModel):
    type: str
    title: str
    occurs_on: str
    in_days: int


class PersonDetailOut(BaseModel):
    id: int
    nickname: str
    aliases: list[str] = []
    stage: str
    roles: list[str] = []
    birthday: str | None = None
    taboo: list[str] = []
    tags: list[str] = []
    note: str | None = None
    total_in: str = "0"
    total_out: str = "0"
    counts: dict[str, int] = {}
    # 接下来的日子（生日/结款/自定义），模型回答"她生日哪天"靠它
    dates: list[PersonDateOut] = []


class FactOut(BaseModel):
    content: str
    category: str
    weight: float
    evidence: str | None = None
    confirmed_at: str | None = None


class FactListOut(BaseModel):
    person_id: int
    nickname: str
    items: list[FactOut]


class LedgerItemOut(BaseModel):
    direction: str
    kind: str
    amount: str
    occurred_at: str
    due_date: str | None = None
    note: str | None = None


class LedgerOut(BaseModel):
    person_id: int | None = None
    total_in: str | None = None
    total_out: str | None = None
    unsettled: int = 0
    recent: list[LedgerItemOut] = []


# ── 五个只读接口 ─────────────────────────────────────────
@router.get("/today", response_model=DataResponse[TodayOut], summary="今天该做什么")
async def assistant_today(session: Session) -> dict[str, Any]:
    """今日页的精简版：只留模型组织回答需要的东西。"""
    user_id = session.info["rls_user_id"]
    view = await today_service.get_today(session, user_id)
    return ok(
        TodayOut(
            date=view.date.isoformat(),
            tasks=[
                TaskOut(
                    kind=item.type,
                    title=item.title,
                    why=item.subtitle,
                    person_id=item.person_id,
                    due=item.due.isoformat() if item.due else None,
                )
                for item in view.tasks
            ],
            pending_count=view.pending_count,
            degraded=view.degraded,
        )
    )


@router.get("/persons", response_model=DataResponse[PersonListOut], summary="找人")
async def assistant_persons(
    session: Session,
    keyword: Annotated[str | None, Query(description="昵称或别名的一部分")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    """按关键词找人。不传关键词就是最近更新的人。"""
    items, total = await person_service.list_persons(
        session, offset=0, limit=limit, keyword=keyword
    )
    return ok(
        PersonListOut(
            total=total,
            items=[
                PersonBrief(
                    id=item.person.id or 0,
                    nickname=item.person.nickname,
                    stage=item.relationship.stage,
                    tags=item.tags,
                    last_interaction_at=item.relationship.last_interaction_at.isoformat()
                    if item.relationship.last_interaction_at
                    else None,
                )
                for item in items
            ],
        )
    )


@router.get("/persons/{person_id}", response_model=DataResponse[PersonDetailOut], summary="档案")
async def assistant_person(session: Session, person_id: int) -> dict[str, Any]:
    """一个人的身份、关系、标签、四类计数。**不含事实列表**（那是另一个接口）。"""
    detail = await person_service.get_person_detail(session, person_id)
    relationship = detail.relationship
    birthday = relationship.birthday_solar.isoformat() if relationship.birthday_solar else None
    return ok(
        PersonDetailOut(
            id=detail.person.id or 0,
            nickname=detail.person.nickname,
            aliases=[str(item) for item in (detail.person.aliases or [])],
            stage=relationship.stage,
            roles=[str(item) for item in (relationship.roles or [])],
            birthday=birthday,
            taboo=[str(item) for item in (relationship.taboo or [])],
            tags=detail.tags,
            note=detail.person.note,
            total_in=str(relationship.total_in),
            total_out=str(relationship.total_out),
            counts=detail.counts,
            dates=[
                PersonDateOut(**item)
                for item in await assistant_service.person_dates(session, person_id)
            ],
        )
    )


@router.get(
    "/persons/{person_id}/facts",
    response_model=DataResponse[FactListOut],
    summary="一个人的事实",
)
async def assistant_facts(session: Session, person_id: int) -> dict[str, Any]:
    """只返回**已确认**的事实，按权重降序。

    这一条是 19.7.5 的落地：模型必须每次来这儿读，不许把事实记进自己的 memory。
    """
    detail = await person_service.get_person_detail(session, person_id)
    facts = await profile_service.facts_of(session, person_id)
    return ok(
        FactListOut(
            person_id=person_id,
            nickname=detail.person.nickname,
            items=[
                FactOut(
                    content=fact.content,
                    category=fact.category,
                    weight=fact.weight,
                    evidence=fact.evidence,
                    confirmed_at=fact.last_confirmed_at.isoformat()
                    if fact.last_confirmed_at
                    else None,
                )
                for fact in facts
            ],
        )
    )


def _ledger_out(rows: list[Ledger], detail: PersonDetailData, person_id: int) -> LedgerOut:
    """把逐笔明细 + 人员汇总映射成助理响应。"""
    return LedgerOut(
        person_id=person_id,
        total_in=str(detail.relationship.total_in),
        total_out=str(detail.relationship.total_out),
        unsettled=sum(1 for row in rows if row.settled_at is None),
        recent=[
            LedgerItemOut(
                direction=row.direction,
                kind=row.kind,
                amount=str(row.amount),
                occurred_at=row.occurred_at.isoformat(),
                due_date=row.due_date.isoformat() if row.due_date else None,
                note=row.note,
            )
            for row in rows
        ],
    )


@router.get("/ledger", response_model=DataResponse[LedgerOut], summary="某人的往来汇总")
async def assistant_ledger(
    session: Session,
    person_id: Annotated[int, Query(description="必须指定人")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """逐笔明细 + 收/支合计 + 未结清笔数。

    **person_id 必填**：跨人加总没有业务含义（不是"我的钱"），
    助理真正需要的是"我跟王姐还差多少"，那必须带人。
    """
    rows, _total = await ledger_service.list_ledger(session, person_id, offset=0, limit=limit)
    detail = await person_service.get_person_detail(session, person_id)
    return ok(_ledger_out(rows, detail, person_id))


# ── RAG 检索（2026-11-04 新增）──────────────────────────
class SearchItemOut(BaseModel):
    content: str
    source_type: str
    source_id: int | None = None
    person_id: int | None = None
    scope: str
    score: float


class SearchOut(BaseModel):
    query: str
    semantic: bool
    items: list[SearchItemOut]


@router.get("/search", response_model=DataResponse[SearchOut], summary="检索资料（RAG）")
async def assistant_search(
    session: Session,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> dict[str, Any]:
    """按语义（或关键词）找"哪份材料提过这件事"。

    `semantic=false` 表示没配向量模型、这次走的是关键词匹配：
    模型看到这个字段就该知道"没搜到不代表没记过"，而不是断言"你没提过"。
    """
    items = await rag_service.search(session, q, limit=limit)
    return ok(
        SearchOut(
            query=q,
            semantic=embedding_service.available(),
            items=[SearchItemOut(**item) for item in items],
        )
    )


# ── 写操作（2026-11-04：Harness 这条路上模型也要能建东西）──────
# 为什么只有一个端点、不按动作分三个：
# 写操作的校验（人名解析、日期解析、类型白名单、上限）全在
# `conversation.execute_actions` 里，那是内置对话与 Harness **共用**的一份实现。
# 在这里再拆三个端点，等于把同一套规则抄三遍，迟早有两份不一致。
class ActionIn(BaseModel):
    """一条动作。字段名与 `execute_actions` 认的 payload 完全一致。"""

    type: str = Field(description="create_todo / create_event / add_fact")
    title: str | None = None
    person: str | None = Field(default=None, description="对方昵称，认不出就不会建")
    due_on: str | None = None
    event_type: str | None = None
    occurs_on: str | None = None
    content: str | None = None
    category: str | None = None


class ActionResultOut(BaseModel):
    type: str
    ok: bool
    summary: str
    ref_id: int | None = None
    reason: str | None = None


@router.post(
    "/actions",
    response_model=DataResponse[ActionResultOut],
    summary="执行一条写操作（建待办 / 建事件 / 记事实）",
)
async def assistant_action(session: Session, payload: ActionIn) -> dict[str, Any]:
    """白名单之外的类型直接拒（§19.7.10）。记事实只落 pending，不进档案。

    返回 `ok=false` 时 `reason` 是给模型看的原因（"没认出是哪个人"），
    模型据此反问用户，而不是硬编一个。
    """
    user_id = int(session.info.get("rls_user_id") or 0)
    result = await conversation_service.run_single_action(
        session, user_id, payload.model_dump(exclude_none=True)
    )
    return ok(ActionResultOut(**result.as_dict()))
