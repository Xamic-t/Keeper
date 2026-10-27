# 02 · 人板块 Spec

> 对应文件：`services/person.py`、`api/persons.py`
> 计划：**W2**
> 依赖：`persons` + `relationships` 表（W1 已建）

---

## 1. 这个板块解决什么问题

**一句话**：系统里所有"人"的入口——列表怎么看、档案怎么建、关系怎么改、删掉怎么清。

三个产品约束决定它的形状：

**① 列表和对象是两个东西。** 列表是**给人扫的**（一行一个人，6 个字段，带分页）；对象是**给人读的**（聚合了关系、标签、统计）。这两个用不同的响应模型，不能混。

**② 建人必须同时建关系。** `persons` 和 `relationships` 是 1:1。只建一半，后面查档案会拿到空关系，整个画像都是坏的。所以**两个 insert 必须在同一事务**。

**③ 删人是真删。** 用户要求"全删不留痕"。删一个人 → 级联 10 张表 + 删文件，**唯一例外是待办**（任务留着，只是没了归属）。

---

## 2. 业务流程

### 2.1 列表

```
GET /api/persons?page=1&size=20&keyword=王
      ↓
① get_session → 认证 + SET LOCAL（RLS 生效）
      ↓
② Pydantic 校验 page≥1、size≤100
      ↓
③ service：select persons（RLS 自动过滤，不写 where user_id）
      ↓
④ 同条件 count(*) → meta.total
      ↓
⑤ 转成 PersonListItem（6 个字段）
      ↓
{ "data": [...], "meta": {total, page, size, total_pages, has_next} }
```

### 2.2 建人

```
POST /api/persons  {nickname: "王姐", roles: ["ge_jie"]}
      ↓
① Pydantic 校验 nickname 1-50、roles 在枚举内
      ↓
② service.create_person：
   ┌─ 事务开始
   │   insert persons
   │   insert relationships（person_id = 新 id，roles 来自请求）
   └─ 事务提交
      ↓
③ 201 + Location: /api/persons/21
```

### 2.3 删人

```
DELETE /api/persons/19
      ↓
① 校验人存在且属于自己（RLS 保证后者）
      ↓
② 查出该人所有 raw_inputs.file_path
      ↓
③ 事务：DELETE persons → 级联 10 张表
      ↓
④ 事务提交后：删磁盘文件（**不能在事务里**）
      ↓
204 No Content
```

---

## 3. 数据流

| 操作 | 读 | 写 |
|---|---|---|
| 列表 | `persons`、`relationships` | — |
| 详情 | `persons`、`relationships`、`tags`、`facts`(count)、`ledger`(count)、`events`(count)、`todos`(count) | — |
| 建人 | `persons` | `persons`、`relationships` |
| 改人 | `persons` | `persons` |
| 改关系 | `relationships` | `relationships` |
| 删人 | `persons`、`raw_inputs` | 级联删 10 张表 + 文件 |

---

## 4. 函数清单

| 函数 | 一句话 | 被谁调用 |
|---|---|---|
| `list_persons` | 分页查人，带关键词搜索 | `GET /api/persons` |
| `get_person_detail` | 聚合档案（人+关系+标签+计数） | `GET /api/persons/{id}` |
| `create_person` | 建人 + 建关系（同一事务） | `POST /api/persons` |
| `update_person` | 改身份信息 | `PATCH /api/persons/{id}` |
| `update_relationship` | 改关系状态 | `PATCH /api/persons/{id}/relationship` |
| `delete_person` | 级联删 + 删文件 | `DELETE /api/persons/{id}` |

---

## 5. 逐函数六问

### 5.1 `services/person.py`

---

#### `list_persons(session, *, offset: int, limit: int, keyword: str | None = None) -> tuple[list[PersonSummary], int]`

1) **WHAT** —— 按关键词分页查人，返回当页数据和总数。
2) **WHO** —— `api/persons.list_persons`。
3) **INPUT** ——
   - `session`：带 RLS 的会话
   - `offset` / `limit`：由 `deps.Pagination` 算好后传进来（size 已限 1-100）
   - `keyword`：可选，1-50 字符，模糊匹配 `nickname` 和 `aliases`
4) **OUTPUT** —— `(当页 Person 列表, 总数)`。**列表可能为空，总数可能是 0**。
5) **HAPPY** ——
   - 构造 `select(Person)`
   - 有 keyword → `.where(Person.nickname.ilike(f"%{keyword}%"))`
   - `.order_by(Person.updated_at.desc())`
   - `.offset(pagination.offset).limit(pagination.size)`
   - 执行 → 拿到列表
   - 同条件 `count(*)` → 总数
   - 返回元组
6) **EDGE** ——
   - 无数据 → 返回 `([], 0)`，**不是 404**
   - keyword 为空串 → 当作无关键词
   - keyword 含 `%` 或 `_` → **要转义**，否则会被当通配符
   - **不写 `where user_id`** —— RLS 会加
   - `count(*)` 也会被 RLS 过滤，得到的正是"你自己的数量"

---

#### `get_person_detail(session, person_id: int) -> PersonDetail`

1) **WHAT** —— 一次查询拿到档案页需要的全部数据。
2) **WHO** —— `api/persons.get_person`。
3) **INPUT** —— `person_id` 正整数。
4) **OUTPUT** —— 聚合对象：`person` + `relationship` + `tags` + `counts`。
5) **HAPPY** ——
   - 查 `persons` → 不存在抛 `AppError(NOT_FOUND, 404)`
   - 查 `relationships`（person_id 唯一，必有）
   - 查 `tags`（未过期的）
   - 四个 `COUNT(*)`：facts / ledger / events / todos
   - 组装返回
6) **EDGE** ——
   - **不存在和越权返回同一个 404**（不区分，防泄露）
   - `relationship` 缺失（数据不一致）→ 抛 500，**不要静默补**
   - `counts` 用 `COUNT(*)`，**不要加载全量再 len()**
   - tags 过滤 `expires_at IS NULL OR expires_at > now()`

---

#### `create_person(session, user_id: int, *, nickname: str, roles: list[str], aliases=None, platform_ids=None, note=None) -> PersonSummary`

1) **WHAT** —— 建一个人，同时建他对应的关系记录。
2) **WHO** —— `api/persons.create_person`。
3) **INPUT** ——
   - `user_id`：当前用户（写操作必须显式传）
   - 其余字段就是 `PersonCreate` 拆开的入参（见第 5 节末"W2 实现说明"）
4) **OUTPUT** —— `PersonSummary`（person + relationship，tags 为空列表）。
5) **HAPPY** ——
   - `session.add(Person(...))`
   - `await session.flush()` 拿到 person.id
   - `session.add(Relationship(person_id=person.id, roles=roles))`
   - `await session.commit()`
   - 返回 `PersonSummary`
6) **EDGE** ——
   - **两个 insert 必须在同一事务**，第二个失败要回滚第一个
   - 为什么不是 `async with session.begin():` —— 见第 5 节末"W2 实现说明"：session 在
     `get_session` 里已经因为要下发 RLS 变量而开启了事务，再 `begin()` 会直接报错
   - `roles` 为空数组 → 默认 `["ge_jie"]`（Pydantic 层给默认值）
   - `nickname` 重复 → **允许**（可能有两个"王姐"），不加唯一约束
   - `user_id` 非法（外键不存在）→ 抛 500，不该发生（来自登录态）

---

#### `update_person(session, person_id: int, changes: dict[str, Any]) -> PersonSummary`

1) **WHAT** —— 改人的身份信息（昵称、别名、联系方式、备注）。
2) **WHO** —— `api/persons.update_person`。
3) **INPUT** —— `person_id`；`changes` 是 `PersonUpdate.model_dump(exclude_unset=True)`（PATCH 语义）。
4) **OUTPUT** —— 更新后的 `PersonSummary`。
5) **HAPPY** ——
   - 查 person → 不存在 404
   - 按白名单过滤 `changes`（`nickname/aliases/platform_ids/contact/note`）
   - `update(...).returning(Person)` 一条语句改完并拿回新行
   - 提交前把 tags / relationship 查好（提交后 RLS 变量已失效）
   - commit → 返回
6) **EDGE** ——
   - 空 changes → 直接返回原对象（不报错）
   - **不能改 `user_id`**（白名单里没有）
   - `note` 传 null 表示清空；其他字段传 null 视为没传（列本身 NOT NULL）
   - `updated_at` 仍是库里的 `onupdate` 生成，只是用 `RETURNING` 拿回来，不手写时间

---

#### `update_relationship(session, person_id: int, changes: dict[str, Any]) -> Relationship`

1) **WHAT** —— 改关系状态（阶段、预算、生日、禁忌、角色）。
2) **WHO** —— `api/persons.update_relationship`。
3) **INPUT** —— `person_id`；`changes` 是 `RelationshipUpdate.model_dump(exclude_unset=True)`。
4) **OUTPUT** —— 更新后的 `Relationship`。
5) **HAPPY** ——
   - 先确认 person 存在 → 不存在 404（否则"查不到人"会被误报成 500）
   - 查 relationship → 不存在抛 500（数据不一致）
   - 按白名单过滤
   - **若 `stage` 被显式传入** → 同时设 `stage_source='manual'`、`stage_updated_at=now`
   - `update(...).returning(Relationship)` → commit
6) **EDGE** ——
   - 只改 `gift_budget_min` 不传 max → 允许，但 CHECK 会拦 `min > max`
   - `birthday_type='lunar'` 但没传 `birthday_lunar` → Pydantic 层校验拦
   - 手动改过阶段后，**自动算法不再覆盖**（靠 `stage_source='manual'` 判断）

---

#### `delete_person(session, person_id: int) -> None`

1) **WHAT** —— 删一个人，级联删所有关联数据，并删磁盘文件。
2) **WHO** —— `api/persons.delete_person`。
3) **INPUT** —— `person_id`。
4) **OUTPUT** —— 无。
5) **HAPPY** ——
   - 查 `raw_inputs` 里该人的 `file_path` 列表（**先查再删，删完就查不到了**）
   - 事务：`DELETE FROM persons WHERE id=?`（`persons` / 关系等靠数据库外键级联）
   - 数据库级联自动删：relationships / facts / tags / ledger / events / reminders / raw_inputs / chat_segments / extraction_logs
   - `todos.person_id` 被置 NULL（任务保留）
   - 事务提交后，遍历 file_path 删文件
6) **EDGE** ——
   - **文件删除必须在事务外**：文件系统不支持回滚
   - 文件删除失败 → 记 WARNING，**不回滚**（数据已删，孤儿文件可后续清理）
   - 删不存在的人 → 404
   - **越权删除 → 404**（RLS 查不到）

---

### 5.2 `api/persons.py`

路由层只做三件事：接参数、调 service、包响应。**每个函数不超过 15 行。**

| 路由 | 方法 | 返回 | 状态码 |
|---|---|---|---|
| `list_persons` | GET `/api/persons` | `ok(items, meta)` | 200 |
| `get_person` | GET `/api/persons/{id}` | `ok(detail)` | 200 |
| `create_person` | POST `/api/persons` | `ok(person)` | 201 + `Location` |
| `update_person` | PATCH `/api/persons/{id}` | `ok(person)` | 200 |
| `update_relationship` | PATCH `/api/persons/{id}/relationship` | `ok(rel)` | 200 |
| `delete_person` | DELETE `/api/persons/{id}` | 空 | **204** |

**请求/响应模型**（放 `api/persons.py` 顶部，见 `ARCHITECTURE` 11.3.1）：

```
PersonCreate      nickname, roles, aliases?, platform_ids?, note?
PersonUpdate      nickname?, aliases?, platform_ids?, contact?, note?
RelationshipUpdate stage?, roles?, birthday_*?, gift_budget_*?, taboo?
PersonListItem    id, nickname, avatar_path, stage, stage_source, tags[], last_interaction_at
PersonDetail      person + relationship + tags + counts
```

---

## 6. 验收标准

| # | 检查 | 怎么验 |
|---|---|---|
| 1 | 两个账号各建一个人，互相看不见 | 双账号登录，分别 GET 列表 |
| 2 | 建人后 relationship 同步存在 | 查库 `SELECT * FROM relationships WHERE person_id=?` |
| 3 | 建人事务性 | 人为让 relationship insert 失败，persons 里不该留记录 |
| 4 | 空列表返回 200 | 搜索不存在的关键词 |
| 5 | 删人级联 | 删完查 10 张表都无该人数据；`todos.person_id` 变 NULL |
| 6 | 越权访问 404 | 用账号 B 的 person_id 请求 |
| 7 | 路由都用 `response_model` | `/docs` 里能看到响应结构 |

---

## 7. W2 实现说明（写代码时定下的三件事）

写这份 spec 时没想清楚、写代码时才撞上的，记在这里，免得下次又绕一遍。

**① service 不收 Pydantic 请求模型。**
`PersonCreate` / `PersonUpdate` 定义在 `api/persons.py`（接口契约跟路由走，ARCHITECTURE 11.3.1）。
service 若 import 它们，就变成 `services/ → api/` 的反向依赖，还会循环 import。
所以：建人拆成关键字参数；改人传 `dict`（就是 `model_dump(exclude_unset=True)` 的结果），
service 内部按白名单过滤。**`Pagination` 同理**——它定义在 `deps.py`（import 了 fastapi），
service 只收算好的 `offset` / `limit`。

**② 事务由 session 自己带着走，service 只写 `commit()`。**
`get_session` 为了让 RLS 变量有事务可依附，session 一进路由就已经在事务里了。
service 再写 `session.begin()` 会抛 `A transaction is already begun`。
所以 `create_person` 的两个 insert 天然同事务，只是最后 `commit()` 一次。

**③ 提交之后不要再用同一个 session 查库——现在也不会出错了。**
`set_config(..., is_local=true)` 随事务消亡。W2 的做法是在 `db.py` 挂 `after_begin` 事件
（见 ARCHITECTURE 15.2）：每个新事务开头自动补一次变量，所以提交后再查也不会拿到空变量。
即便如此，**返回给前端的数据尽量在提交前备齐**，少一次往返。

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-28 | v1.0 | 首版：6 个 service 函数 + 6 个路由 + 请求响应模型清单 |
| 2026-10-28 | v1.1 | W2 实现后回填：签名去掉 Pydantic 入参（改 offset/limit 与 dict）、事务写法、关系更新先校验人存在；新增第 7 节 |
