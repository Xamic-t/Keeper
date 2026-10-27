# 04 · 抽取板块 Spec

> 对应文件：`services/vision.py`、`services/extract.py`
> 计划：**W5**
> 依赖：`raw_inputs`、`chat_segments`、`facts`、`ledger`、`events`、`extraction_logs`

---

## 1. 这个板块解决什么问题

**一句话**：把一张聊天截图，变成结构化的"这个人的事实 / 事件 / 往来"。

这是整个项目的**技术命门**——抽不准，后面全是垃圾。

三个约束：

**① 宁可少抽，不可错抽。** 低置信度的全进确认队列，而不是直接落库。
**② 必须留出处。** 每条抽取结果带 `evidence`（原文）和 `source_id`（哪张图）。
**③ 必须有主备。** DeepSeek 挂了切通义，都挂了标 failed 等人工。

---

## 2. 三步流程（为什么不是一步）

```
截图
 ↓
① 识别：这是不是聊天记录？谁说了什么？时间？
 ↓
② 归属：这是和谁的？（从候选 20 人里选，或 unknown）
 ↓
③ 抽取：facts / events / ledger，每条带 confidence + evidence
```

| 为什么拆三步 | 说明 |
|---|---|
| 失败可定位 | 抽错了知道是看错图还是理解错 |
| 可分别重试 | 归属错了只重跑②，不用重读图 |
| 中间产物可展示 | 每一步都能给用户看，这是"可解释"的具体形态 |
| 可分别评估 | 论文里能按步骤报准确率 |

---

## 3. 数据流

| 步骤 | 读 | 写 |
|---|---|---|
| 抽取 | `raw_inputs`、`persons`（候选列表） | `extraction_logs` |
| 落库 | — | `chat_segments`、`facts`、`events`、`ledger`（均为 pending 态） |

---

## 4. 函数清单

| 函数 | 一句话 | 被谁调用 |
|---|---|---|
| `VisionClient.extract` | 统一模型入口（抽象） | `extract_from_raw_input` |
| `_call_deepseek` | 主通道 | `VisionClient.extract` |
| `_call_qwen` | 备通道 | `VisionClient.extract` |
| `build_prompt` | 组装三步抽取 prompt | `extract_from_raw_input` |
| `parse_result` | 解析并校验模型返回的 JSON | `extract_from_raw_input` |
| `extract_from_raw_input` | 编排整个抽取流程 | `run_extraction` |
| `persist_candidates` | 把抽取结果写成 pending 态记录 | `extract_from_raw_input` |

---

## 5. 逐函数六问

---

#### `VisionClient.extract(image: bytes | None, text: str | None, prompt: str, schema: dict) -> dict`

1) **WHAT** —— 调模型把图片/文字转成结构化 JSON，主备自动降级。
2) **WHO** —— `extract_from_raw_input`。
3) **INPUT** —— 图片二进制（可空）、文本（可空）、prompt、期望的 JSON schema。
4) **OUTPUT** —— 解析后的 dict。
5) **HAPPY** ——
   - 按 `settings.vision_provider` 选 provider
   - 调对应 `_call_*`
   - 用 Pydantic 校验返回结构
   - 返回
6) **EDGE** ——
   - 主通道超时/5xx → **重试 1 次** → 切备通道
   - 备通道也失败 → 抛 `AppError(AI_UPSTREAM_ERROR, 502)`
   - 返回不是合法 JSON → 抛 `AppError(AI_UPSTREAM_ERROR)`，**不要尝试修复**
   - 图片超过 8MB → 先压缩再传

---

#### `build_prompt(candidates: list[dict], kind: str) -> str`

1) **WHAT** —— 组装三步抽取的 prompt，把候选人物列表塞进去。
2) **WHO** —— `extract_from_raw_input`。
3) **INPUT** —— 候选列表 `[{id, nickname, aliases}]`（最多 20 人）、材料类型。
4) **OUTPUT** —— 完整 prompt 字符串。
5) **HAPPY** —— 从 `prompts/v1_extract.md` 读模板 → 填入候选人 → 返回
6) **EDGE** ——
   - 候选为空（第一个人）→ prompt 里说明"这是第一个联系人，归属请返回 unknown"
   - 模板文件缺失 → 抛 500（**这是部署错误，不能兜底**）

---

#### `parse_result(raw: dict, candidates: list[dict]) -> ExtractionResult`

1) **WHAT** —— 校验模型返回，过滤掉不合格的条目。
2) **WHO** —— `extract_from_raw_input`。
3) **INPUT** —— 模型原始输出、候选列表。
4) **OUTPUT** —— `ExtractionResult(person_id, segments, facts, events, ledgers)`。
5) **HAPPY** ——
   - 校验 `person_id` 在候选内，否则置 `None`
   - 每条抽取校验 `confidence` 在 0-1，`evidence` 非空
   - 丢掉缺 `content` 或 `evidence` 的条目
6) **EDGE** ——
   - `confidence < 0.7` → **保留，但标记**（仍进确认队列）
   - 金额/生日/禁忌类 `confidence < 0.85` → 保留但强制人工确认
   - 模型编造的 `person_id`（不在候选内）→ 置 None，归属待定
   - 返回为空 → 合法，表示"这张没抽出有用信息"

---

#### `extract_from_raw_input(raw_input_id: int) -> ExtractionResult`

1) **WHAT** —— 编排：读材料 → 调模型 → 解析 → 写候选 → 记审计。
2) **WHO** —— `services/ingest.run_extraction`。
3) **INPUT** —— `raw_input_id`。
4) **OUTPUT** —— 抽取结果（同时写库）。
5) **HAPPY** ——
   - 读 `raw_inputs`，拿图片或文字
   - 查最近 20 个活跃人当候选
   - `build_prompt` → `VisionClient.extract` → `parse_result`
   - `persist_candidates`：写 `chat_segments`、`facts(status=pending)`、`events`、`ledger`
   - 写 `extraction_logs`（模型名、prompt 版本、原始输出、耗时）
6) **EDGE** ——
   - **模型失败不抛到上层**：写 `raw_inputs.status='failed'`，返回空结果
   - 归属判为 unknown → `raw_inputs.person_id` 留空，事实先不落库（等用户指定人）
   - `extraction_logs` 无论如何都要写（审计需要）

---

#### `persist_candidates(session, raw_input_id, person_id, result) -> None`

1) **WHAT** —— 把抽取结果写成待确认记录。
2) **WHO** —— `extract_from_raw_input`。
3) **INPUT** —— session、材料 id、归属人 id、抽取结果。
4) **OUTPUT** —— 无。
5) **HAPPY** ——
   - `chat_segments` 逐句插入
   - `facts` 每条 `status='pending'`
   - `ledger` 直接落库（V1 无 pending 态，见 `07` 的说明）
   - `events` 插入
6) **EDGE** ——
   - `person_id` 为空 → **只写 chat_segments，不写 facts**（事实必须挂人）
   - 重复抽取同一张图 → 先删旧的 pending 记录再写（幂等）
   - 全部写在一个事务里

---

## 6. 验收标准

| # | 检查 | 怎么验 |
|---|---|---|
| 1 | 抽 30 张真实截图 | 人工核对，**事实准确率 ≥75%** |
| 2 | 金额/日期准确率 | **≥95%**（错一个 0 就完了） |
| 3 | 主备切换 | 故意填错 DeepSeek key，应自动切 qwen |
| 4 | 审计可查 | `extraction_logs` 有模型名、prompt 版本、原始输出 |
| 5 | 低置信度进队列 | 造一张模糊图，条目应标黄 |

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-28 | v1.0 | 首版 |
