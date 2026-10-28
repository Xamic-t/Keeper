"""农历换算边界：闰月、腊月跨年、非法月日。

覆盖 specs/08-events.md 5.1 / 5.2 的换算逻辑，也是 W8 最容易错的边界。
纯函数测试，不需要数据库。
"""

from datetime import date

from app.services.festival import lunar_to_solar


def test_lunar_to_solar_known_festival() -> None:
    # 中秋（八月十五）2025 年落在公历 10 月 6 日
    assert lunar_to_solar(8, 15, False, 2025) == date(2025, 10, 6)
    # 春节（正月初一）2026 年落在公历 2 月 17 日
    assert lunar_to_solar(1, 1, False, 2026) == date(2026, 2, 17)


def test_lunar_to_solar_invalid_month_or_day_returns_none() -> None:
    assert lunar_to_solar(13, 1, False, 2026) is None  # 月越界
    assert lunar_to_solar(0, 1, False, 2026) is None  # 月为 0
    assert lunar_to_solar(1, 0, False, 2026) is None  # 日为 0
    assert lunar_to_solar(1, 31, False, 2026) is None  # 小月无三十一


def test_lunar_to_solar_leap_month() -> None:
    # 2025 年闰六月，闰六月初一存在
    assert lunar_to_solar(6, 1, True, 2025) == date(2025, 7, 25)
    # 2026 年没有闰六月，闰六月生日这一年不存在 → None
    assert lunar_to_solar(6, 1, True, 2026) is None


def test_lunar_to_solar_leap_when_month_not_leap() -> None:
    # 请求闰月，但当年闰的不是这个月 → None
    assert lunar_to_solar(5, 1, True, 2025) is None


def test_lunar_to_solar_late_month_crosses_year() -> None:
    # 农历十二月落在次年 1 月：换算到"今年"的那一次（上一农历年）
    assert lunar_to_solar(12, 1, False, 2026) == date(2026, 1, 19)
