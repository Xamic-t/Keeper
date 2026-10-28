"""农历与节日：把"日子"算准。

对应文档：specs/08-events.md 5.1 / 5.2

两个坑：
  ① 农历生日**不能用 FREQ=YEARLY** 表达——每年公历日期都不同，必须逐年换算
  ② `lunar-python` 的节气表按年硬编码，跨年可能缺；取不到就当没有，不能让日历整页挂掉
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime

from lunar_python import Lunar, LunarYear, Solar

logger = logging.getLogger(__name__)

# 农历固定日期的传统节日：(月, 日, 名称)
LUNAR_FESTIVALS: tuple[tuple[int, int, str], ...] = (
    (1, 1, "春节"),
    (1, 15, "元宵"),
    (5, 5, "端午"),
    (7, 7, "七夕"),
    (8, 15, "中秋"),
    (9, 9, "重阳"),
)

# 24 节气（只取中文名，表里还有 DA_XUE 这种内部键）
SOLAR_TERMS: tuple[str, ...] = (
    "小寒",
    "大寒",
    "立春",
    "雨水",
    "惊蛰",
    "春分",
    "清明",
    "谷雨",
    "立夏",
    "小满",
    "芒种",
    "夏至",
    "小暑",
    "大暑",
    "立秋",
    "处暑",
    "白露",
    "秋分",
    "寒露",
    "霜降",
    "立冬",
    "小雪",
    "大雪",
    "冬至",
)


@dataclass(frozen=True, slots=True)
class Festival:
    """一个节日或节气。节日是公共的，不挂人。"""

    name: str
    day: date
    kind: str  # festival | solar_term


def lunar_to_solar(lunar_month: int, lunar_day: int, is_leap: bool, year: int) -> date | None:
    """农历月日 → 指定公历年的公历日期。不存在（闰月缺失 / 小月无三十）返回 None。"""
    if not 1 <= lunar_month <= 12 or not 1 <= lunar_day <= 30:
        return None

    try:
        leap_month = LunarYear.fromYear(year).getLeapMonth()
        if is_leap and leap_month != lunar_month:
            return None

        month = -lunar_month if is_leap else lunar_month
        solar = Lunar.fromYmd(year, month, lunar_day).getSolar()
        result = date(solar.getYear(), solar.getMonth(), solar.getDay())
        if result.year != year:
            # 农历十二月落在次年 1 月：换成上一个农历年，才是"今年的这次生日"
            solar = Lunar.fromYmd(year - 1, month, lunar_day).getSolar()
            result = date(solar.getYear(), solar.getMonth(), solar.getDay())
        return result
    except Exception as exc:  # noqa: BLE001 — 农历库对非法日期抛多种异常
        logger.warning(
            "lunar convert failed year=%s month=%s day=%s kind=%s",
            year,
            lunar_month,
            lunar_day,
            type(exc).__name__,
        )
        return None


def get_festivals(year: int) -> list[Festival]:
    """某年的传统节日 + 24 节气。取不到的跳过，绝不抛。"""
    items: list[Festival] = []

    for month, day, name in LUNAR_FESTIVALS:
        converted = lunar_to_solar(month, day, False, year)
        if converted is not None:
            items.append(Festival(name=name, day=converted, kind="festival"))

    try:
        table = Solar.fromYmd(year, 6, 1).getLunar().getJieQiTable()
    except Exception as exc:  # noqa: BLE001
        logger.warning("jieqi table failed year=%s kind=%s", year, type(exc).__name__)
        table = {}

    for term in SOLAR_TERMS:
        solar = table.get(term)
        if solar is None:
            continue
        items.append(
            Festival(
                name=term,
                day=date(solar.getYear(), solar.getMonth(), solar.getDay()),
                kind="solar_term",
            )
        )

    items.sort(key=lambda item: item.day)
    return items


def today() -> date:
    """今天。抽出来是为了测试能替换。"""
    return datetime.now().date()
