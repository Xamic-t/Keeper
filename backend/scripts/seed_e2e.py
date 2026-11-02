"""给自动化验收建一个隔离账号和一份固定的种子数据。

为什么必须有这个东西：
  验收脚本要"改日期、改状态、删条目"，如果跑在真实账号上，明早打开手册
  会看到被测试动过的待办。所以 e2e 只认这个脚本建的账号，其它账号一概不碰。

幂等：同一个账号重复跑，结果是同一份数据（先清该账号的业务数据再重建）。
  只清这个账号自己的行 —— 靠 RLS，session 里设了 app.user_id，
  别人的数据一行都看不见，也就删不到。

用法：
  cd backend && uv run python scripts/seed_e2e.py            # 建/重置
  cd backend && uv run python scripts/seed_e2e.py --purge    # 只删不建

账号默认 keeper-e2e / keeper-e2e-pass，可用 --username/--password 改。
"""

import argparse
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from app.core.clock import local_today  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models.event import Event, Reminder  # noqa: E402
from app.models.fact import Fact, Tag  # noqa: E402
from app.models.input import ChatSegment, RawInput  # noqa: E402
from app.models.ledger import Ledger  # noqa: E402
from app.models.person import Person  # noqa: E402
from app.models.relationship import Relationship  # noqa: E402
from app.models.todo import Todo  # noqa: E402
from app.models.user import User  # noqa: E402

DEFAULT_USERNAME = "keeper-e2e"
DEFAULT_PASSWORD = "keeper-e2e-pass"

# 种子人名。别用中文之外的花样：e2e 要按名字定位元素，名字得稳定。
PERSONS = [
    ("测试甲", "core", ["客户"]),
    ("测试乙", "familiar", ["朋友"]),
    ("测试丙", "new", ["同事"]),
]

# 四条待办覆盖三种日期状态 + 一条没日期，正好是日历要区分的三档颜色
TODOS = [
    ("回访测试甲", -2, "逾期：应该显示成红色"),
    ("给测试乙寄样品", 0, "今天：应该显示成琥珀色"),
    ("约测试丙吃饭", 3, "以后：应该显示成陶土色"),
    ("整理测试资料", None, "没日期：不该出现在日历上"),
]

EVENTS = [
    ("测试甲生日", 5, "birthday", "FREQ=YEARLY"),
    ("测试结款日", 12, "payment_due", None),
]


async def get_or_create_user(username: str, password: str) -> User:
    """建账号或改用已有账号的密码，保证脚本可反复跑。"""
    async with SessionLocal() as session:
        existing = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if existing is not None:
            existing.password_hash = hash_password(password)
            existing.is_active = True
            existing.failed_attempts = 0
            existing.locked_until = None
            session.add(existing)
            await session.commit()
            return existing

        user = User(
            username=username,
            display_name="验收账号",
            password_hash=hash_password(password),
            role="member",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def purge_business_data(user_id: int) -> None:
    """删掉该账号的全部业务数据。顺序按外键依赖来，先子后父。

    不只清这四张：用例中断会留下事实、往来、材料等垃圾，
    "幂等"要覆盖到它们，否则脏数据会慢慢堆。
    """
    async with SessionLocal() as session:
        session.info["rls_user_id"] = user_id
        for model in (
            Todo,
            Reminder,
            Fact,
            Tag,
            Ledger,
            ChatSegment,
            RawInput,
            Event,
            Relationship,
            Person,
        ):
            await session.execute(delete(model).where(model.user_id == user_id))  # type: ignore[attr-defined]
        await session.commit()


async def seed(user_id: int) -> dict[str, int]:
    """建人 + 关系 + 待办 + 事件 + 标签。日期全部相对"今天"算，跨天跑不会失效。"""
    today = local_today()
    counts = {"persons": 0, "todos": 0, "events": 0, "tags": 0}

    async with SessionLocal() as session:
        session.info["rls_user_id"] = user_id

        person_ids: list[int] = []
        for nickname, stage, roles in PERSONS:
            person = Person(user_id=user_id, nickname=nickname)
            session.add(person)
            await session.flush()  # 拿到 id 才能建关系
            session.add(
                Relationship(
                    user_id=user_id,
                    person_id=person.id,
                    stage=stage,
                    stage_source="manual",
                    roles=roles,
                )
            )
            person_ids.append(person.id)  # type: ignore[arg-type]
            counts["persons"] += 1

        # 标签筛选用例靠它：只有「测试甲」带这个标签
        session.add(Tag(user_id=user_id, person_id=person_ids[0], key="preference", value="白茶"))
        counts["tags"] += 1

        for title, offset, _hint in TODOS:
            session.add(
                Todo(
                    user_id=user_id,
                    person_id=person_ids[0] if offset is not None else None,
                    title=title,
                    source="manual",
                    due_date=None if offset is None else today + timedelta(days=offset),
                )
            )
            counts["todos"] += 1

        for title, offset, event_type, rrule in EVENTS:
            session.add(
                Event(
                    user_id=user_id,
                    person_id=person_ids[0],
                    type=event_type,
                    title=title,
                    occurs_on=today + timedelta(days=offset),
                    rrule=rrule,
                    source_type="manual",
                )
            )
            counts["events"] += 1

        await session.commit()

    return counts


async def main() -> int:
    parser = argparse.ArgumentParser(description="建/重置 e2e 隔离账号与种子数据")
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--purge", action="store_true", help="只删业务数据，不重建")
    args = parser.parse_args()

    user = await get_or_create_user(args.username, args.password)
    assert user.id is not None
    print(f"账号就绪：{args.username}（id={user.id}，role={user.role}）")

    await purge_business_data(user.id)
    print("已清空该账号的业务数据")

    if not args.purge:
        counts = await seed(user.id)
        print(
            f"已写入：{counts['persons']} 人 / "
            f"{counts['todos']} 条待办 / {counts['events']} 个事件 / "
            f"{counts['tags']} 个标签"
        )
        print(f"今天（业务时区）：{local_today()}")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
