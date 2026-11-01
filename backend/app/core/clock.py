"""业务日期：判断"今天是哪天"。

对应文档：`ARCHITECTURE` 8.6

**为什么不能直接用 `date.today()`**：容器默认时区是 UTC。北京时间 0 点到 8 点之间，
`date.today()` 返回的还是昨天——今日页会显示昨天的任务，生日当天的 T-0 提醒会提前一天发。

时间戳（`sent_at`、`created_at`、日志）照旧用 UTC，那是另一件事。这里只解决"日期"。
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

# 产品是单区域：用户都在国内，所以只有一个业务时区。
# 将来真要做多时区，把它挪进用户设置表，不改调用方。
BUSINESS_TZ = ZoneInfo("Asia/Shanghai")


def local_today() -> date:
    """今天（北京时间）。"""
    return datetime.now(BUSINESS_TZ).date()


def local_now() -> datetime:
    """现在（带北京时区）。只在需要判断"日期"边界时用，落库仍用 UTC。"""
    return datetime.now(BUSINESS_TZ)
