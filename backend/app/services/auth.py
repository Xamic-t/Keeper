"""认证业务逻辑。不 import fastapi，能脱离 HTTP 单测。

对应文档：specs/01-auth.md 5.2

三条不能动的规则：
  ① 账号不存在和密码错返回同一个错误码（防账号枚举）
  ② 锁定判断在密码校验之前（锁定期内不许再试密码）
  ③ users 表没开 RLS，登录查询必须手动按 username 过滤（ARCHITECTURE 15.5 唯一例外）
"""

import logging
import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.security import hash_password, verify_password
from app.models.user import User

logger = logging.getLogger(__name__)

# 连续失败几次锁、锁多久（specs/01-auth.md 第 1 节）
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15


async def authenticate(session: AsyncSession, username: str, password: str) -> User:
    """完整登录校验。通过则返回用户，任何一步不过都抛 AppError。"""
    result = await session.execute(select(User).where(User.username == username.strip()))
    user = result.scalar_one_or_none()

    if user is None:
        raise AppError(
            ErrorCode.BAD_CREDENTIALS,
            "账号或密码不正确",
            status_code=401,
        )

    # 锁定判断必须在密码校验之前
    if user.locked_until is not None and user.locked_until > datetime.now(UTC):
        remaining = math.ceil((user.locked_until - datetime.now(UTC)).total_seconds() / 60)
        raise AppError(
            ErrorCode.ACCOUNT_LOCKED,
            f"账号已被锁定，请 {remaining} 分钟后再试",
            status_code=401,
            detail={"retry_after_minutes": remaining},
        )

    # 停用的账号和密码错用同一个码——不告诉对方"这个号存在但被停用了"
    if not user.is_active:
        raise AppError(ErrorCode.BAD_CREDENTIALS, "账号或密码不正确", status_code=401)

    if not verify_password(password, user.password_hash):
        await record_login_failure(session, user)
        raise AppError(ErrorCode.BAD_CREDENTIALS, "账号或密码不正确", status_code=401)

    await record_login_success(session, user)
    return user


async def record_login_failure(session: AsyncSession, user: User) -> None:
    """失败计数 +1；累计到 5 次就锁 15 分钟，并把计数清零重新算。"""
    user.failed_attempts += 1
    if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
        user.locked_until = datetime.now(UTC) + timedelta(minutes=LOCK_MINUTES)
        user.failed_attempts = 0
        logger.warning("account locked user_id=%s", user.id)

    session.add(user)
    await session.commit()


async def record_login_success(session: AsyncSession, user: User) -> None:
    """清零失败计数、解锁、写最后登录时间。"""
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = datetime.now(UTC)
    session.add(user)
    await session.commit()


async def change_password(
    session: AsyncSession,
    user_id: int,
    old_password: str,
    new_password: str,
) -> None:
    """验旧密码后改成新密码。

    收 user_id 而不是 User/CurrentUser：CurrentUser 定义在 deps.py（import 了 fastapi），
    service 不能反向依赖它，保持"能脱离 HTTP 单测"。
    """
    user = await session.get(User, user_id)
    if user is None:
        # 登录态有效但账号没了（并发删除），当作未登录
        raise AppError(ErrorCode.UNAUTHENTICATED, "请先登录", status_code=401)

    if not verify_password(old_password, user.password_hash):
        raise AppError(ErrorCode.BAD_CREDENTIALS, "原密码不正确", status_code=400)

    user.password_hash = hash_password(new_password)
    session.add(user)
    await session.commit()
