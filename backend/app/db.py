"""数据库连接。引擎只建一次，session 按请求注入（见 deps.py）。

注意：echo 会把 SQL 连同参数一起打进日志，可能带出隐私数据，默认关闭。
"""

import logging

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

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


@event.listens_for(Session, "after_begin")
def _inject_rls_user_id(
    session: Session,
    transaction: object,  # noqa: ARG001 — 事件签名固定
    connection: object,
) -> None:
    """每次事务开始时重放 RLS 变量。

    为什么需要：`set_config(..., is_local=true)` 与事务同生共死。service 里
    commit 之后事务结束，变量随之失效；同一 session 再查库会拿到空字符串，
    Postgres 会报 `invalid input syntax for type bigint: ""`。

    用 SQLAlchemy 官方事件（after_begin）在每次新事务开头补一次，
    调用方只需要在 `session.info["rls_user_id"]` 里放上当前用户。
    没放这个 key 的 session（系统级 session）不受影响。
    """
    user_id = session.info.get("rls_user_id")
    if user_id is None:
        return
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "SELECT set_config('app.user_id', %s, true)", (str(user_id),)
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
