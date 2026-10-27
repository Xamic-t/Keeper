# 11 · 今日聚合板块 Spec

> 对应文件：`services/today.py`、`api/today.py`
> 计划：**W10**
> 依赖：`reminders`、`todos`、`facts`、`ledger`、`events`

---

## 1. 这个板块解决什么问题

**一句话**：打开系统第一屏，一次拿全"今天该做什么"。

这是一个**纯聚合**板块——它不产生数据，只把别处算好的东西拼起来。

三个约束：

**① 一次请求拿全。** 不能前端发 5 个请求拼页面，手机上太慢。
**② 最多 3 条。** 20 人规模下，一屏塞 10 条就是制造焦虑。超出的折叠。
**③ 排序有讲究。** 提醒优先于待办，金额大的优先，快到期优先。

---

## 2. 拼装顺序

```
GET /api/today
  ↓
① 查未来 3 天内命中的提醒（status=sent）
  ↓
② 查今天到期 + 无截止日的未完成待办（最多 5 条）
  ↓
③ 补齐今日固定模板任务（ensure_daily_template）
  ↓
④ 合并成 tasks，排序，**只留前 3 条**
  ↓
⑤ 查待确认数量 + 前 1 条预览
  ↓
⑥ 查最近 3 条动态（facts 按 created_at 倒序）
  ↓
返回
```

---

## 3. 数据流

所有操作都是读，唯一例外是 `ensure_daily_template` 会写 `todos`。

| 来源 | 表 |
|---|---|
| 提醒 | `reminders` |
| 待办 | `todos` |
| 待确认 | `facts` |
| 动态 | `facts`、`ledger` |

---

## 4. 函数清单

| 函数 | 一句话 | 被谁调用 |
|---|---|---|
| `get_today` | 组装今日页全部数据 | `GET /api/today` |
| `collect_tasks` | 提醒 + 待办合成任务列表 | `get_today` |
| `rank_tasks` | 排序并截断到 3 条 | `get_today` |

---

## 5. 逐函数六问

---

#### `get_today(session, user_id, today: date) -> TodayView`

1) **WHAT** —— 一次查询组装今日页所需的全部数据。
2) **WHO** —— `GET /api/today`。
3) **INPUT** —— `user_id`、今天日期。
4) **OUTPUT** ——
   ```
   {
     date,
     tasks: [...],            // 最多 3 条
     tasks_more: int,         // 被折叠的条数
     pending_count: int,
     pending_preview: {...},  // 前 1 条
     recent: [...]            // 最近 3 条动态
   }
   ```
5) **HAPPY** ——
   - `ensure_daily_template` 补齐模板任务
   - `collect_tasks` 取提醒 + 待办
   - `rank_tasks` 排序并截断
   - 查待确认数量 + 预览
   - 查最近动态
6) **EDGE** ——
   - **全空时也要返回完整结构**（空数组，不是 404）
   - 单条查询失败 → **不要让整页崩**，该块返回空并在响应里标 `degraded`
   - 总耗时控制在 300ms 内（5 个查询并行）

---

#### `collect_tasks(session, user_id, today) -> list[TaskItem]`

1) **WHAT** —— 把提醒和待办合成统一的任务结构。
2) **WHO** —— `get_today`。
3) **INPUT** —— `user_id`、今天。
4) **OUTPUT** —— `[{type: 'reminder'|'todo', title, subtitle, meta, person_id, source_id}]`。
5) **HAPPY** ——
   - 查 `reminders WHERE status='sent' AND remind_on BETWEEN today AND today+3`
   - 查 `todos WHERE status='open' AND (due_date <= today OR due_date IS NULL)`
   - 转成统一结构，带上 `type` 和来源标记
6) **EDGE** ——
   - 提醒的 `payload` 可能为空（降级模式）→ `subtitle` 用事件标题
   - 待办无截止日 → 排在最后
   - 已完成待办不返回

---

#### `rank_tasks(tasks: list[TaskItem], limit: int = 3) -> tuple[list, int]`

1) **WHAT** —— 排序并截断。
2) **WHO** —— `get_today`。
3) **INPUT** —— 任务列表、上限（默认 3）。
4) **OUTPUT** —— `(前 N 条, 剩余条数)`。
5) **HAPPY** —— 按权重排序：
   - 提醒 > 待办
   - 同为提醒：`T-0` > `T-3` > `T-7`
   - 同档：有金额的优先
   - 截断到 `limit`
6) **EDGE** ——
   - 超过 3 条 → 返回剩余数，前端显示"还有 N 条"
   - 排序相同 → 保持原顺序（稳定排序）
   - 空列表 → `([], 0)`

---

## 6. 验收标准

| # | 检查 | 怎么验 |
|---|---|---|
| 1 | 一次请求拿全 | 网络面板只有 1 个 `/api/today` |
| 2 | 最多 3 条 | 造 8 条任务，只显示 3 条 + "还有 5 条" |
| 3 | 提醒排在待办前 | 造一条提醒一条待办，顺序固定 |
| 4 | 响应 < 300ms | curl 看 `time_total` |
| 5 | 全空不报错 | 新账号打开，返回空结构 |

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-28 | v1.0 | 首版 |
