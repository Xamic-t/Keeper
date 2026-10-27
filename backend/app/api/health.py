"""健康检查。唯一不需要登录的接口，供 Docker healthcheck 和人工排查使用。"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.response import ok
from app.db import check_database

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


@router.get("/health", summary="健康检查")
async def health() -> JSONResponse:
    """探测应用与数据库状态。

    返回 200 表示全部正常；503 表示有依赖不可用（Docker 会据此重启）。
    """
    database = await check_database()
    healthy = database["status"] == "ok"

    payload: dict[str, Any] = {
        "status": "ok" if healthy else "degraded",
        "app": settings.app_name,
        "version": settings.app_version,
        "env": settings.app_env,
        "time": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "checks": {"database": database},
    }
    return JSONResponse(status_code=200 if healthy else 503, content=ok(payload))
