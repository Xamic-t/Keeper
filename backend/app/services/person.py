"""人的业务逻辑：列表、档案、建人、改人、改关系、删人。

对应文档：specs/02-persons.md 5.1

三条不能动的规则：
  ① 列表和对象是两个东西（不同返回结构，别混用）
  ② 建人必须同时建关系，两个 insert 在同一事务
  ③ 删人是真删：级联 10 张表 + 删文件，唯一例外是待办（person_id 置 NULL）

查询一律不写 where user_id —— RLS 会加（ARCHITECTURE 15.5）。
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import String, cast, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.event import Event
from app.models.fact import Fact, Tag
from app.models.input import RawInput
from app.models.ledger import Ledger
from app.models.person import Person
from app.models.relationship import Relationship
from app.models.todo import Todo

logger = logging.getLogger(__name__)

# PATCH 允许改的字段白名单。user_id 故意不在里面——所有权不能改。
PERSON_EDITABLE = ("nickname", "aliases", "platform_ids", "contact", "note")
RELATIONSHIP_EDITABLE = (
    "roles",
    "stage",
    "birthday_solar",
    "birthday_lunar",
    "birthday_type",
    "gift_budget_min",
    "gift_budget_max",
    "taboo",
    "is_active",
)


@dataclass(frozen=True, slots=True)
class PersonSummary:
    """列表一行需要的东西：人的身份 + 关系状态 + 标签值。"""

    person: Person
    relationship: Relationship
    tags: list[str]


@dataclass(frozen=True, slots=True)
class PersonDetailData:
    """档案页需要的全部数据。"""

    person: Person
    relationship: Relationship
    tags: list[str]
    counts: dict[str, int]


def _escape_like(keyword: str) -> str:
    """转义 LIKE 通配符，避免用户输入 % 或 _ 时把全表搜出来。"""
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _tags_of(session: AsyncSession, person_ids: list[int]) -> dict[int, list[str]]:
    """批量取标签（一次查询，不循环查）。过期标签不返回。"""
    if not person_ids:
        return {}

    now = datetime.now(UTC)
    stmt = (
        select(Tag.person_id, Tag.value)
        .where(Tag.person_id.in_(person_ids))
        .where(or_(Tag.expires_at.is_(None), Tag.expires_at > now))
        .order_by(Tag.created_at)
    )
    grouped: dict[int, list[str]] = {}
    for person_id, value in (await session.execute(stmt)).all():
        grouped.setdefault(person_id, []).append(value)
    return grouped


async def _relationship_of(session: AsyncSession, person_id: int) -> Relationship:
    """取关系记录。1:1 是设计保证，缺了说明数据坏了，直接报错不静默补。"""
    stmt = select(Relationship).where(Relationship.person_id == person_id)
    relationship = (await session.execute(stmt)).scalar_one_or_none()
    if relationship is None:
        logger.error("relationship missing person_id=%s", person_id)
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "档案数据不一致，请联系管理员",
            status_code=500,
        )
    return relationship


async def list_persons(
    session: AsyncSession,
    *,
    offset: int,
    limit: int,
    keyword: str | None = None,
) -> tuple[list[PersonSummary], int]:
    """分页查人，关键词同时搜昵称和别名。返回 (当页数据, 总数)。"""
    rows_stmt = select(Person, Relationship).join(Relationship, Relationship.person_id == Person.id)
    count_stmt = select(func.count()).select_from(Person)

    text_keyword = (keyword or "").strip()
    if text_keyword:
        pattern = f"%{_escape_like(text_keyword)}%"
        condition = or_(
            Person.nickname.ilike(pattern, escape="\\"),
            cast(Person.aliases, String).ilike(pattern, escape="\\"),
        )
        rows_stmt = rows_stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    rows_stmt = (
        rows_stmt.order_by(Person.updated_at.desc(), Person.id.desc()).offset(offset).limit(limit)
    )
    rows = (await session.execute(rows_stmt)).all()
    total = (await session.execute(count_stmt)).scalar_one()

    tag_map = await _tags_of(session, [person.id for person, _ in rows if person.id is not None])
    items = [
        PersonSummary(
            person=person,
            relationship=relationship,
            tags=tag_map.get(person.id or 0, []),
        )
        for person, relationship in rows
    ]
    return items, total


async def get_person_detail(session: AsyncSession, person_id: int) -> PersonDetailData:
    """一次拿齐档案页数据：身份 + 关系 + 标签 + 四类计数。"""
    person = await session.get(Person, person_id)
    if person is None:
        # 不存在与越权返回同一个 404——不告诉对方"这个资源存在"
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个人", status_code=404)

    relationship = await _relationship_of(session, person_id)
    tag_map = await _tags_of(session, [person_id])

    counts: dict[str, int] = {}
    for name, model in (
        ("facts", Fact),
        ("ledger", Ledger),
        ("events", Event),
        ("todos", Todo),
    ):
        stmt = select(func.count()).select_from(model).where(model.person_id == person_id)
        counts[name] = (await session.execute(stmt)).scalar_one()

    return PersonDetailData(
        person=person,
        relationship=relationship,
        tags=tag_map.get(person_id, []),
        counts=counts,
    )


async def create_person(
    session: AsyncSession,
    user_id: int,
    *,
    nickname: str,
    roles: list[str],
    aliases: list[str] | None = None,
    platform_ids: dict[str, Any] | None = None,
    note: str | None = None,
) -> PersonSummary:
    """建人 + 建关系。两个 insert 在同一事务里，第二个失败要回滚第一个。"""
    person = Person(
        user_id=user_id,
        nickname=nickname.strip(),
        aliases=aliases or [],
        platform_ids=platform_ids or {},
        note=note,
    )
    session.add(person)
    await session.flush()  # 拿到自增 id，关系记录要用

    relationship = Relationship(user_id=user_id, person_id=person.id, roles=roles)
    session.add(relationship)

    # 一次提交让两条记录同时生效。注意：提交后 RLS 变量失效，不能再查库
    await session.commit()

    return PersonSummary(person=person, relationship=relationship, tags=[])


async def update_person(
    session: AsyncSession,
    person_id: int,
    changes: dict[str, Any],
) -> PersonSummary:
    """改身份信息。只改 changes 里出现过的字段（PATCH 语义）。"""
    person = await session.get(Person, person_id)
    if person is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个人", status_code=404)

    values = {
        key: value
        for key, value in changes.items()
        if key in PERSON_EDITABLE and (value is not None or key == "note")
    }
    if not values:
        # 空 PATCH 不报错，原样返回
        tags = (await _tags_of(session, [person_id])).get(person_id, [])
        return PersonSummary(
            person=person,
            relationship=await _relationship_of(session, person_id),
            tags=tags,
        )

    if "nickname" in values:
        values["nickname"] = str(values["nickname"]).strip()

    # RETURNING 拿回数据库生成的 updated_at，避免提交后再查（提交后 RLS 变量已失效）
    stmt = update(Person).where(Person.id == person_id).values(**values).returning(Person)
    updated = (await session.execute(stmt)).scalar_one()

    tags = (await _tags_of(session, [person_id])).get(person_id, [])
    relationship = await _relationship_of(session, person_id)
    await session.commit()

    return PersonSummary(person=updated, relationship=relationship, tags=tags)


async def update_relationship(
    session: AsyncSession,
    person_id: int,
    changes: dict[str, Any],
) -> Relationship:
    """改关系状态。手改了 stage 就打上 manual 标记，自动算法以后不再覆盖。"""
    # 先确认这个人存在（RLS 会过滤别人的人），否则会把"查不到人"误报成 500
    if await session.get(Person, person_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个人", status_code=404)

    relationship = await _relationship_of(session, person_id)

    values = {
        key: value
        for key, value in changes.items()
        if key in RELATIONSHIP_EDITABLE and value is not None
    }
    if not values:
        return relationship

    if "stage" in values:
        values["stage_source"] = "manual"
        values["stage_updated_at"] = datetime.now(UTC)

    stmt = (
        update(Relationship)
        .where(Relationship.person_id == person_id)
        .values(**values)
        .returning(Relationship)
    )
    updated = (await session.execute(stmt)).scalar_one()
    await session.commit()
    return updated


async def delete_person(session: AsyncSession, person_id: int) -> None:
    """真删一个人：级联 10 张表，最后删磁盘文件。

    顺序不能反：先查文件路径（删完就查不到了）→ 删库并提交 → 删文件。
    文件在事务外删——文件系统不支持回滚。
    """
    person = await session.get(Person, person_id)
    if person is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个人", status_code=404)

    file_stmt = select(RawInput.file_path).where(
        RawInput.person_id == person_id,
        RawInput.file_path.is_not(None),
    )
    file_paths = [row for row in (await session.execute(file_stmt)).scalars().all() if row]

    # 数据库级联：relationships / facts / tags / ledger / events / reminders /
    # raw_inputs / chat_segments；todos.person_id 由外键 SET NULL 置空
    await session.execute(delete(Person).where(Person.id == person_id))
    await session.commit()

    for file_path in file_paths:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError as exc:
            # 数据已删，孤儿文件可后续清理；不回滚
            logger.warning(
                "delete file failed person_id=%s error=%s", person_id, type(exc).__name__
            )
