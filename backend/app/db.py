"""数据库连接。引擎只建一次，session 按请求注入（见 deps.py）。

注意：echo 会把 SQL 连同参数一起打进日志，可能带出隐私数据，默认关闭。
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

logger = logging.getLogger(__name__)

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    echo=settings.db_echo,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def check_database() -> dict[str, str]:
    """探测数据库连通性，供健康检查使用。不抛异常，只返回状态。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 — 健康检查不该因为探测失败而崩
        # 只暴露异常类型，绝不返回连接串或堆栈
        logger.warning("database check failed: %s", type(exc).__name__)
        return {"status": "down", "reason": type(exc).__name__}


async def dispose_engine() -> None:
    """关闭连接池，在应用退出时调用。"""
    await engine.dispose()
