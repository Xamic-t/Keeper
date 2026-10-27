# 07 · 往来板块 Spec

> 对应文件：`services/ledger.py`、`api/ledger.py`
> 计划：**W8**
> 依赖：`ledger`、`relationships`、`events`

---

## 1. 这个板块解决什么问题

**一句话**：记清楚"我给他、他给我，多少钱，什么时候"。

三个约束：

**① 金额永远正数。** 方向由 `direction` 表达。负数会让求和和判断大小都变复杂。
**② 往来和关系绑在一起。** 打开一个人的档案，要能一眼看到他给过多少、你送过多少。
**③ 有结款日的往来要自动进日历。** 不然到期忘了催。

---

## 2. 流程

```
新增一笔往来
  ↓
校验：amount>0、due_date>=occurred_at、kind 在白名单
  ↓
事务：
  ① insert ledger
  ② 重算 relationships.total_in / total_out
  ③ 若 due_date 非空且未结清 → 生成 events(type=payment_due)
  ↓
201
```

---

## 3. 数据流

| 操作 | 读 | 写 |
|---|---|---|
| 列表 | `ledger` | — |
| 新增 | — | `ledger`、`relationships`、`events` |
| 修改 | `ledger` | `ledger`、`relationships`、`events` |
| 删除 | `ledger` | `ledger`、`relationships`、`events` |

---

## 4. 函数清单

| 函数 | 一句话 | 被谁调用 |
|---|---|---|
| `list_ledger` | 某人的往来明细 | `GET /api/persons/{id}/ledger` |
| `create_ledger` | 新增并触发级联 | `POST /api/ledger` |
| `update_ledger` | 修改并触发级联 | `PATCH /api/ledger/{id}` |
| `delete_ledger` | 删除并触发级联 | `DELETE /api/ledger/{id}` |
| `sync_due_event` | 同步结款事件 | 上面三个 |
| `recalc_totals` | 重算累计 | 上面三个 |

---

## 5. 逐函数六问

---

#### `create_ledger(session, user_id, payload: LedgerCreate) -> Ledger`

1) **WHAT** —— 新增一笔往来，同步累计和日历事件。
2) **WHO** —— `api/ledger.create`。
3) **INPUT** —— `person_id, direction, kind, amount, occurred_at, due_date?, note?`。
4) **OUTPUT** —— 新建的 `Ledger`。
5) **HAPPY** ——
   - `async with session.begin():`
     - insert ledger
     - `recalc_totals(person_id)`
     - `due_date` 非空 → `sync_due_event(ledger)`
   - 返回
6) **EDGE** ——
   - `amount <= 0` → Pydantic 拦（`gt=0`）
   - `due_date < occurred_at` → CHECK 约束拦，转 400
   - `person_id` 不属于自己 → 404（RLS）
   - 同一笔重复提交 → **V1 不做幂等**（用户手动录入，重复自己删）

---

#### `recalc_totals(session, person_id: int) -> None`

1) **WHAT** —— 从 ledger 重算 `total_in` / `total_out`。
2) **WHO** —— ledger 增删改后。
3) **INPUT** —— `person_id`。
4) **OUTPUT** —— 无。
5) **HAPPY** ——
   - `SELECT direction, COALESCE(SUM(amount),0) FROM ledger WHERE person_id=? GROUP BY direction`
   - 更新 `relationships`
6) **EDGE** ——
   - 无记录 → 两个都置 0
   - **必须用 SUM 重算**，不能增量加减（会漂）
   - 与 `05-confirm` 的 `recalc_relationship_totals` 是同一个逻辑，**只实现一份**

---

#### `sync_due_event(session, ledger: Ledger) -> None`

1) **WHAT** —— 有结款日的往来，同步成日历上的"结款到期"事件。
2) **WHO** —— ledger 增删改后。
3) **INPUT** —— `ledger` 记录。
4) **OUTPUT** —— 无。
5) **HAPPY** ——
   - `due_date` 为空 或 `settled_at` 非空 → **删除已存在的事件**，返回
   - 否则 → upsert `events(type='payment_due', person_id, occurs_on=due_date, source_type='ledger', source_id=ledger.id)`
6) **EDGE** ——
   - 事件标题：「{nickname}的款到期」
   - 改金额不改日期 → 只更新事件标题，不重建
   - 结清（`settled_at` 有值）→ 事件删掉
   - **`events` 表允许多条 source_id 相同的记录吗** → 不允许，靠 upsert

---

## 6. 验收标准

| # | 检查 | 怎么验 |
|---|---|---|
| 1 | 新增往来后累计变化 | 加 520，`total_in` 加 520 |
| 2 | 结款日进日历 | 加一笔带 due_date 的，日历上能看到 |
| 3 | 结清后事件消失 | 设 `settled_at`，事件应删除 |
| 4 | 金额为 0 被拒 | 提交 amount=0，返回 400 |
| 5 | 重算准确 | 删一笔，累计回到原值 |

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-28 | v1.0 | 首版 |
