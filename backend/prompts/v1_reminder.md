# v1 · 提醒建议 prompt

> 版本：v1

你要给一个做私域生意的人生成一条**能直接照着做**的提醒。只输出 JSON，不要解释。

## 输入

```json
{
  "level": "{level}",
  "event": {"type": "{event_type}", "title": "{event_title}", "date": "{event_date}", "days_left": {days_left}},
  "person": {
    "nickname": "{nickname}",
    "stage": "{stage}",
    "budget_min": "{budget_min}",
    "budget_max": "{budget_max}",
    "taboo": {taboo},
    "summary": "{summary}",
    "tags": {tags}
  },
  "memory": {memory_facts},
  "history": {history_ledger},
  "knowledge": "{knowledge_hint}"
}
```

- `level`：`d7` / `d3` / `d0`（距当天还有几天）
- `memory`：关于这件事他说过什么（已确认事实，按权重排序）
- `history`：往年和他有关的往来（送过什么、给过多少钱）
- `knowledge`：这类人、这个预算通常送什么的常识提示

## 输出

```json
{
  "headline": "一句话：还剩几天、什么事",
  "suggestion": "具体建议，能直接执行（送什么 / 说什么 / 花多少钱）",
  "reasons": ["因为…", "因为…"],
  "script": "可以直接复制发过去的一段话",
  "degraded": false
}
```

## 铁律

1. **`reasons` 不能为空**。每条必须是"因为[A]，所以建议[B]"里的那个 A，且 A 必须来自输入
   （事实、标签、历史往来、预算、禁忌），**不许凭空编**。
2. **禁忌必须规避**。如果 `taboo` 里有"年龄"，就不要提年龄。
3. **往年送过同类就提示换一个**，并给出替代方案。
4. **不要输出精确金额对比**（例如"他刷了 8000 你只回 800"）。这种话发出去会毁关系。
5. 档位不同，风格不同：
   - `d7`：帮他选品、定预算（还早，可以从容准备）
   - `d3`：催他下单/动手（时间紧了）
   - `d0`：给一段开场白，直接能发
6. `script` 要像人说的话，不要"尊敬的客户"。
7. 输入信息不足时，`degraded` 置 true，建议里只给通用做法，不要编细节。
