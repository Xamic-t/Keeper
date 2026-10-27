# 05 · 确认板块 Spec

> 对应文件：`services/confirm.py`、`api/confirmations.py`
> 计划：**W6**
> 依赖：`facts`、`tags`、`relationships`、`persons`

---

## 1. 这个板块解决什么问题

**一句话**：AI 抽出来的东西，让人点一遍 ✓ / ✗，才真正进档案。

这是**产品质量的闸门**。三个约束：

**① AI 不许直接写档案。** 生日、金额、禁忌抽错，比没有系统更糟。
**② 每张卡片必须显示出处。** 用户要看原图才能判断对错。
**③ 确认后要级联。** 一条事实确认 → 派生标签 → 重算价值 → 标记综述过期。

---

## 2. 流程

```
GET /api/confirmations
  → 按人分组返回 pending 条目（含 evidence + 缩略图 URL）

用户点 ✓
  → POST /api/confirmations/{id}/confirm
  → 事务：
      ① facts.status = 'confirmed'
      ② 派生 tags（按 category 映射）
      ③ 若涉及金额 → 重算 relationships.total_*
      ④ 标记 person.summary_stale = True（不在这里调模型）
  → 返回 {fact_id, status, cascaded:{...}}

用户点 ✗
  → status = 'rejected'（保留数据，用于改进抽取，不展示）

用户改归属
  → PATCH /api/confirmations/{id} {person_id: 20}
  → 改 facts.person_id → 重新派生标签
```

---

## 3. 数据流

| 操作 | 读 | 写 |
|---|---|---|
| 列表 | `facts`、`ledger`、`persons`、`raw_inputs` | — |
| 确认 | `facts` | `facts`、`tags`、`relationships` |
| 驳回 | `facts` | `facts.status` |
| 改归属 | `facts` | `facts.person_id`、`tags` |

---

## 4. 函数清单

| 函数 | 一句话 | 被谁调用 |
|---|---|---|
| `list_confirmations` | 待确认条目按人分组 | `GET /api/confirmations` |
| `confirm_fact` | 确认一条事实并级联 | `POST .../confirm` |
| `reject_fact` | 驳回一条 | `POST .../reject` |
| `reassign_fact` | 改归属后确认 | `PATCH .../{id}` |
| `batch_confirm` | 批量确认 | `POST .../batch` |
| `derive_tags` | 从事实派生标签 | `confirm_fact`、`reassign_fact` |
| `recalc_relationship_totals` | 重算往来累计 | `confirm_fact`、ledger 变更 |

---

## 5. 逐函数六问

---

#### `confirm_fact(session, fact_id: int) -> ConfirmResult`

1) **WHAT** —— 把一条 pending 事确认，并触发所有级联。
2) **WHO** —— `api/confirmations.confirm`。
3) **INPUT** —— `fact_id`。
4) **OUTPUT** —— `{fact_id, status, cascaded: {tags_derived, totals_recalculated, summary_stale}}`。
5) **HAPPY** ——
   - 查 fact → 不存在 404
   - `status != 'pending'` → 抛 `AppError(INVALID_STATE, 400)`
   - `status='confirmed'`, `last_confirmed_at=now`
   - `derive_tags(fact)` → 插入/更新 tags
   - 若 category 涉及金额（不常见）→ 重算
   - `person.summary_stale = True`
   - commit
6) **EDGE** ——
   - 已确认过 → 400 `INVALID_STATE`（幂等但报错，防止误点）
   - 标签冲突 → `ON CONFLICT (person_id, key, value) DO NOTHING`
   - **不在这里调模型写综述**（几秒的耗时不能进请求）

---

#### `derive_tags(session, fact: Fact) -> int`

1) **WHAT** —— 按事实的 category 映射成标签，写进 `tags`。
2) **WHO** —— `confirm_fact`、`reassign_fact`。
3) **INPUT** —— 已确认的 fact。
4) **OUTPUT** —— 派生出的标签数量。
5) **HAPPY** ——
   - category → key 映射表：
     `structure→industry`、`preference→preference`、`situation→situation`、`taboo→taboo(不建标签)`
   - 从 `content` 里抽 `value`（用规则或再调小模型，V1 先用规则）
   - 插 `tags(person_id, key, value, source_fact_id, expires_at)`
6) **EDGE** ——
   - `category='permanent'`（如生日）→ 不建标签，进 `relationships`
   - `category='mood'` → 半衰期极短，expires_at = now + 14 天
   - 抽不出 value → 跳过，返回 0
   - 重复 → 忽略

---

#### `recalc_relationship_totals(session, person_id: int) -> None`

1) **WHAT** —— 从 `ledger` 重新累加 `total_in` / `total_out`。
2) **WHO** —— `confirm_fact`（涉及往来时）、ledger 增删改。
3) **INPUT** —— `person_id`。
4) **OUTPUT** —— 无。
5) **HAPPY** ——
   - `SELECT direction, SUM(amount) FROM ledger WHERE person_id=? GROUP BY direction`
   - 更新 `relationships.total_in/out`
6) **EDGE** ——
   - 无往来 → 都置 0（不是 NULL）
   - **用 SUM 重算而不是增量加减**（增量会漂）

---

#### `batch_confirm(session, fact_ids: list[int]) -> BatchResult`

1) **WHAT** —— 一次确认多条。
2) **WHO** —— `POST /api/confirmations/batch`。
3) **INPUT** —— id 列表（≤50）。
4) **OUTPUT** —— `{confirmed: n, skipped: m, skipped_ids: [...]}`。
5) **HAPPY** —— 逐条走 `confirm_fact` 的逻辑，**一个事务**
6) **EDGE** ——
   - 某条不是 pending → **跳过，不整体失败**
   - 超过 50 条 → Pydantic 拦
   - 部分失败 → 返回 skipped 列表

---

## 6. 验收标准

| # | 检查 | 怎么验 |
|---|---|---|
| 1 | 确认后档案变厚 | 确认一条，档案页 tags 多一条 |
| 2 | 重复确认报错 | 点两次 ✓，第二次 400 |
| 3 | 驳回不展示 | ✗ 掉的条目在档案页看不到 |
| 4 | 金额联动 | 确认一笔往来，`total_in` 变化 |
| 5 | 卡片有出处 | 列表里每条都有 `evidence` 和缩略图 URL |

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-28 | v1.0 | 首版 |
