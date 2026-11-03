"""重建检索索引：把所有已确认事实 + 已投喂材料的原文切片、向量化。

什么时候要跑：
  · 刚配好 `DASHSCOPE_API_KEY`——之前的切片 `embedding` 是 NULL，跑一次就补齐
  · 换了切片规则 / 换了向量模型
  · 怀疑索引落后于数据

用法：
    cd backend && uv run python scripts/reindex.py            # 所有人
    cd backend && uv run python scripts/reindex.py --user 10805

为什么要按用户逐个开 session：RLS 是按 `app.user_id` 隔离的，
用超级视角遍历所有人再写切片，等于绕开隔离——那正是 §17 禁止的事。
这里以"每个用户自己的身份"写他自己的索引，和业务代码走同一条路。
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models.fact import Fact  # noqa: E402
from app.models.input import RawInput  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import embedding, rag  # noqa: E402


async def _reindex_user(user_id: int) -> tuple[int, int]:
    """重建一个用户的索引，返回（事实条数，材料份数）。"""
    async with SessionLocal() as session:
        session.info["rls_user_id"] = user_id

        facts = list(
            (await session.execute(select(Fact).where(Fact.status == "confirmed"))).scalars().all()
        )
        for fact in facts:
            await rag.index_fact(session, fact)

        inputs = list(
            (await session.execute(select(RawInput).where(RawInput.ocr_text.is_not(None))))
            .scalars()
            .all()
        )
        for item in inputs:
            if item.id is None:
                continue
            await rag.index_source(
                session,
                user_id=user_id,
                source_type="raw_input",
                source_id=item.id,
                content=item.ocr_text or "",
                person_id=item.person_id,
                scope="knowledge",
            )
    return len(facts), len(inputs)


async def main() -> None:
    parser = argparse.ArgumentParser(description="重建 RAG 索引")
    parser.add_argument("--user", type=int, default=None, help="只重建某个 user_id")
    args = parser.parse_args()

    async with SessionLocal() as probe:
        statement = select(User.id, User.username).order_by(User.id)
        if args.user is not None:
            statement = statement.where(User.id == args.user)
        users = (await probe.execute(statement)).all()

    if not users:
        print("没有匹配的账号")
        return

    state = "已配置" if embedding.available() else "未配置（切片会写入，但 embedding 为 NULL）"
    print(f"向量模型：{state}")
    for user_id, username in users:
        facts, inputs = await _reindex_user(user_id)
        print(f"  {username}(id={user_id})：{facts} 条事实、{inputs} 份材料")


if __name__ == "__main__":
    asyncio.run(main())
