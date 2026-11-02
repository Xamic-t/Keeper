"""助理侧车的账号解析。token 校验仍在 HTTP 依赖里，账号查询在这里。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import AppError, ErrorCode
from app.models.user import User


async def resolve_user(session: AsyncSession) -> User:
    """把 ASSISTANT_USERNAME 映射到活跃账号。"""
    stmt = select(User).where(
        User.username == settings.assistant_username, User.is_active.is_(True)
    )
    user = (await session.execute(stmt)).scalars().first()
    if user is None:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            f"助理账号 {settings.assistant_username} 不存在或已禁用",
            status_code=503,
        )
    return user
