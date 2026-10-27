# 10 · 待办板块 Spec

> 对应文件：`services/todo.py`、`api/todos.py`
> 计划：**W10**
> 依赖：`todos`、`reminders`

---

## 1. 这个板块解决什么问题

**一句话**：把"要做的事"记下来，勾掉。

三个约束：

**① 待办可以不挂人。** "回复昨天的私信"不属于任何人。所以 `person_id` 可空。
**② 删人不删任务。** 唯一用 `SET NULL` 的业务表——任务本身还有意义，只是失去归属。
**③ 错了也无所谓。** 待办不设确认队列（对应宪法原则三的例外：待办错了删掉就行）。

---

## 2. 流程

```
新增（三个来源）
  ├─ 用户手动输入
  ├─ 每日固定模板（下播 SOP）
  └─ 从提醒转过来（source='reminder', source_id=提醒id）
  ↓
列表（按 due_date 排序，未完成的在前）
  ↓
勾选 → status='done', done_at=now
```

---

## 3. 数据流

| 操作 | 读 | 写 |
|---|---|---|
| 列表 | `todos` | — |
| 新增 | — | `todos` |
| 勾选 | `todos` | `todos.status`、`done_at` |
| 删除 | `todos` | `todos` |

---

## 4. 函数清单

| 函数 | 一句话 | 被谁调用 |
|---|---|---|
| `list_todos` | 按状态和日期查 | `GET /api/todos` |
| `create_todo` | 新增 | `POST /api/todos` |
| `update_todo` | 改内容 / 勾选 | `PATCH /api/todos/{id}` |
| `delete_todo` | 删除 | `DELETE /api/todos/{id}` |
| `ensure_daily_template` | 今日模板任务补齐 | `GET /api/today` |

---

## 5. 逐函数六问

---

#### `list_todos(session, status: str | None, due_before: date | None) -> list[Todo]`

1) **WHAT** —— 查待办，默认只返回未完成的。
2) **WHO** —— `GET /api/todos`、`services/today`。
3) **INPUT** —— `status`（默认 `open`）、`due_before`（可选）。
4) **OUTPUT** —— 按 `due_date` 升序（空的排最后）。
5) **HAPPY** —— `WHERE status='open'` + 可选条件 → 排序返回
6) **EDGE** ——
   - 无数据 → 空列表 + 200
   - `status='all'` → 不过滤

---

#### `create_todo(session, user_id, payload: TodoCreate) -> Todo`

1) **WHAT** —— 新增一条待办。
2) **WHO** —— `POST /api/todos`、`services/reminder.to_todo`。
3) **INPUT** —— `title, detail?, due_date?, person_id?, source?, ai_reason?`。
4) **OUTPUT** —— 新建的 `Todo`。
5) **HAPPY** —— 校验 → insert → commit
6) **EDGE** ——
   - `title` 为空 → Pydantic 拦
   - `person_id` 不属于自己 → 404
   - `source='ai'` 时 `ai_reason` 应非空（可空但建议有）

---

#### `update_todo(session, todo_id, payload: TodoUpdate) -> Todo`

1) **WHAT** —— 改内容或勾选完成。
2) **WHO** —— `PATCH /api/todos/{id}`。
3) **INPUT** —— 只含要改的字段。
4) **OUTPUT** —— 更新后的 `Todo`。
5) **HAPPY** ——
   - `status` 改成 `done` → 同时写 `done_at=now`
   - `status` 改成 `open` → `done_at=None`
   - 逐字段 setattr
6) **EDGE** ——
   - `done → open` 允许（用户点错了要能改回来）
   - `dropped` 是"不做了"，与 `done` 区分
   - 空 payload → 返回原对象

---

#### `ensure_daily_template(session, user_id, today: date) -> int`

1) **WHAT** —— 如果今天的固定模板任务还没生成，补上。
2) **WHO** —— `services/today.get_today`。
3) **INPUT** —— `user_id`、今天。
4) **OUTPUT** —— 新建条数。
5) **HAPPY** ——
   - 查今天是否已有 `source='template'` 的任务
   - 没有 → 按模板建（"复盘今日数据"/"回复私信"/"更新哥姐动态"）
6) **EDGE** ——
   - 已有 → 返回 0（幂等）
   - **模板内容先写死在代码里**，V2 做成用户可编辑

---

## 6. 验收标准

| # | 检查 | 怎么验 |
|---|---|---|
| 1 | 勾选后 today 页消失 | 勾掉一条，刷新今日页 |
| 2 | 删人后任务还在 | 删人，查 todos 里该任务 `person_id` 变 NULL |
| 3 | 模板不重复生成 | 一天内多次打开今日页，模板任务只有一份 |
| 4 | 提醒转待办不重复 | 同一条提醒点两次，第二次 409 |

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-28 | v1.0 | 首版 |
