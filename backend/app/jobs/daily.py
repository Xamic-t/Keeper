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

from app.db import SessionLocal
from app.services import profile, reminder

logger = logging.getLogger(__name__)


async def run_once(today: date | None = None) -> reminder.ScanResult:
    """跑一轮：刷新权重 → 扫事件 → 写提醒 → 发送。"""
    reference = today or datetime.now(UTC).date()
    async with SessionLocal() as session:
        updated = await profile.refresh_weights(session)
        result = await reminder.run_daily(session, reference)
    logger.info(
        "daily job done date=%s weights_updated=%s created=%s sent=%s failed=%s",
        reference,
        updated,
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
