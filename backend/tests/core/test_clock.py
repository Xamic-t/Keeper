"""业务日期：必须是北京时间，不能是 UTC。

钉住这个是因为容器默认 TZ 是 UTC：北京时间 0–8 点之间，
`date.today()` 返回昨天，今日页和 T-0 提醒都会差一天（ARCHITECTURE 8.6）。
"""

from datetime import UTC, datetime, timedelta

from app.core.clock import BUSINESS_TZ, local_now, local_today


def test_business_timezone_is_shanghai() -> None:
    assert str(BUSINESS_TZ) == "Asia/Shanghai"


def test_local_now_is_eight_hours_ahead_of_utc() -> None:
    offset = local_now().utcoffset()
    assert offset == timedelta(hours=8)


def test_local_today_matches_beijing_calendar_date() -> None:
    beijing = datetime.now(UTC).astimezone(BUSINESS_TZ).date()
    assert local_today() == beijing
