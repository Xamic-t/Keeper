"""人模块路由：列表 / 档案 / 建 / 改 / 改关系 / 删。

路由只做三件事：接参数、调 service、包响应。每个函数不超过 15 行。
请求/响应模型写在文件顶部（接口契约跟路由走，见 ARCHITECTURE 11.3.1）。
对应文档：specs/02-persons.md 5.2
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field, PlainSerializer, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import DataResponse, PageResponse, ok, page_meta
from app.deps import (
    CurrentUser,
    Pagination,
    get_current_user,
    get_session,
    require_csrf,
)
from app.models.person import Person
from app.models.relationship import Relationship
from app.services import person as person_service
from app.services.person import PersonDetailData, PersonSummary

router = APIRouter(prefix="/persons", tags=["persons"])

# V1 只有两种关系角色（docs/02 2.5：一个人可同时是哥姐和客户）
Role = Literal["ge_jie", "customer"]
Stage = Literal["new", "familiar", "core", "cold"]
BirthdayType = Literal["solar", "lunar"]

# 金额按字符串输出 "12400.00"，前端不做浮点运算（docs/10 第 3.2 节）
Money = Annotated[
    Decimal,
    PlainSerializer(lambda value: f"{value:.2f}", return_type=str, when_used="json"),
]


# ---------- 请求模型 ----------


class LunarBirthday(BaseModel):
    """农历生日。换算成公历是 W8 的事，这里只负责存对。"""

    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=30)
    is_leap: bool = False


class PersonCreate(BaseModel):
    """建人。roles 不传默认哥姐。"""

    nickname: str = Field(min_length=1, max_length=50)
    roles: list[Role] = Field(default_factory=lambda: ["ge_jie"])
    aliases: list[str] | None = None
    platform_ids: dict[str, str] | None = None
    note: str | None = Field(default=None, max_length=2000)


class PersonUpdate(BaseModel):
    """改身份信息。只改传了的字段（PATCH 语义）。"""

    nickname: str | None = Field(default=None, min_length=1, max_length=50)
    aliases: list[str] | None = None
    platform_ids: dict[str, str] | None = None
    contact: dict[str, str] | None = None
    note: str | None = Field(default=None, max_length=2000)


class RelationshipUpdate(BaseModel):
    """改关系状态。改 stage 会自动打上 manual 标记。"""

    roles: list[Role] | None = None
    stage: Stage | None = None
    birthday_type: BirthdayType | None = None
    birthday_solar: date | None = None
    birthday_lunar: LunarBirthday | None = None
    gift_budget_min: Decimal | None = Field(default=None, ge=0)
    gift_budget_max: Decimal | None = Field(default=None, ge=0)
    taboo: list[str] | None = None

    @model_validator(mode="after")
    def _birthday_type_needs_value(self) -> "RelationshipUpdate":
        """生日类型和生日值必须配套，不能只写类型（specs/02 6.2）。"""
        if self.birthday_type == "solar" and self.birthday_solar is None:
            raise ValueError("birthday_type=solar 时必须同时给 birthday_solar")
        if self.birthday_type == "lunar" and self.birthday_lunar is None:
            raise ValueError("birthday_type=lunar 时必须同时给 birthday_lunar")
        return self


# ---------- 响应模型 ----------


class PersonListItem(BaseModel):
    """列表一行：6 个字段，不查关联明细。"""

    id: int
    nickname: str
    avatar_path: str | None = None
    stage: str
    stage_source: str
    tags: list[str]
    last_interaction_at: datetime | None = None


class RelationshipOut(BaseModel):
    """关系状态。前端档案页直接渲染这几块。"""

    roles: list[str]
    stage: str
    stage_source: str
    birthday_type: BirthdayType | None = None
    birthday_solar: date | None = None
    birthday_lunar: dict[str, Any] | None = None
    gift_budget_min: Money | None = None
    gift_budget_max: Money | None = None
    taboo: list[str]
    total_in: Money
    total_out: Money
    last_interaction_at: datetime | None = None


class PersonOut(BaseModel):
    """创建 / 更新后返回的完整对象——前端不用再发一次 GET。"""

    id: int
    nickname: str
    aliases: list[str]
    platform_ids: dict[str, Any]
    avatar_path: str | None = None
    contact: dict[str, Any]
    note: str | None = None
    created_at: datetime
    updated_at: datetime
    relationship: RelationshipOut


class PersonCounts(BaseModel):
    """档案页四个计数。W2 只有计数，明细在各板块。"""

    facts: int
    ledger: int
    events: int
    todos: int


class PersonDetail(PersonOut):
    """档案详情。tags 是标签值列表，summary 留给 W7 画像生成。"""

    tags: list[str]
    counts: PersonCounts
    summary: str | None = None


# ---------- 转换（model → 响应模型） ----------


def _relationship_out(relationship: Relationship) -> RelationshipOut:
    return RelationshipOut(
        roles=relationship.roles,
        stage=relationship.stage,
        stage_source=relationship.stage_source,
        birthday_type=relationship.birthday_type,  # type: ignore[arg-type]
        birthday_solar=relationship.birthday_solar,
        birthday_lunar=relationship.birthday_lunar,
        gift_budget_min=relationship.gift_budget_min,
        gift_budget_max=relationship.gift_budget_max,
        taboo=relationship.taboo,
        total_in=relationship.total_in,
        total_out=relationship.total_out,
        last_interaction_at=relationship.last_interaction_at,
    )


def _person_out(person: Person, relationship: Relationship) -> PersonOut:
    if person.id is None:
        raise ValueError("person.id 为空：数据库里读出来的行必有 id")
    return PersonOut(
        id=person.id,
        nickname=person.nickname,
        aliases=person.aliases,
        platform_ids=person.platform_ids,
        avatar_path=person.avatar_path,
        contact=person.contact,
        note=person.note,
        created_at=person.created_at,
        updated_at=person.updated_at,
        relationship=_relationship_out(relationship),
    )


def _list_item(summary: PersonSummary) -> PersonListItem:
    if summary.person.id is None:
        raise ValueError("person.id 为空：数据库里读出来的行必有 id")
    return PersonListItem(
        id=summary.person.id,
        nickname=summary.person.nickname,
        avatar_path=summary.person.avatar_path,
        stage=summary.relationship.stage,
        stage_source=summary.relationship.stage_source,
        tags=summary.tags,
        last_interaction_at=summary.relationship.last_interaction_at,
    )


def _detail(data: PersonDetailData) -> PersonDetail:
    base = _person_out(data.person, data.relationship)
    return PersonDetail(
        **base.model_dump(),
        tags=data.tags,
        counts=PersonCounts(**data.counts),
    )


# ---------- 路由 ----------


@router.get("", response_model=PageResponse[PersonListItem], summary="人列表")
async def list_persons(
    pagination: Annotated[Pagination, Query()],
    session: Annotated[AsyncSession, Depends(get_session)],
    keyword: Annotated[str | None, Query(max_length=50, description="搜昵称或别名")] = None,
) -> dict[str, Any]:
    """分页查人。空列表返回 200，不是 404。"""
    items, total = await person_service.list_persons(
        session,
        offset=pagination.offset,
        limit=pagination.size,
        keyword=keyword,
    )
    meta = page_meta(total, pagination.page, pagination.size)
    return ok([_list_item(item) for item in items], meta)


@router.get("/{person_id}", response_model=DataResponse[PersonDetail], summary="档案详情")
async def get_person(
    person_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """聚合档案：身份 + 关系 + 标签 + 四项计数。越权返回 404。"""
    return ok(_detail(await person_service.get_person_detail(session, person_id)))


@router.post(
    "",
    response_model=DataResponse[PersonOut],
    status_code=status.HTTP_201_CREATED,
    summary="新建人",
    dependencies=[Depends(require_csrf)],
)
async def create_person(
    payload: PersonCreate,
    response: Response,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """建人 + 建关系（同一事务）。返回完整对象和 Location 头。"""
    summary = await person_service.create_person(
        session,
        user.id,
        nickname=payload.nickname,
        roles=list(payload.roles),
        aliases=payload.aliases,
        platform_ids=payload.platform_ids,
        note=payload.note,
    )
    response.headers["Location"] = f"/api/persons/{summary.person.id}"
    return ok(_person_out(summary.person, summary.relationship))


@router.patch(
    "/{person_id}",
    response_model=DataResponse[PersonOut],
    summary="改身份信息",
    dependencies=[Depends(require_csrf)],
)
async def update_person(
    person_id: int,
    payload: PersonUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """只改传了的字段。返回更新后的完整对象，不是只有改动的字段。"""
    summary = await person_service.update_person(
        session,
        person_id,
        payload.model_dump(exclude_unset=True),
    )
    return ok(_person_out(summary.person, summary.relationship))


@router.patch(
    "/{person_id}/relationship",
    response_model=DataResponse[RelationshipOut],
    summary="改关系状态",
    dependencies=[Depends(require_csrf)],
)
async def update_relationship(
    person_id: int,
    payload: RelationshipUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """改阶段 / 角色 / 生日 / 预算 / 禁忌。改 stage 后自动算法不再覆盖。"""
    relationship = await person_service.update_relationship(
        session,
        person_id,
        payload.model_dump(exclude_unset=True),
    )
    return ok(_relationship_out(relationship))


@router.delete(
    "/{person_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除（级联）",
    dependencies=[Depends(require_csrf)],
)
async def delete_person(
    person_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """真删：级联 10 张表 + 删文件，待办保留但失去归属。204 无 body。"""
    await person_service.delete_person(session, person_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
