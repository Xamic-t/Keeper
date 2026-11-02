"""全量导出：把每个人的 JSON 和清单打成 ZIP。"""

import csv
import io
import json
import re
import tempfile
import zipfile
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.person import Person
from app.services import person as person_service

# ZIP 条目名里只允许：文字、数字、下划线、连字符、汉字。其它一律换成下划线。
# 不这么干，昵称里写 `../../etc/x` 就能在解压时跳出导出目录（Zip Slip，评审 P2-12）。
_UNSAFE_NAME = re.compile(r"[^\w\u4e00-\u9fff-]")


def safe_name(text: str, fallback: str = "person") -> str:
    """把人名清洗成可以放进 ZIP 路径的片段。纯符号的名字退化成 fallback。"""
    cleaned = _UNSAFE_NAME.sub("_", text or "").strip("_")
    return cleaned[:80] or fallback


async def build_archive(session: AsyncSession) -> tempfile.SpooledTemporaryFile[bytes]:
    """打包全部人的档案。超过 1MB 自动落临时磁盘，不把整个 ZIP 堆在内存里。"""
    persons = list((await session.execute(select(Person).order_by(Person.id))).scalars().all())
    buffer: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(
        max_size=1024 * 1024, mode="w+b"
    )
    try:
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
        return buffer
    except Exception:
        buffer.close()
        raise
