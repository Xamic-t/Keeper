"""管理接口：账号列表 / 建号 / 禁用或重置密码（仅 owner）。

对应文档：docs/07-后端设计.md 5.1（管理）

规则：
  · 不能删自己
  · 不能禁用最后一个 owner
  · 新建账号只给初始密码，用户自己改
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.response import DataResponse, ok
from app.core.security import hash_password
from app.deps import CurrentUser, get_session, require_owner
from app.models.user import User

router = APIRouter(prefix="/admin/users", tags=["admin"])

INITIAL_PASSWORD = "keeper-2026"


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
    password: str = Field(default=INITIAL_PASSWORD, min_length=8, max_length=128)


class UserUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    role: Literal["owner", "member"] | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


@router.get("", response_model=DataResponse[list[UserOut]], summary="账号列表")
async def list_users(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[CurrentUser, Depends(require_owner)] = None,
) -> dict[str, Any]:
    """列出全部账号（只有 owner 能看）。"""
    stmt = select(User).order_by(User.id)
    users = list((await session.execute(stmt)).scalars().all())
    return ok(
        [
            UserOut(
                id=user.id or 0,
                username=user.username,
                display_name=user.display_name,
                role=user.role,
                is_active=user.is_active,
                last_login_at=user.last_login_at,
                created_at=user.created_at,
            )
            for user in users
        ]
    )


@router.post(
    "",
    response_model=DataResponse[UserOut],
    status_code=status.HTTP_201_CREATED,
    summary="建号",
)
async def create_user(
    payload: UserCreateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[CurrentUser, Depends(require_owner)] = None,
) -> dict[str, Any]:
    """新建账号。用户名重复返回 409。"""
    existing = (
        await session.execute(select(User).where(User.username == payload.username.strip()))
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError(
            ErrorCode.CONFLICT,
            "这个登录名已经有了",
            status_code=409,
            detail={"field": "username"},
        )

    user = User(
        username=payload.username.strip(),
        display_name=payload.display_name,
        role=payload.role,
        password_hash=hash_password(payload.password),
        is_active=True,
    )
    session.add(user)
    await session.commit()

    return ok(
        UserOut(
            id=user.id or 0,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            is_active=user.is_active,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
        )
    )


@router.patch(
    "/{user_id}",
    response_model=DataResponse[UserOut],
    summary="禁用 / 重置密码",
)
async def update_user(
    user_id: int,
    payload: UserUpdateIn,
    actor: Annotated[CurrentUser, Depends(require_owner)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """改账号状态或重置密码。"""
    user = await session.get(User, user_id)
    if user is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个账号", status_code=404)

    if payload.is_active is False:
        if user.id == actor.id:
            raise AppError(ErrorCode.INVALID_STATE, "不能禁用自己", status_code=400)
        if user.role == "owner":
            remaining = (
                await session.execute(
                    select(func.count())
                    .select_from(User)
                    .where(User.role == "owner", User.is_active.is_(True), User.id != user_id)
                )
            ).scalar_one()
            if remaining == 0:
                raise AppError(ErrorCode.INVALID_STATE, "不能禁用最后一个 owner", status_code=400)

    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
        user.failed_attempts = 0
        user.locked_until = None

    session.add(user)
    await session.commit()

    return ok(
        UserOut(
            id=user.id or 0,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            is_active=user.is_active,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
        )
    )
