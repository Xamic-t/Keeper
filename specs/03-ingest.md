# 03 · 投喂板块 Spec

> 对应文件：`services/storage.py`、`services/ingest.py`、`api/ingest.py`
> 计划：**W4**
> 依赖：`raw_inputs` 表、`data/uploads/` 目录

---

## 1. 这个板块解决什么问题

**一句话**：让人把截图丢进来，系统存下来、去重、排队等抽取。

三个约束：

**① 必须快。** 用户下播后批量传 10 张图，不能让他等。所以**上传立即返回 202**，抽取在后台跑。

**② 必须去重。** 同一张图传两次会生成两轮抽取，档案被污染两遍。用 sha256 挡。

**③ 原图可能被删。** 用户可关"保留原图"开关。关了以后，抽取完就删文件，只留文字。

---

## 2. 业务流程

```
POST /api/ingest/image  (multipart, 1-10 张)
      ↓
① 校验：张数 ≤10、单张 ≤8MB、格式 jpg/png/webp
      ↓
② 逐张：算 sha256 → 查 (user_id, file_hash) 是否已存在
      ├─ 已存在 → 返回 {duplicate: true, existing_id}
      └─ 不存在 → 存盘 → 写 raw_inputs(status=pending) → 返回 {raw_input_id}
      ↓
③ 202 Accepted（立即返回，不等抽取）
      ↓
④ BackgroundTasks: run_extraction(raw_input_id)
      ├─ status=processing
      ├─ 调 services/extract
      ├─ 成功 → status=done
      └─ 失败 → status=failed + error_msg
      ↓
⑤ 需要时才删原图（用户关了开关）
```

---

## 3. 数据流

| 操作 | 读 | 写 |
|---|---|---|
| 上传 | `raw_inputs`（查重） | `raw_inputs`、磁盘文件 |
| 查状态 | `raw_inputs` | — |
| 取原图 | `raw_inputs` | — |

---

## 4. 函数清单

| 函数 | 一句话 | 被谁调用 |
|---|---|---|
| `compute_hash` | 算文件 sha256 | `ingest_images` |
| `save_upload` | 存盘并返回路径 | `ingest_images` |
| `delete_file` | 删磁盘文件（不报错） | `delete_person`、清理任务 |
| `ingest_images` | 批量接收截图 | `POST /api/ingest/image` |
| `ingest_text` | 接收一句话 | `POST /api/ingest/text` |
| `get_raw_input` | 查处理状态 | `GET /api/raw-inputs/{id}` |
| `run_extraction` | 异步抽取入口 | `BackgroundTasks` |

---

## 5. 逐函数六问

---

#### `compute_hash(content: bytes) -> str`

1) **WHAT** —— 算内容的 sha256 十六进制串。
2) **WHO** —— `ingest_images`。
3) **INPUT** —— 文件二进制内容。
4) **OUTPUT** —— 64 字符十六进制串。
5) **HAPPY** —— `hashlib.sha256(content).hexdigest()`
6) **EDGE** —— 空内容 → 返回空文件的固定哈希（不会报错）；**不要在这里判大小**（上游已拦）

---

#### `save_upload(user_id: int, raw_input_id: int, content: bytes, suffix: str) -> str`

1) **WHAT** —— 把图片写到 `data/uploads/{user_id}/{raw_input_id}.{suffix}`。
2) **WHO** —— `ingest_images`。
3) **INPUT** —— `user_id`、`raw_input_id`、内容、后缀（`jpg`/`png`/`webp`）。
4) **OUTPUT** —— 相对路径字符串（存进 `raw_inputs.file_path`）。
5) **HAPPY** ——
   - `Path(f"data/uploads/{user_id}").mkdir(parents=True, exist_ok=True)`
   - 写文件
   - 返回相对路径
6) **EDGE** ——
   - 磁盘满 → 抛 `OSError` → 上游转 507/500
   - `suffix` 不在白名单 → 上游已拦，这里不重复校验
   - **绝不拼用户可控的路径片段**（防 `../` 穿越）

---

#### `ingest_images(session, user_id, files: list[UploadFile], person_id: int | None) -> list[IngestResult]`

1) **WHAT** —— 批量接收截图，去重后落库，返回每张的结果。
2) **WHO** —— `api/ingest.upload_images`。
3) **INPUT** —— 文件列表、可选归属人。
4) **OUTPUT** —— 每张一条：`{raw_input_id, status, duplicate, existing_id?}`。
5) **HAPPY** ——
   - 遍历文件：
     - 读内容 → `compute_hash`
     - 查 `raw_inputs` 是否已有该 hash（RLS 自动限本用户）
     - 有 → 记 `duplicate=True, existing_id=...`，跳过
     - 无 → 建 `RawInput(status='pending')` → flush 拿 id → `save_upload` → 更新 `file_path`/`file_hash`
   - 全部处理完 commit
   - 返回结果列表
6) **EDGE** ——
   - **全部重复也是 200**，不是错误
   - 单张存盘失败 → 该张标 failed，**其他张继续**
   - 传了 `person_id` 但不属于自己 → 404（RLS 查不到）
   - 张数超 10 → 上游 Pydantic 拦

---

#### `ingest_text(session, user_id, text: str, person_id) -> RawInput`

1) **WHAT** —— 存一句用户输入的话，等抽取。
2) **WHO** —— `api/ingest.upload_text`。
3) **INPUT** —— 文本 1-2000 字、可选归属人。
4) **OUTPUT** —— 新建的 `RawInput`。
5) **HAPPY** ——
   - `RawInput(kind='text', raw_text=text, status='pending')` → insert
   - commit
6) **EDGE** ——
   - 空串 → Pydantic 拦
   - 文本很长 → 仍走抽取，但 prompt 里要截断

---

#### `run_extraction(raw_input_id: int) -> None`

1) **WHAT** —— 后台任务：对一条材料跑完整抽取，更新状态。
2) **WHO** —— `BackgroundTasks`（上传后自动触发）。
3) **INPUT** —— `raw_input_id`。
4) **OUTPUT** —— 无（结果写库）。
5) **HAPPY** ——
   - 开**自己的 session**（后台任务不能复用请求的 session）
   - `status='processing'`
   - 调 `services/extract.extract_from_raw_input`
   - 成功 → `status='done'`, `processed_at=now`
   - 失败 → `status='failed'`, `error_msg=简短原因`
6) **EDGE** ——
   - **异常必须捕获**，否则后台任务静默失败
   - 模型超时 → `error_msg='AI_TIMEOUT'`
   - 原图已被删（用户同时关了开关）→ 走 `ocr_text`
   - **不能抛到 FastAPI**（后台任务没有请求上下文）

---

## 6. 验收标准

| # | 检查 | 怎么验 |
|---|---|---|
| 1 | 传 10 张不卡 | 计时，应在 2 秒内返回 202 |
| 2 | 同一张传两次 | 第二次返回 `duplicate: true`，`raw_inputs` 不新增 |
| 3 | 后台任务失败不崩溃 | 断网传图，`status` 变 `failed` 但服务正常 |
| 4 | 文件按 user_id 分目录 | `ls data/uploads/` |
| 5 | 关掉原图开关后文件被删 | 上传后检查目录为空 |

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-28 | v1.0 | 首版 |
