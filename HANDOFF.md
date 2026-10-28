# HANDOFF · 开发交接说明

> **这份文档给接手开发的 Agent 看。** 读完它，你应该知道：做什么、读什么、按什么顺序做、怎么算做完。
> 最后更新：2026-10-28 23:15 · 当前进度 **W2–W10 功能全部完成**

---

## 一、你要做什么

**项目**：Keeper —— 面向私域关系维护的 AI Agent（Python + FastAPI + PostgreSQL 17 + Vue 3）

**当前状态**：

| 层 | 状态 |
|---|---|
| 设计文档 | ✅ 16 份，`docs/` 下 |
| 后端骨架 | ✅ 可运行，`backend/` |
| 数据库 | ✅ **12 张表 + RLS + 默认值（0003）** |
| 认证 + 人模块 | ✅ **10 个接口（W2）** |
| 其余业务接口 | ✅ **45 个接口全部实现（W3–W10，88 个测试通过）** |
| 前端 | ✅ **8 个页面（登录/今日/投喂/人/档案/确认/日历/待办/设置）** |
| 部署 | ✅ 三容器 + `deploy.sh` + `backup.sh`（**等你填域名与 Key**） |

**你的任务**：**代码已经写完了。** 现在要做的是把东西填上、跑起来、验证它好不好用——
见 [`docs/待填写清单.md`](./docs/待填写清单.md)。

---

## 二、开工前必须读的（顺序不能乱）

| 顺序 | 文档 | 读多久 | 为什么必须读 |
|---|---|---|---|
| 1 | `docs/CONSTITUTION.md` | 3 分钟 | 不可让步的原则，违反就是返工 |
| 2 | `docs/ARCHITECTURE.md` 第 11、15、19.6 节 | 15 分钟 | 目录怎么放、RLS 怎么生效、什么时候不许加封装 |
| 3 | `docs/待填写清单.md` | 5 分钟 | **需要你填的东西都在这里（密码 / Key / 域名 / 素材）** |
| 4 | `docs/W3-W10-完成报告.md` | 10 分钟 | 这一轮做了什么、验证了什么、有什么取舍 |
| 5 | `specs/README.md` | 3 分钟 | 函数六问格式 |
| 6 | `specs/03-ingest.md` ~ `specs/11-today.md` | 按需 | 已实现，改动前先看六问 |
| 7 | `docs/10-接口响应规范.md` 第 3、5 节 | 5 分钟 | 响应长什么样 |

**其他文档按需查，不要通读。**

---

## 三、六条铁律（违反就是错）

| # | 铁律 | 出处 |
|---|---|---|
| 1 | **`api/` 不写业务逻辑**。路由函数超过 30 行就是放错了 | ARCHITECTURE 11.2 |
| 2 | **`services/` 不 import fastapi**（`HTTPException` 除外） | 同上 |
| 3 | **`models/` 不写查询**。出现 `select()` 就是错的 | 同上 |
| 4 | **查询不写 `where user_id`**。RLS 会自动加 | ARCHITECTURE 15.5 |
| 5 | **业务错误用 `AppError`**，不裸抛 `HTTPException` | ARCHITECTURE 13.2 |
| 6 | **加任何封装前答三问**：框架有吗？说得清吗？当前有调用者吗？ | ARCHITECTURE 19.6 |

---

## 四、写一个模块的标准动作（9 步，照做）

```
1. 读该板块的 spec（specs/NN-xxx.md）
2. models/ 有表吗？没有先加（W1 已建 12 张，一般不用加）
3. services/xxx.py 写业务逻辑（先写测试再写实现也行）
4. tests/services/test_xxx.py 写单测（不起 HTTP）
5. api/xxx.py 写路由 + 请求/响应模型（每个 ≤30 行）
6. api/router.py 挂一行 include_router
7. curl 验证：状态码、响应格式、日志
8. 换第二个账号验证：跨用户拿不到数据（验证 RLS）
9. git commit + push（推送必须带代理，见第八节）
```

**第 7、8 步不许跳。** 它们是唯一能证明"对"的环节。

---

## 五、排期

排期在 **`docs/14-开工手册.md`** 第三节，表格到天。这里给摘要：

| 周 | 主线 | 交付 | 周验收 |
|---|---|---|---|
| ~~W1~~ | ~~建表 + RLS~~ | ✅ **已完成** | 双账号隔离通过 |
| ~~W2~~ | ~~认证 + 人模块~~ | ✅ **已完成**（见 `docs/W2-完成报告.md`） | 六条全过，42 测试绿 |
| ~~W3~~ | ~~部署上线~~ | ✅ 脚本就绪（**域名待填**） | `./deploy.sh` 一条命令 |
| ~~W4~~ | ~~投喂上传~~ | ✅ `api/ingest.py` | 传 10 张不卡 |
| ~~W5~~ | ~~VLM 抽取~~ | ✅ `services/vision.py`<br>`services/extract.py` | 代码就绪，**准确率待真实素材验收** |
| ~~W6~~ | ~~确认队列~~ | ✅ `services/confirm.py` | 传图→确认→档案变厚 |
| ~~W7~~ | ~~画像~~ | ✅ `services/profile.py` | 能筛"谁喜欢茶" |
| ~~W8~~ | ~~往来 + 日历~~ | ✅ `services/ledger.py`<br>`services/festival.py` | 农历生日算对（2027 已验） |
| ~~W9~~ | ~~提醒 + 推送~~ | ✅ `services/reminder.py`<br>`services/notify.py` | 代码就绪，**待真实推送验收** |
| ~~W10~~ | ~~待办 + 今日~~ | ✅ `services/todo.py`<br>`services/today.py` | 端到端走通一天 |
| **W11-12** | 前端按真稿微调 | 现在是功能版（配色已对齐 v5） | **等你用一周后按真实反馈改** |

**每周结束必须 commit + push，并在 `docs/W{N}-完成报告.md` 写一份简报。**

---

## 六、每个模块的 Spec 在哪

| 板块 | Spec | 状态 |
|---|---|---|
| 认证 | `specs/01-auth.md` | ✅ 完整 · **已实现（W2）** |
| 人 | `specs/02-persons.md` | ✅ 完整 · **已实现（W2）** |
| 投喂 | `specs/03-ingest.md` | ✅ 完整 |
| 抽取 | `specs/04-extract.md` | ✅ 完整 |
| 确认 | `specs/05-confirm.md` | ✅ 完整 |
| 画像 | `specs/06-profile.md` | ✅ 完整 |
| 往来 | `specs/07-ledger.md` | ✅ 完整 |
| 日历 | `specs/08-events.md` | ✅ 完整 |
| 提醒 | `specs/09-reminder.md` | ✅ 完整 |
| 待办 | `specs/10-todos.md` | ✅ 完整 |
| 今日 | `specs/11-today.md` | ✅ 完整 |

**spec 里没有的函数，不要写。** 需要新函数 → 先补 spec 的六问，再写代码。

---

## 七、怎么算做完

### 单模块完成（每写完一个模块自查）

```
□ spec 里的函数全部实现
□ services/ 有单测，pytest 能过
□ 路由都有 response_model
□ 写操作挂了 Depends(require_csrf)
□ curl 验证过：状态码对、响应格式对
□ 换第二个账号验证过：拿不到别人的数据
□ ruff check + ruff format 全过
□ commit + push
```

### ~~W2 完成（六条验收）~~

已全部实跑通过，证据见 `docs/W2-完成报告.md`。照抄它的做法即可：

```
✅ 三种格式登录 / ✅ 5 次锁定 / ✅ 未登录 401
✅ 双账号互相看不见 / ✅ relationships 有记录 / ✅ 删人级联 + 待办保留
```

### 接下来要跑的（需要真实环境，见待填写清单）

```
□ 手机浏览器能打开真实域名（HTTPS 不报警）
□ 刷新任意前端路由不 404（SPA fallback）
□ docker compose up 一条命令起全栈
□ backup.sh 能 pg_dump 出一个可恢复的文件
□ 30 张真实截图的抽取准确率 ≥75%
□ 手机收到一条带依据的提醒
```

---

## 八、环境与命令

### 启动

```bash
cd /Users/xamic/Customers_System

# 1. 起数据库（Docker Desktop 必须先开）
docker compose up -d db
docker compose ps                    # 期望 healthy

# 2. 起后端
cd backend
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 常用

```bash
uv run alembic upgrade head          # 迁移
uv run alembic revision --autogenerate -m "xxx"
uv run pytest                        # 测试
uv run ruff check . && uv run ruff format .
curl -s --noproxy '*' localhost:8000/api/health
```

### 推送（**必须带代理，否则 502**）

```bash
git -c http.proxy=http://127.0.0.1:7897 \
    -c https.proxy=http://127.0.0.1:7897 push origin main
```

---

## 九、卡住了怎么办

| 卡在哪 | 查哪 |
|---|---|
| 不知道代码放哪个文件 | `ARCHITECTURE` 11.3 目录责任表 |
| 想加个工具函数 / 依赖 | `ARCHITECTURE` 19.6 三条判定 |
| 两份文档说法不一致 | 优先级：`06` > `07` > `05` > `02` > `01`，然后回头改上层 |
| 不知道响应该返回什么 | `docs/10-接口响应规范` |
| 不确定某个函数怎么写 | 该板块的 `specs/NN-xxx.md` 六问 |
| 数据库连不上 | `docker compose ps`；`uv run python -c "..."` 跑 `check_database` |

---

## 十、绝对不要做

| 禁止 | 为什么 |
|---|---|
| 手工 `ALTER TABLE` | 第二天没人知道线上结构 |
| 在 `api/` 里写 `if` 判断业务 | 无法单测 |
| 查询里写 `where user_id` | RLS 会加，重复且可能出错 |
| 在业务代码里硬编码错误字符串 | 前端判断不到 |
| 加"以后可能用"的封装 | 违反 19.6，且零调用代码会腐烂 |
| 改 `docs/` 里的设计结论而不说 | 设计与代码会脱节 |
| 跳过"第二账号验证" | RLS 问题最难发现 |

---

## 十一、W1 遗留的 5 条（W2 已全部清掉，留档备查）

| # | 问题 | 状态 |
|---|---|---|
| 1 | 多数表 `default` 只在 Python 层，库里无 `DEFAULT` | ✅ `0003` 迁移补了 14 个字段的 `server_default`，`alembic check` 无漂移 |
| 2 | 500 响应没有 `X-Trace-Id` 头，响应体也没有 `trace_id` | ✅ 头和体都带，同一串 |
| 3 | 同一异常记两次日志（中间件 + handler） | ✅ 删掉中间件的 `try/except`，只在 handler 记 |
| 4 | 缺 `IntegrityError → 409` 处理器 | ✅ 已加，单测覆盖 |
| 5 | 全项目 `response_model` 零使用 | ✅ 10 个业务接口全部带 |
| （附加） | 401 缺 `WWW-Authenticate` 头 | ✅ 已补 `Cookie realm="keeper"` |

**W3–W10 之后的状态**：

| # | 问题 | 状态 |
|---|---|---|
| 1 | 登录 IP 限流 | ✅ 已做（`core/ratelimit.py`，10 次/分钟 → 429） |
| 2 | 每个认证请求占两条连接（认证一条 + 业务一条） | 已知取舍，20 人规模无影响 |
| 3 | 模型 API Key 没配 → 抽取会失败 | ⬜ **等你填**（待填写清单第 3 项） |
| 4 | 每日任务默认不常驻 | ⬜ **等你决定**（待填写清单第 7 项） |
| 5 | 综述是显式触发生成，不是读档案时懒生成 | 刻意取舍，见 W3-W10 报告第五节 |

---

## 十二、一句提醒

> **这个项目最难的不是写代码，是三个月后还能看懂。**
> 所以：函数先有 spec，路由不超过 30 行，查询不写 where user_id，加封装前先问三遍。

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-28 | v1.0 | 首版：W1 完成后的交接说明，覆盖 W2-W12 |
| 2026-10-28 | v1.1 | W2 完成：状态表、必读顺序、排期、验收清单、遗留项全部回填；新增两条 W3 待办 |
| 2026-10-28 | v1.2 | W3–W10 全部完成：45 个接口 + 8 个前端页面 + 部署脚本；必读清单改为「待填写清单」；遗留项换成待你处理的三条 |
