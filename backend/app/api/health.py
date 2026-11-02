"""健康检查。唯一不需要登录的接口，供 Docker healthcheck 和人工排查使用。"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.config import settings
from app.core.response import DataResponse, ok
from app.db import check_database

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


class HealthOut(BaseModel):
    status: str
    app: str
    version: str
    env: str
    time: str
    checks: dict[str, Any]


@router.get("/health", response_model=DataResponse[HealthOut], summary="健康检查")
async def health(response: Response) -> dict[str, Any]:
    """探测应用与数据库状态。

    返回 200 表示全部正常；503 表示有依赖不可用（Docker 会据此重启）。
    """
    database = await check_database()
    healthy = database["status"] == "ok"
    if not healthy:
        response.status_code = 503

    return ok(
        HealthOut(
            status="ok" if healthy else "degraded",
            app=settings.app_name,
            version=settings.app_version,
            env=settings.app_env,
            time=datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
            checks={"database": database},
        )
    )
