# 08 · 日历板块 Spec

> 对应文件：`services/festival.py`、`services/event.py`、`api/events.py`
> 计划：**W8**
> 依赖：`events`、`relationships`

---

## 1. 这个板块解决什么问题

**一句话**：把"日子"算准——尤其是农历生日和传统节日。

三个约束：

**① 农历生日必须每年换算。** 不能用 `FREQ=YEARLY` 表达，因为每年的公历日期都不同。
**② 节日是公共的，不挂人。** `events.person_id` 可空。
**③ 节日库会跨年失效。** `lunar-python` 的节假日数据按年硬编码，要包一层兜底。

---

## 2. 流程

```
查日历（from/to 范围）
  ↓
① 查 events 里落在范围内的事件（含 rrule 展开）
  ↓
② 对每个 type=birthday 且 birthday_type=lunar 的：
     lunar_to_solar(月, 日, 目标年份) → 当年的公历日期
  ↓
③ 合并内置节日（春节、中秋、端午、清明 + 24 节气）
  ↓
返回按日期排序的事件列表
```

---

## 3. 数据流

| 操作 | 读 | 写 |
|---|---|---|
| 查日历 | `events`、`relationships` | — |
| 新增事件 | — | `events` |
| 修改/删除 | `events` | `events` |

---

## 4. 函数清单

| 函数 | 一句话 | 被谁调用 |
|---|---|---|
| `lunar_to_solar` | 农历转公历 | `expand_events` |
| `get_festivals` | 某年的节日与节气 | `expand_events` |
| `expand_events` | 把事件展开到日期范围 | `list_events` |
| `list_events` | 日历查询 | `GET /api/events` |
| `create_event` | 新增事件 | `POST /api/events` |

---

## 5. 逐函数六问

---

#### `lunar_to_solar(lunar_month: int, lunar_day: int, is_leap: bool, year: int) -> date | None`

1) **WHAT** —— 把农历月日转成指定年份的公历日期。
2) **WHO** —— `expand_events`。
3) **INPUT** —— 农历月（1-12）、日（1-30）、是否闰月、目标公历年。
4) **OUTPUT** —— 公历 `date`；无此日期（如闰月不存在）返回 `None`。
5) **HAPPY** ——
   - `LunarYear.from_year(year).to_solar(month, day, is_leap)`
   - 返回 `date`
6) **EDGE** ——
   - 闰月在某年不存在 → 返回 None，调用方跳过
   - 农历 30 日遇到小月（只有 29 天）→ 返回 None
   - **库抛异常要捕获**，返回 None，不让整个日历挂掉

---

#### `get_festivals(year: int) -> list[Festival]`

1) **WHAT** —— 取某年的传统节日 + 24 节气。
2) **WHO** —— `expand_events`。
3) **INPUT** —— 公历年。
4) **OUTPUT** —— `[{name, date, type}]`。
5) **HAPPY** ——
   - 从 `lunar-python` 取春节/元宵/端午/中秋/重阳/清明
   - 取 24 节气
   - 返回合并列表
6) **EDGE** ——
   - **库的数据按年硬编码，跨年可能缺失** → 封装 `FestivalService`，缺失时读本地 `festivals.json` 兜底
   - 都取不到 → 返回空列表，日历只显示用户事件

---

#### `expand_events(session, user_id, start: date, end: date) -> list[CalendarItem]`

1) **WHAT** —— 把事件（含重复规则、农历生日）展开成范围内的一条条日期。
2) **WHO** —— `list_events`。
3) **INPUT** —— 日期范围。
4) **OUTPUT** —— `[{date, title, type, person_id, color}]`。
5) **HAPPY** ——
   - 查 `events WHERE occurs_on BETWEEN start AND end OR rrule IS NOT NULL`
   - 有 `rrule` → 用 `dateutil.rrule` 展开到范围内
   - `type='birthday'` 且农历 → 逐年 `lunar_to_solar`
   - 合并 `get_festivals(start.year)` 和 `get_festivals(end.year)`
   - 按日期排序
6) **EDGE** ——
   - 范围跨年 → 两年节日都要取
   - 范围内没事件 → 返回空列表（200，不是 404）
   - rrule 非法字符串 → 跳过该事件，记 WARNING

---

#### `create_event(session, user_id, payload: EventCreate) -> Event`

1) **WHAT** —— 新增一个事件。
2) **WHO** —— `POST /api/events`。
3) **INPUT** —— `type, title, occurs_on, person_id?, rrule?, lunar?`。
4) **OUTPUT** —— 新建的 `Event`。
5) **HAPPY** —— 校验后 insert，commit
6) **EDGE** ——
   - `type='birthday'` 必须挂 `person_id`（Pydantic 校验）
   - `type='festival'` **不能**挂人
   - `type='birthday'` 且 `lunar` 非空 → 同时写 `relationships.birthday_lunar`
   - 重复建同一天同类型的生日 → 允许（用户自己删）

---

## 6. 验收标准

| # | 检查 | 怎么验 |
|---|---|---|
| 1 | 农历生日算对 | 建农历 3-12 生日，查当年公历日期（对照万年历） |
| 2 | 跨年不出错 | 查 12 月到次年 2 月的范围 |
| 3 | 节日显示 | 查中秋所在月份，能看到中秋 |
| 4 | 闰月不存在时跳过 | 建闰四月的生日，非闰年不显示 |
| 5 | 空范围返回 200 | 查一个没有事件的月份 |

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-28 | v1.0 | 首版 |
