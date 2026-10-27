# 06 · 画像板块 Spec

> 对应文件：`services/profile.py`、`services/fact.py`
> 计划：**W7**
> 依赖：`facts`、`tags`、`relationships`、`persons`

---

## 1. 这个板块解决什么问题

**一句话**：把一堆零散事实，变成"打开档案 10 秒就知道这人是谁"。

三个约束：

**① 综述是给人读的，不是标签堆砌。** 150 字以内，像在描述一个人，不是罗列字段。
**② 事实会过期。** "她孩子在高考"一年后就是错的。每条事实带半衰期。
**③ 综述不能每次读都算。** 生成要调模型（几秒），所以用 `summary_stale` 标记，懒生成。

---

## 2. 流程

```
事实被确认
  ↓
person.summary_stale = True       （不立即生成）
  ↓
下次有人打开档案
  ↓
读 person.summary
  ├─ stale=False → 直接返回
  └─ stale=True  → 后台生成新综述 → 写回 → 返回旧的那份（不阻塞）
  ↓
生成综述时：
  取 weight > 0.5 的事实（过滤掉快过期的）
  → 按类别分组 → 调模型 → 150 字
```

---

## 3. 数据流

| 操作 | 读 | 写 |
|---|---|---|
| 读档案 | `persons`、`facts`、`tags` | — |
| 生成综述 | `facts`、`relationships` | `persons.summary` |
| 重算权重 | `facts` | `facts.weight` |

---

## 4. 函数清单

| 函数 | 一句话 | 被谁调用 |
|---|---|---|
| `compute_weight` | 按半衰期算当前权重 | 定时任务、读档案时 |
| `select_facts_for_summary` | 挑出该进综述的事实 | `rewrite_summary` |
| `rewrite_summary` | 调模型重写 150 字综述 | 懒生成、手动重写 |
| `refresh_weights` | 批量更新所有事实的 weight | 每日定时任务 |
| `rebuild_tags` | 从 facts 全量重建该人的 tags | 数据修复 |

---

## 5. 逐函数六问

---

#### `compute_weight(half_life_days: int | None, last_confirmed_at: datetime) -> float`

1) **WHAT** —— 按半衰期算一条事实现在的权重（0-1）。
2) **WHO** —— `refresh_weights`、`select_facts_for_summary`。
3) **INPUT** —— 半衰期天数（None = 永久）、最后确认时间。
4) **OUTPUT** —— `0.5 ** (天数差 / half_life)`，永久事实返回 1.0。
5) **HAPPY** ——
   - `half_life_days is None` → 返回 1.0
   - `days = (now - last_confirmed_at).days`
   - 返回 `0.5 ** (days / half_life_days)`
6) **EDGE** ——
   - `days < 0`（时钟漂移）→ 按 0 算
   - `last_confirmed_at` 为空 → 用 `created_at`
   - 结果保留 3 位小数

**半衰期映射**（由 category 决定，写入时赋值）：

| category | 天数 |
|---|---|
| permanent | None |
| taboo | 540 |
| structure | 365 |
| preference | 180 |
| situation | 60 |
| mood | 14 |

---

#### `select_facts_for_summary(session, person_id: int) -> list[Fact]`

1) **WHAT** —— 挑出能进综述的事实。
2) **WHO** —— `rewrite_summary`。
3) **INPUT** —— `person_id`。
4) **OUTPUT** —— 按权重降序的事实列表，**最多 15 条**。
5) **HAPPY** ——
   - `WHERE status='confirmed' AND weight > 0.5`
   - 按 `weight DESC, last_confirmed_at DESC`
   - 限 15 条
6) **EDGE** ——
   - 一条都没有 → 返回空列表（综述降级为"还没有足够信息"）
   - 全是 situation（快过期）→ 仍返回，但综述里标注"近期"

---

#### `rewrite_summary(session, person_id: int) -> str`

1) **WHAT** —— 调模型把事实写成一段人物综述。
2) **WHO** —— 读档案时懒触发、`POST /api/persons/{id}/summary/rewrite`。
3) **INPUT** —— `person_id`。
4) **OUTPUT** —— 综述文本（≤150 字），同时写回 `persons.summary`。
5) **HAPPY** ——
   - 取 person + relationship（生日、行业、阶段）
   - `select_facts_for_summary`
   - 组装 prompt（`prompts/v1_summary.md`）
   - 调模型 → 截断到 150 字
   - 写回 `summary`，`summary_stale=False`
6) **EDGE** ——
   - 模型失败 → **保留旧综述**，`summary_stale` 保持 True，下次再试
   - 输出超 150 字 → 截断（不重新调）
   - 输出含 markdown → 剥掉
   - 事实为 0 → 直接写"还没有足够信息，先投喂几张聊天截图"

---

#### `refresh_weights(session) -> int`

1) **WHAT** —— 批量更新所有事实的当前权重。
2) **WHO** —— 每日定时任务（`jobs/daily.py`）。
3) **INPUT** —— 无。
4) **OUTPUT** —— 更新条数。
5) **HAPPY** ——
   - 遍历所有 `status='confirmed'` 的事实
   - 重算 weight
   - 批量 update
6) **EDGE** ——
   - 数据量大时用批量 SQL 而非 ORM 循环（V1 数据少，循环也行）
   - **不改 `last_confirmed_at`**（那会重置衰减）

---

## 6. 验收标准

| # | 检查 | 怎么验 |
|---|---|---|
| 1 | 打开档案 10 秒知道这人是谁 | 主观 |
| 2 | 综述不超 150 字 | `len(summary)` |
| 3 | 过期事实不出现在综述 | 造一条 100 天前的 situation，应被过滤 |
| 4 | 综述懒生成不阻塞 | 首次读档案要快，综述稍后更新 |

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-28 | v1.0 | 首版 |
