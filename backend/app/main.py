"""应用入口。启动、中间件、异常处理、路由挂载都在这里。"""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api.router import api_router
from app.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import setup_logging
from app.db import dispose_engine, engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期。启动时初始化日志并做安全配置检查，退出时释放连接池。"""
    setup_logging()
    logger.info(
        "app starting name=%s env=%s version=%s",
        settings.app_name,
        settings.app_env,
        settings.app_version,
    )
    _assert_secret_key_is_safe()
    await _assert_database_role_respects_rls()
    yield
    await dispose_engine()
    logger.info("app stopped")


def _assert_secret_key_is_safe() -> None:
    """SECRET_KEY 是伪造会话的钥匙：生产环境太短/占位必须拒启动。"""
    value = settings.secret_key.get_secret_value()
    placeholder_markers = ("change-me", "dev-only", "example")
    if settings.is_dev:
        if len(value) < 32 or any(marker in value.lower() for marker in placeholder_markers):
            logger.warning("SECRET_KEY 太弱或是占位值；开发环境放行，生产必须更换")
        return

    if len(value) < 32 or any(marker in value.lower() for marker in placeholder_markers):
        raise RuntimeError(
            "SECRET_KEY 未配置为强随机值。生成：\n"
            'python -c "import secrets;print(secrets.token_urlsafe(32))"'
        )


async def _assert_database_role_respects_rls() -> None:
    """确认应用连接角色不能绕过 RLS。

    keeper（owner）在 Postgres 里是 BYPASSRLS，用它跑应用会让多用户隔离静默失效。
    开发环境只告警（本地可能还没建 keeper_app），生产环境直接拒启动。
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).first()

    if row is None:
        return
    if not (bool(row.rolsuper) or bool(row.rolbypassrls)):
        return

    message = (
        "应用正在用可绕过 RLS 的数据库角色运行（current_user 是 superuser/owner）。"
        "请把 DATABASE_URL 改为 keeper_app，迁移用 DATABASE_URL_MIGRATE。"
    )
    if settings.is_dev:
        logger.warning(message)
        return
    raise RuntimeError(message)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="私域关系维护 Agent —— 投喂、记住、判断、提醒",
    lifespan=lifespan,
    # 生产环境关掉交互文档，减少暴露面
    docs_url="/docs" if settings.is_dev else None,
    redoc_url=None,
)

# 开发时允许 Vite 开发服务器跨域；生产同域部署，不需要 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"] if settings.is_dev else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def access_log(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """每个请求记一行日志，并下发 trace_id。

    这里不捕获异常：未捕获异常由 core/errors.py 的全局 handler 统一记一次。
    中间件再 try/except 一次会导致同一异常记两遍（W1 遗留 G7）。
    """
    trace_id = uuid.uuid4().hex[:12]
    request.state.trace_id = trace_id
    started = time.perf_counter()

    response = await call_next(request)

    duration = int((time.perf_counter() - started) * 1000)
    logger.info(
        "request",
        extra={
            "trace_id": trace_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": duration,
        },
    )
    response.headers["X-Trace-Id"] = trace_id
    return response


# 六个全局异常处理器：AppError / 参数校验 / HTTPException / 唯一约束 / 兜底
register_exception_handlers(app)

# 所有业务路由挂在 /api 下
app.include_router(api_router)
