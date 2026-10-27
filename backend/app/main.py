"""应用入口。启动、中间件、异常处理、路由挂载都在这里。"""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api.router import api_router
from app.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import setup_logging
from app.db import dispose_engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期。启动时初始化日志，退出时释放连接池。"""
    setup_logging()
    logger.info(
        "app starting name=%s env=%s version=%s",
        settings.app_name,
        settings.app_env,
        settings.app_version,
    )
    yield
    await dispose_engine()
    logger.info("app stopped")


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
    """每个请求记一行日志，并下发 trace_id。"""
    trace_id = uuid.uuid4().hex[:12]
    request.state.trace_id = trace_id
    started = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        duration = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "request failed",
            extra={
                "trace_id": trace_id,
                "method": request.method,
                "path": request.url.path,
                "duration_ms": duration,
            },
        )
        raise

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


# 四个全局异常处理器：AppError / 参数校验 / HTTPException / 兜底
register_exception_handlers(app)

# 所有业务路由挂在 /api 下
app.include_router(api_router)
