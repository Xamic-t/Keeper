"""管理账号的业务规则。API 层只取参数、调这里、返回。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.security import hash_password
from app.models.user import User


async def list_users(session: AsyncSession) -> list[User]:
    """全部账号，按 id 排序。"""
    stmt = select(User).order_by(User.id)
    return list((await session.execute(stmt)).scalars().all())


async def _count_other_active_owners(session: AsyncSession, user_id: int) -> int:
    """除指定账号外，还剩几个可用 owner。"""
    return (
        await session.execute(
            select(func.count())
            .select_from(User)
            .where(User.role == "owner", User.is_active.is_(True), User.id != user_id)
        )
    ).scalar_one()


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    display_name: str | None,
    role: str,
    password: str,
) -> User:
    """新建账号；登录名重复返回 409。"""
    normalized = username.strip()
    existing = (
        await session.execute(select(User).where(User.username == normalized))
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError(
            ErrorCode.CONFLICT,
            "这个登录名已经有了",
            status_code=409,
            detail={"field": "username"},
        )

    user = User(
        username=normalized,
        display_name=display_name,
        role=role,
        password_hash=hash_password(password),
        is_active=True,
    )
    session.add(user)
    await session.commit()
    return user


async def update_user(
    session: AsyncSession,
    user_id: int,
    *,
    actor_id: int,
    display_name: str | None = None,
    is_active: bool | None = None,
    role: str | None = None,
    password: str | None = None,
) -> User:
    """改账号状态、角色或密码；守住最后一个 owner。"""
    user = await session.get(User, user_id)
    if user is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个账号", status_code=404)

    if is_active is False:
        if user.id == actor_id:
            raise AppError(ErrorCode.INVALID_STATE, "不能禁用自己", status_code=400)
        if user.role == "owner" and await _count_other_active_owners(session, user_id) == 0:
            raise AppError(ErrorCode.INVALID_STATE, "不能禁用最后一个 owner", status_code=400)

    if role == "member" and user.role == "owner":
        if await _count_other_active_owners(session, user_id) == 0:
            raise AppError(
                ErrorCode.INVALID_STATE,
                "不能把最后一个 owner 改成 member",
                status_code=400,
            )

    if display_name is not None:
        user.display_name = display_name
    if role is not None:
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    if password is not None:
        user.password_hash = hash_password(password)
        user.failed_attempts = 0
        user.locked_until = None

    session.add(user)
    await session.commit()
    return user
