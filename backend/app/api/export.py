"""全量导出：一个 ZIP，里面是每个人的 JSON + 一份 CSV 清单。

对应文档：docs/07-后端设计.md 5.1（导出）

**这个接口会带走全部隐私数据**，所以：
  · 只给本人导出（RLS 保证只能导自己的）
  · 响应里带一句提醒，前端要显著提示用户
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.deps import CurrentUser, get_current_user, get_session
from app.services import export as export_service

router = APIRouter(prefix="/export", tags=["export"])


@router.get("", summary="全量导出 ZIP")
async def export_all(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """把所有数据打包成 ZIP 下载。"""
    buffer = await export_service.build_archive(session)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="keeper-export.zip"'},
        background=BackgroundTask(buffer.close),
    )
