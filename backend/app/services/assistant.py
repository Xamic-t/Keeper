"""助理侧车的账号解析。token 校验仍在 HTTP 依赖里，账号查询在这里。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.clock import local_today
from app.core.errors import AppError, ErrorCode
from app.models.event import Event
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


async def person_dates(
    session: AsyncSession, person_id: int, limit: int = 3
) -> list[dict[str, object]]:
    """这个人接下来的日子：生日 / 结款 / 自定义，按日期升序。

    为什么单独查 events 而不放进档案：档案是"这个人是谁"，日子是"什么时候要动"。
    模型回答"她生日是哪天"时必须能拿到日期，否则只能含糊说"没记着"——
    而用户明明在系统里记过。
    """
    today = local_today()
    rows = (
        (
            await session.execute(
                select(Event)
                .where(Event.person_id == person_id, Event.occurs_on >= today)
                .order_by(Event.occurs_on)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "type": event.type,
            "title": event.title,
            "occurs_on": event.occurs_on.isoformat(),
            "in_days": (event.occurs_on - today).days,
        }
        for event in rows
    ]
