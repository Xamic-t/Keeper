"""每日任务：扫事件 → 生成提醒 → 推送；顺便刷新事实权重。

两种跑法：
  1. 常驻：`python -m app.jobs.daily --serve`（APScheduler，每天 08:00 跑）
  2. 手动：`python -m app.jobs.daily`（跑一次就退出，方便验证）

幂等靠数据库唯一约束 (event_id, remind_on, level)，重跑不会重复发。
"""

import argparse
import asyncio
import logging
from datetime import UTC, date, datetime

from sqlalchemy import select

from app.db import SessionLocal
from app.models.user import User
from app.services import profile, reminder

logger = logging.getLogger(__name__)


async def run_once(today: date | None = None) -> reminder.ScanResult:
    """跑一轮：按用户逐个刷新权重 → 扫事件 → 写提醒 → 发送。

    必须按用户逐个开 session：每张业务表都开了 RLS，
    一个不带 rls_user_id 的 session 查 persons 会直接抛 UndefinedObject。
    users 表没开 RLS，所以先用系统 session 取活跃用户列表。
    """
    reference = today or datetime.now(UTC).date()
    result = reminder.ScanResult()

    # ① users 表没开 RLS，用无变量的系统 session 取活跃用户
    async with SessionLocal() as probe:
        user_ids = list(
            (await probe.execute(select(User.id).where(User.is_active.is_(True)))).scalars().all()
        )

    # ② 每个用户单独一个 session，各自带自己的 RLS 变量
    for user_id in user_ids:
        async with SessionLocal() as session:
            session.info["rls_user_id"] = user_id
            updated = await profile.refresh_weights(session)
            partial = await reminder.run_daily(session, reference)
        result.created += partial.created
        result.sent += partial.sent
        result.failed += partial.failed
        logger.info(
            "daily for user=%s weights_updated=%s created=%s sent=%s failed=%s",
            user_id,
            updated,
            partial.created,
            partial.sent,
            partial.failed,
        )

    logger.info(
        "daily job done date=%s users=%s created=%s sent=%s failed=%s",
        reference,
        len(user_ids),
        result.created,
        result.sent,
        result.failed,
    )
    return result


def _serve() -> None:
    """常驻模式：每天 08:00 跑一次。"""
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        lambda: asyncio.run(run_once()),
        "cron",
        hour=8,
        minute=0,
        id="keeper-daily",
    )
    logger.info("daily scheduler started (08:00 Asia/Shanghai)")
    scheduler.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Keeper 每日任务")
    parser.add_argument("--serve", action="store_true", help="常驻，按 cron 跑")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    if args.serve:
        _serve()
        return

    result = asyncio.run(run_once())
    print(f"created={result.created} sent={result.sent} failed={result.failed}")


if __name__ == "__main__":
    main()
