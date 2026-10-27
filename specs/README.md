# 函数级 Spec · 总纲

> **这套文档写给谁**：写代码的 Agent，和要验收的产品经理。
> **读法**：先读本页 → 找到要开发的板块 → 读那个板块的 spec → 再写代码。
> **原则**：**函数先有 spec，才有实现。** 没有 spec 的函数不许写。

---

## 一、这套 spec 解决什么问题

架构文档告诉你"代码放哪"，接口文档告诉你"返回什么"，**但没人告诉你"这个函数到底在干嘛"**。

结果就是：写完 30 个函数，回头看有一半不知道为什么要存在。

这套 spec 的目标只有一句：

> **任何一个函数，读它的 6 行说明，就知道它是干什么的、被谁用、什么时候会出错。**

---

## 二、每个函数的六问格式

```
#### `函数名(参数: 类型) -> 返回类型`

1) **WHAT** —— 一句话：这个函数做什么（不是怎么做）
2) **WHO**  —— 谁调用它（哪个 API / 哪个任务 / 哪个别的 service）
3) **INPUT** —— 参数含义、类型、是否可空
4) **OUTPUT** —— 返回什么、什么情况下返回空
5) **HAPPY** —— 正常路径，一步一行
6) **EDGE** —— 边界与异常：空值、越权、并发、超时、外部失败
```

**为什么是这六问**：

| 问题 | 不写会怎样 |
|---|---|
| WHAT | 三个月后不知道为什么有这个函数 |
| WHO | 没人调用 = 死代码（对应 `ARCHITECTURE` 19.6） |
| INPUT / OUTPUT | 接口对不上，前后端打架 |
| HAPPY | 实现跑偏，加了不该有的分支 |
| EDGE | 线上出事才知道没处理空值 |

---

## 三、板块索引

按开发顺序排。**W1 已完成，从 W2 开始。**

| # | 板块 | Spec | 主要文件 | 计划周 |
|---|---|---|---|---|
| — | 数据库 | 已建表，见 `docs/06` | `models/` | **W1 ✅** |
| 01 | **认证** | [`01-auth.md`](./01-auth.md) | `core/security.py`<br>`api/auth.py` | **W2** |
| 02 | **人** | [`02-persons.md`](./02-persons.md) | `services/person.py`<br>`api/persons.py` | **W2** |
| 03 | 投喂 | [`03-ingest.md`](./03-ingest.md) | `api/ingest.py`<br>`services/storage.py` | W4 |
| 04 | 抽取 | [`04-extract.md`](./04-extract.md) | `services/vision.py`<br>`services/extract.py` | W5 |
| 05 | 确认 | [`05-confirm.md`](./05-confirm.md) | `services/confirm.py`<br>`api/confirmations.py` | W6 |
| 06 | 画像 | [`06-profile.md`](./06-profile.md) | `services/profile.py`<br>`services/fact.py` | W7 |
| 07 | 往来 | [`07-ledger.md`](./07-ledger.md) | `services/ledger.py` | W8 |
| 08 | 日历 | [`08-events.md`](./08-events.md) | `services/festival.py`<br>`services/event.py` | W8 |
| 09 | 提醒 | [`09-reminder.md`](./09-reminder.md) | `services/reminder.py`<br>`services/notify.py` | W9 |
| 10 | 待办 | [`10-todos.md`](./10-todos.md) | `services/todo.py` | W10 |
| 11 | 今日聚合 | [`11-today.md`](./11-today.md) | `services/today.py` | W10 |

---

## 四、一个板块的文档结构

每份 spec 固定五节：

```
1. 这个板块解决什么问题（产品经理视角，2-3 段）
2. 业务流程（一张图）
3. 数据流（读哪些表、写哪些表）
4. 函数清单（表格：函数 / 一句话 / 被谁调用）
5. 逐函数六问
```

**第 1 节是给产品经理的**，第 5 节是给 Agent 的。可以只看前 4 节就理解整个板块。

---

## 五、三个跨板块的通用约定

| 约定 | 说明 | 出处 |
|---|---|---|
| **分层** | `api/` 调 `services/`，`services/` 不 import fastapi | `ARCHITECTURE` 11.2 |
| **错误** | service 抛 `AppError(code, message, status)`，不返回错误码 | `ARCHITECTURE` 13 |
| **权限** | 所有业务函数都在 `get_session` 里跑，RLS 自动过滤，**不写 `where user_id`** | `ARCHITECTURE` 15.5 |

---

## 六、新函数怎么加

写完一个函数，回头补 spec。顺序是：

```
1. 先在本板块的"函数清单"表里加一行
2. 再写六问
3. 再写代码
```

**如果六问里"WHO"答不出来，说明这个函数不该存在。**

---

## 七、和代码的对应关系

| Spec 里的位置 | 对应代码 |
|---|---|
| 板块 01 的 `hash_password()` | `backend/app/core/security.py` |
| 板块 02 的 `create_person()` | `backend/app/services/person.py` |

**每个函数六问的第一行就写了函数签名，直接照抄成 Python 函数定义。**

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-28 | v1.0 | 首版：总纲 + 11 个板块索引 + 六问格式 |
