# 09 · 提醒板块 Spec

> 对应文件：`services/reminder.py`、`services/notify.py`、`jobs/daily.py`
> 计划：**W9**
> 依赖：`events`、`reminders`、`facts`、`ledger`、`persons`、`notification_channels`

---

## 1. 这个板块解决什么问题

**一句话**：在正确的时间，推一条**带依据、能直接用**的提醒。

这是用户感知最强的功能。三个约束：

**① 建议必须带依据。** "建议送茶叶"是废话；"因为她 10-03 说过喜欢喝茶、去年送过香水不重复"才有用。
**② 必须幂等。** 定时任务重跑不能重复发。靠 `UNIQUE(event_id, remind_on, level)`。
**③ 通道要能降级。** 企微 webhook 失败 → PushPlus。

---

## 2. 流程

```
每日 08:00（APScheduler）
  ↓
① 扫 events：未来 7 天内命中的
   · 生日：T-7 / T-3 / T-0
   · 结款：T-3 / T-0
   · 节日：T-2
  ↓
② 每条命中 → 生成提醒内容（四路取数）
   · 档案（硬字段 + 标签 + 综述）
   · 记忆层（关于这事他说过什么）
   · 知识层（这类人/这个预算送什么）
   · 历史往来（往年送过什么，防重复）
  ↓
③ upsert reminders(status=pending, payload=生成好的完整内容)
  ↓
④ 发送：企微 → 失败切 PushPlus → 都失败标 failed
  ↓
⑤ 用户点"转待办" → 调 10-todos
```

---

## 3. 数据流

| 操作 | 读 | 写 |
|---|---|---|
| 扫描 | `events` | — |
| 生成 | `persons`、`relationships`、`facts`、`ledger` | `reminders.payload` |
| 发送 | `notification_channels` | `reminders.status`、`sent_at` |

---

## 4. 函数清单

| 函数 | 一句话 | 被谁调用 |
|---|---|---|
| `scan_due_events` | 扫出该提醒的事件 | `jobs/daily.run` |
| `generate_content` | 生成建议 + 依据 + 话术 | `scan_due_events` |
| `upsert_reminder` | 幂等写入提醒记录 | `scan_due_events` |
| `send` | 按通道发送，失败降级 | `jobs/daily.run` |
| `to_todo` | 提醒转待办 | `POST /api/reminders/{id}/to-todo` |
| `regenerate` | 换一个建议 | `POST /api/reminders/{id}/regenerate` |

---

## 5. 逐函数六问

---

#### `scan_due_events(session, today: date) -> list[ReminderDraft]`

1) **WHAT** —— 扫出今天该提醒的事件，生成草稿。
2) **WHO** —— `jobs/daily.run`。
3) **INPUT** —— 今天日期。
4) **OUTPUT** —— 提醒草稿列表（含 event_id、person_id、level、payload）。
5) **HAPPY** ——
   - 查未来 7 天的事件（含 rrule 展开）
   - 按类型匹配档位：
     生日 `[7,3,0]`、结款 `[3,0]`、节日 `[2]`
   - 命中的 → 调 `generate_content`
6) **EDGE** ——
   - 同一天命中多个档位 → 只发最高档（T-0 > T-3 > T-7）
   - 人已删除 → 跳过
   - 模型失败 → 仍生成，`payload.degraded=true`，只给日期不生成建议

---

#### `generate_content(session, event, person_id, level) -> ReminderPayload`

1) **WHAT** —— 生成一条完整提醒：事实陈述 + 建议 + 依据 + 话术。
2) **WHO** —— `scan_due_events`。
3) **INPUT** —— 事件、人、档位。
4) **OUTPUT** —— `{headline, suggestion, reasons[], script, degraded}`。
5) **HAPPY** ——
   - 取档案 + 标签 + 综述
   - 取相关事实（`weight>0.5`，按 category 过滤）
   - 取历史往来（去年送过什么）
   - 取知识库建议
   - 调模型（`prompts/v1_reminder.md`）
   - 校验输出含 `reasons`，否则降级
6) **EDGE** ——
   - **每条建议必须能写成"因为[A]，所以建议[B]"**，不合格就重试一次
   - 档案有禁忌 → 建议里显式规避
   - 往年送过同类 → 必须提示并给替代
   - **不输出精确金额对比**（"他刷了 8000 你回 800"这种会毁关系）
   - 档位不同，话术风格不同：T-7 给选品，T-3 催下单，T-0 给开场白

---

#### `upsert_reminder(session, draft) -> Reminder`

1) **WHAT** —— 幂等写入提醒记录。
2) **WHO** —— `scan_due_events`。
3) **INPUT** —— 草稿。
4) **OUTPUT** —— 已存在的或新建的 `Reminder`。
5) **HAPPY** ——
   - `INSERT ... ON CONFLICT (event_id, remind_on, level) DO NOTHING`
   - 查回记录
6) **EDGE** ——
   - 已存在且 `status='sent'` → 不重发
   - 已存在且 `status='failed'` → 允许重试（更新 payload）
   - **这是幂等的唯一保证**，不能靠应用层判断

---

#### `send(session, reminder) -> bool`

1) **WHAT** —— 按通道优先级发送，失败降级。
2) **WHO** —— `jobs/daily.run`。
3) **INPUT** —— 提醒记录。
4) **OUTPUT** —— 成功 True。
5) **HAPPY** ——
   - 按 `priority` 排序取通道
   - POST webhook
   - 成功 → `status='sent'`, `sent_at=now`
6) **EDGE** ——
   - 通道失败（超时/非 2xx）→ 切下一个，记 WARNING
   - 全部失败 → `status='failed'`，**不抛异常**（不能因为推送失败让整个任务挂）
   - webhook URL 为空 → 跳过该通道

---

#### `to_todo(session, reminder_id) -> Todo`

1) **WHAT** —— 把一条提醒变成待办。
2) **WHO** —— `POST /api/reminders/{id}/to-todo`。
3) **INPUT** —— `reminder_id`。
4) **OUTPUT** —— 新建的 `Todo`。
5) **HAPPY** ——
   - 查 reminder → 404
   - `status` 必须是 `sent` 或 `clicked`，否则 400
   - 查是否已有 `source='reminder' AND source_id=?` 的待办 → 有则 409
   - 建 todo
6) **EDGE** ——
   - 重复转 → 409 `CONFLICT`
   - 提醒的 `person_id` 已删除 → 建 todo 时 `person_id=None`

---

## 6. 验收标准

| # | 检查 | 怎么验 |
|---|---|---|
| 1 | 造 3 天后的生日，收到推送 | 手动跑 `python -m app.jobs.daily` |
| 2 | 重跑不重复发 | 连跑两次，只收到一条 |
| 3 | 建议带依据 | 看 `payload.reasons` 非空 |
| 4 | 通道降级 | 把企微 webhook 改错，应走 PushPlus |
| 5 | 转待办 | 点按钮，今日页出现该任务 |

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-28 | v1.0 | 首版 |
