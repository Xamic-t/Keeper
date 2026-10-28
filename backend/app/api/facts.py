"""事实与标签路由。

对应文档：specs/06-profile.md
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.response import DataResponse, ok
from app.deps import CurrentUser, get_current_user, get_session, require_csrf
from app.models.fact import Fact, Tag
from app.models.person import Person
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
    return ok(
        [
            FactOut(
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
            for fact in facts
        ]
    )


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
    fact = await session.get(Fact, fact_id)
    if fact is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这条事实", status_code=404)
    if payload.category is not None and fact.status == "confirmed":
        raise AppError(ErrorCode.INVALID_STATE, "已确认的事实不能改类别，请先驳回", status_code=400)

    if payload.content is not None:
        fact.content = payload.content
    if payload.category is not None:
        fact.category = payload.category
    if payload.weight is not None:
        fact.weight = payload.weight
    session.add(fact)
    await session.commit()

    return ok(
        FactOut(
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
    )


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
    fact = await session.get(Fact, fact_id)
    if fact is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这条事实", status_code=404)
    await session.delete(fact)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/tags", response_model=DataResponse[list[TagOut]], summary="全部标签")
async def list_tags(
    session: Annotated[AsyncSession, Depends(get_session)],
    key: str | None = None,
    person_id: int | None = None,
) -> dict[str, Any]:
    """标签列表，用于筛选（"谁喜欢茶"）。过期的标签不返回。"""
    now = datetime.now(UTC)
    stmt = select(Tag).where((Tag.expires_at.is_(None)) | (Tag.expires_at > now))
    if key:
        stmt = stmt.where(Tag.key == key)
    if person_id is not None:
        stmt = stmt.where(Tag.person_id == person_id)
    stmt = stmt.order_by(Tag.created_at.desc())

    tags = list((await session.execute(stmt)).scalars().all())
    return ok(
        [
            TagOut(
                id=tag.id or 0,
                person_id=tag.person_id,
                key=tag.key,
                value=tag.value,
                confidence=tag.confidence,
                expires_at=tag.expires_at,
            )
            for tag in tags
        ]
    )


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
    summary = await profile_service.rewrite_summary(session, person_id)
    person = await session.get(Person, person_id)
    return ok(
        SummaryOut(
            person_id=person_id,
            summary=summary,
            stale=bool(person.summary_stale) if person else True,
        )
    )
