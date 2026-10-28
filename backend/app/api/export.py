"""全量导出：一个 ZIP，里面是每个人的 JSON + 一份 CSV 清单。

对应文档：docs/07-后端设计.md 5.1（导出）

**这个接口会带走全部隐私数据**，所以：
  · 只给本人导出（RLS 保证只能导自己的）
  · 响应里带一句提醒，前端要显著提示用户
"""

import csv
import io
import json
import zipfile
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import CurrentUser, get_current_user, get_session
from app.models.person import Person
from app.services import person as person_service

router = APIRouter(prefix="/export", tags=["export"])


@router.get("", summary="全量导出 ZIP")
async def export_all(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """把所有数据打包成 ZIP 下载。"""
    stmt = select(Person).order_by(Person.id)
    persons = list((await session.execute(stmt)).scalars().all())

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        index: list[dict[str, Any]] = []
        for person in persons:
            if person.id is None:
                continue
            data = await person_service.export_person(session, person.id)
            archive.writestr(
                f"persons/{person.id}-{person.nickname}.json",
                json.dumps(data, ensure_ascii=False, indent=2),
            )
            index.append(
                {
                    "id": person.id,
                    "nickname": person.nickname,
                    "facts": len(data["facts"]),
                    "ledger": len(data["ledger"]),
                    "events": len(data["events"]),
                    "tags": len(data["tags"]),
                }
            )

        csv_buffer = io.StringIO()
        writer = csv.DictWriter(
            csv_buffer, fieldnames=["id", "nickname", "facts", "ledger", "events", "tags"]
        )
        writer.writeheader()
        writer.writerows(index)
        archive.writestr("index.csv", csv_buffer.getvalue())
        archive.writestr(
            "README.txt",
            "Keeper 全量导出。index.csv 是名单，persons/ 下是每个人的完整档案 JSON。\n",
        )

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="keeper-export.zip"'},
    )
