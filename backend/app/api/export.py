"""全量导出：一个 ZIP，里面是每个人的 JSON + 一份 CSV 清单。

对应文档：docs/07-后端设计.md 5.1（导出）

**这个接口会带走全部隐私数据**，所以：
  · 只给本人导出（RLS 保证只能导自己的）
  · 响应里带一句提醒，前端要显著提示用户
"""

import csv
import io
import json
import re
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

# ZIP 条目名里只允许：文字、数字、下划线、连字符、汉字。其它一律换成下划线。
# 不这么干，昵称里写 `../../etc/x` 就能在解压时跳出导出目录（Zip Slip，评审 P2-12）。
_UNSAFE_NAME = re.compile(r"[^\w\u4e00-\u9fff-]")


def safe_name(text: str, fallback: str = "person") -> str:
    """把人名清洗成可以放进 ZIP 路径的片段。纯符号的名字退化成 fallback。"""
    cleaned = _UNSAFE_NAME.sub("_", text or "").strip("_")
    return cleaned[:80] or fallback


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
                f"persons/{person.id}-{safe_name(person.nickname)}.json",
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
