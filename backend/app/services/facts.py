"""事实 / 标签的业务规则。API 层只调这里，不直接碰数据库。"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.fact import Fact, Tag
from app.models.person import Person
from app.services import profile as profile_service


async def update_fact(
    session: AsyncSession,
    fact_id: int,
    *,
    content: str | None,
    category: str | None,
    weight: float | None,
) -> Fact:
    """改事实内容/类别/权重。已确认的事实不能改类别（要改先驳回）。"""
    fact = await session.get(Fact, fact_id)
    if fact is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这条事实", status_code=404)
    if category is not None and fact.status == "confirmed":
        raise AppError(ErrorCode.INVALID_STATE, "已确认的事实不能改类别，请先驳回", status_code=400)

    if content is not None:
        fact.content = content
    if category is not None:
        fact.category = category
    if weight is not None:
        fact.weight = weight
    session.add(fact)
    await session.commit()
    return fact


async def delete_fact(session: AsyncSession, fact_id: int) -> None:
    """删一条事实。确认过的事实删掉后档案会变薄。"""
    fact = await session.get(Fact, fact_id)
    if fact is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这条事实", status_code=404)
    await session.delete(fact)
    await session.commit()


async def list_tags(session: AsyncSession, *, key: str | None, person_id: int | None) -> list[Tag]:
    """未过期的标签，用于“谁喜欢茶”这类筛选。"""
    now = datetime.now(UTC)
    stmt = select(Tag).where((Tag.expires_at.is_(None)) | (Tag.expires_at > now))
    if key:
        stmt = stmt.where(Tag.key == key)
    if person_id is not None:
        stmt = stmt.where(Tag.person_id == person_id)
    stmt = stmt.order_by(Tag.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())


async def rewrite_summary(session: AsyncSession, person_id: int) -> tuple[str, bool]:
    """重写综述，返回（新综述, 是否仍标记过期）。失败时保留旧综述。"""
    summary = await profile_service.rewrite_summary(session, person_id)
    person = await session.get(Person, person_id)
    stale = bool(person.summary_stale) if person else True
    return summary, stale
