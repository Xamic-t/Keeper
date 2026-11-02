"""事实与标签路由。

对应文档：specs/06-profile.md
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import DataResponse, ok
from app.deps import CurrentUser, get_current_user, get_session, require_csrf
from app.models.fact import Fact, Tag
from app.services import facts as facts_service
from app.services import profile as profile_service

router = APIRouter(tags=["facts"])


class FactOut(BaseModel):
    id: int
    person_id: int
    content: str
    category: str
    weight: float
    confidence: float
    evidence: str | None = None
    status: str
    last_confirmed_at: datetime | None = None
    created_at: datetime


class TagOut(BaseModel):
    id: int
    person_id: int
    key: str
    value: str
    confidence: float | None = None
    expires_at: datetime | None = None


class FactUpdateIn(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    category: (
        Literal["permanent", "taboo", "structure", "preference", "situation", "mood"] | None
    ) = None
    weight: float | None = Field(default=None, ge=0, le=1)


class SummaryOut(BaseModel):
    person_id: int
    summary: str
    stale: bool


def _fact_out(fact: Fact) -> FactOut:
    return FactOut(
        id=fact.id or 0,
        person_id=fact.person_id,
        content=fact.content,
        category=fact.category,
        weight=fact.weight,
        confidence=fact.confidence,
        evidence=fact.evidence,
        status=fact.status,
        last_confirmed_at=fact.last_confirmed_at,
        created_at=fact.created_at,
    )


def _tag_out(tag: Tag) -> TagOut:
    return TagOut(
        id=tag.id or 0,
        person_id=tag.person_id,
        key=tag.key,
        value=tag.value,
        confidence=tag.confidence,
        expires_at=tag.expires_at,
    )


@router.get(
    "/persons/{person_id}/facts",
    response_model=DataResponse[list[FactOut]],
    summary="某人的事实",
)
async def list_facts(
    person_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    include_pending: bool = False,
) -> dict[str, Any]:
    """档案页的事实列表。默认只给已确认的。"""
    facts = await profile_service.facts_of(session, person_id, include_pending=include_pending)
    return ok([_fact_out(fact) for fact in facts])


@router.patch(
    "/facts/{fact_id}",
    response_model=DataResponse[FactOut],
    summary="改事实",
    dependencies=[Depends(require_csrf)],
)
async def update_fact(
    fact_id: int,
    payload: FactUpdateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """改事实内容/类别/权重。已确认的事实不能改类别（要改先驳回）。"""
    fact = await facts_service.update_fact(
        session,
        fact_id,
        content=payload.content,
        category=payload.category,
        weight=payload.weight,
    )
    return ok(_fact_out(fact))


@router.delete(
    "/facts/{fact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删事实",
    dependencies=[Depends(require_csrf)],
)
async def delete_fact(
    fact_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """删一条事实。确认过的事实删掉后档案会变薄。"""
    await facts_service.delete_fact(session, fact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/tags", response_model=DataResponse[list[TagOut]], summary="全部标签")
async def list_tags(
    session: Annotated[AsyncSession, Depends(get_session)],
    key: str | None = None,
    person_id: int | None = None,
) -> dict[str, Any]:
    """标签列表，用于筛选（"谁喜欢茶"）。过期的标签不返回。"""
    tags = await facts_service.list_tags(session, key=key, person_id=person_id)
    return ok([_tag_out(tag) for tag in tags])


@router.post(
    "/persons/{person_id}/summary/rewrite",
    response_model=DataResponse[SummaryOut],
    summary="重写人物综述",
    dependencies=[Depends(require_csrf)],
)
async def rewrite_summary(
    person_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """调模型重写综述。失败时保留旧综述。"""
    summary, stale = await facts_service.rewrite_summary(session, person_id)
    return ok(SummaryOut(person_id=person_id, summary=summary, stale=stale))
