"""管理接口：账号列表 / 建号 / 禁用或重置密码（仅 owner）。

对应文档：docs/07-后端设计.md 5.1（管理）

规则：
  · 不能删自己
  · 不能禁用最后一个 owner
  · 建号必须由调用方指定初始密码（仓库里不留任何默认口令）

业务规则在 `services/admin.py`；本文件只做参数与响应映射。
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import DataResponse, ok
from app.deps import CurrentUser, get_session, require_csrf, require_owner
from app.models.user import User
from app.services import admin as admin_service

router = APIRouter(prefix="/admin/users", tags=["admin"])


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str | None = None
    role: str
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime


class UserCreateIn(BaseModel):
    username: str = Field(min_length=1, max_length=100, description="用户名 / 手机号 / 邮箱")
    display_name: str | None = Field(default=None, max_length=100)
    role: Literal["owner", "member"] = "member"
    # 以前这里有个写死的默认口令，而仓库是公开的 —— 等于公开了建号口令（评审 P2-1）。
    # 现在必须显式传，没有默认值。
    password: str = Field(min_length=8, max_length=128, description="初始密码，必须由调用方指定")


class UserUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    role: Literal["owner", "member"] | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id or 0,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.get("", response_model=DataResponse[list[UserOut]], summary="账号列表")
async def list_users(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[CurrentUser, Depends(require_owner)] = None,
) -> dict[str, Any]:
    """列出全部账号（只有 owner 能看）。"""
    return ok([_user_out(user) for user in await admin_service.list_users(session)])


@router.post(
    "",
    response_model=DataResponse[UserOut],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
    summary="建号",
)
async def create_user(
    payload: UserCreateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[CurrentUser, Depends(require_owner)] = None,
) -> dict[str, Any]:
    """新建账号。用户名重复返回 409。"""
    user = await admin_service.create_user(
        session,
        username=payload.username,
        display_name=payload.display_name,
        role=payload.role,
        password=payload.password,
    )
    return ok(_user_out(user))


@router.patch(
    "/{user_id}",
    response_model=DataResponse[UserOut],
    dependencies=[Depends(require_csrf)],
    summary="禁用 / 重置密码",
)
async def update_user(
    user_id: int,
    payload: UserUpdateIn,
    actor: Annotated[CurrentUser, Depends(require_owner)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """改账号状态或重置密码。"""
    user = await admin_service.update_user(
        session,
        user_id,
        actor_id=actor.id,
        display_name=payload.display_name,
        is_active=payload.is_active,
        role=payload.role,
        password=payload.password,
    )
    return ok(_user_out(user))
