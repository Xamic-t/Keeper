"""管理账号的业务规则，以及管理后台的汇总数据。API 层只取参数、调这里、返回。"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.clock import local_today
from app.core.errors import AppError, ErrorCode
from app.core.security import hash_password
from app.db import SessionLocal
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


# ── 后台概览：账号 + 每个账号的行为量 ─────────────────────


async def overview(session: AsyncSession) -> dict[str, Any]:
    """管理后台的汇总数据：账号状态 + 每个账号的业务量与最近活动。

    为什么按账号逐个开 session：
      RLS 是按 `app.user_id` 隔离的，一次查询只能看到一个人的数据。
      后台要看"所有人的量"，就只能一个人一个人地数——20 人规模，
      每人一条 SQL（子查询合在一次往返里），总共 20 次往返，够快。
      **不给 RLS 开后门**（不加 is_owner 之类的旁路）：那等于把隔离交给一个开关，
      以后谁都能拿它越权。这里用的是"以该用户身份去数他自己的数"，
      数出来的是**数量**，不是内容。
    """
    users = await list_users(session)

    rows: list[dict[str, Any]] = []
    totals = {
        "persons": 0,
        "facts": 0,
        "todos_open": 0,
        "ledger": 0,
        "reminders_sent": 0,
        "raw_inputs": 0,
        "chat_messages": 0,
        "pending_confirmations": 0,
    }

    async with SessionLocal() as raw:
        revision = (await raw.execute(text("SELECT version_num FROM alembic_version"))).scalar()

    for user in users:
        stats = await _user_stats(user.id or 0)
        for key in totals:
            totals[key] += int(stats[key])
        rows.append(
            {
                "id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "role": user.role,
                "is_active": user.is_active,
                "created_at": user.created_at,
                "last_login_at": user.last_login_at,
                "last_active_at": _latest(
                    user.last_login_at, stats["last_chat_at"], stats["last_input_at"]
                ),
                **{key: int(stats[key]) for key in totals},
            }
        )

    rows.sort(
        key=lambda row: (row["last_active_at"] is None, row["last_active_at"] or ""), reverse=False
    )

    return {
        "accounts": {
            "total": len(users),
            "owners": sum(1 for user in users if user.role == "owner"),
            "active": sum(1 for user in users if user.is_active),
            "disabled": sum(1 for user in users if not user.is_active),
        },
        "totals": totals,
        "users": rows,
        "system": {
            "app_env": settings.app_env,
            "app_version": settings.app_version,
            "migration": revision,
            "today": local_today().isoformat(),
        },
    }


async def _user_stats(user_id: int) -> dict[str, Any]:
    """以这个账号的身份数他自己的数（RLS 生效，只能看到自己的行）。"""
    stmt = text(
        """
        SELECT
            (SELECT count(*) FROM persons) AS persons,
            (SELECT count(*) FROM facts) AS facts,
            (SELECT count(*) FROM facts WHERE status = 'pending') AS pending_confirmations,
            (SELECT count(*) FROM todos WHERE status = 'open') AS todos_open,
            (SELECT count(*) FROM ledger) AS ledger,
            (SELECT count(*) FROM reminders WHERE status = 'sent') AS reminders_sent,
            (SELECT count(*) FROM raw_inputs) AS raw_inputs,
            (SELECT count(*) FROM chat_messages) AS chat_messages,
            (SELECT max(created_at) FROM chat_messages) AS last_chat_at,
            (SELECT max(created_at) FROM raw_inputs) AS last_input_at
        """
    )
    async with SessionLocal() as scoped:
        scoped.info["rls_user_id"] = user_id
        row = (await scoped.execute(stmt)).mappings().one()
    return dict(row)


def _latest(*values: datetime | None) -> datetime | None:
    """取最近的一个时间点。全部为空就返回 None（前端显示"还没用过"）。"""
    candidates = [value for value in values if value is not None]
    return max(candidates) if candidates else None
