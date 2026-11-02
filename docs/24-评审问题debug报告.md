# 24 · 评审问题 Debug 报告（2026-11-02）

> 输入：第三方评审的「需求—文档—代码—测试追溯矩阵 / 前后端契约 / P0-P2 风险 / 待验证项」。
> 原则：**能修的就修并加测试；不能在本机确认的，写清原因和复现方法，不假装已解决。**
> 结论：P0 两条全部修复；P1 四条中三条修复、一条部分修复；P2 七条修复五条、两条接受现状；契约与测试缺口已补；剩 1 条待验证项需要你提供环境信息。

---

## 一、总览

| 类别 | 评审条数 | 已修复 | 部分修复/已缓解 | 接受/待确认 |
|---|---:|---:|---:|---:|
| P0 运行与安全 | 2 | 2 | 0 | 0 |
| P1 安全/一致性 | 4 | 3 | 1（R5） | 0 |
| P2 改进项 | 7 | 5 | 0 | 2（R9/R13） |
| 追溯缺口 | 6 | 5 | 1（管理 UI 仍属 V1 范围外） | 0 |
| 契约不一致 | 12 | 12（文档/前端已对齐代码） | 0 | 0 |

回归结果：

| 关卡 | 结果 |
|---|---|
| 后端 pytest | **184 passed**（原 173，新增 11 条） |
| 后端 ruff | 干净 |
| 前端 Vitest | **7 passed**（新增，原来 0） |
| vue-tsc / build | 通过 |
| e2e（真浏览器） | **8/8 通过**（含新增标签筛选） |
| 迁移 | `0006 (head)` |
| 部署脚本语法 | `dev.sh` / `deploy.sh` / `backup.sh` / `harness/run.sh` 全部 `bash -n` 通过 |

---

## 二、P0：必须修

### R1 · 每日任务从未接入部署 —— ✅ 已修

**原来**：`jobs/daily.py` 只能手动 `--serve`，`docker-compose.yml` 只有 db/app/web，`dev.sh` 也只起 uvicorn + 前端。
**后果**：部署态"扫事件 → 生成提醒 → 推送 → 权重衰减"永不执行，项目第一价值失效。

**修法**：
- `docker-compose.yml` 新增 `scheduler` 服务：同一 backend 镜像，`command: uv run python -m app.jobs.daily --serve`，独立容器（崩了不影响 API）。
- `deploy.sh` 部署时一并起 `scheduler`。
- `dev.sh` 默认也起调度器；新增 `./dev.sh --no-scheduler` 供调试时关掉。
- 已用 `docker compose config --services` 验证服务清单包含 `db/app/scheduler/web`。

**未验证**：真容器里 08:00 触发需要等到明天，或你手动 `docker compose exec scheduler uv run python -m app.jobs.daily` 跑一次。这不影响"接线已完成"。

### R2 · SECRET_KEY 有可预测默认值 —— ✅ 已修（需你换一次密钥）

**原来**：`secret_key` 默认 `"dev-only-secret-change-me"`，生产忘配可伪造会话。
**修法**：
- `Settings.secret_key` **删除默认值**，未配置直接启动失败（Pydantic ValidationError）。
- 启动时 `_assert_secret_key_is_safe()`：生产环境长度 <32 或命中 `change-me/dev-only/example` 直接 `RuntimeError`；开发环境只告警。
- 新增 `tests/core/test_config.py` 钉住"无默认值"。

**当前环境实测（未读取明文）**：`secret_key_present=true`，但 `secret_key_strong=false`。
**你需要做**：
```bash
python -c "import secrets;print(secrets.token_urlsafe(32))"
# 把结果写进 .env 的 SECRET_KEY，然后重启
```
不换的话本地仍能跑（只告警），但 `APP_ENV=prod` 会拒绝启动——这是故意的。

---

## 三、P1（应尽快修）

### R3 · 本地默认用 owner 连库，RLS 被静默绕过 —— ✅ 已修

**核实**：数据库里 `keeper` 是 superuser + BYPASSRLS，`keeper_app` 两者皆 false。
**修法**：
- 默认运行时连接改为 `keeper_app`；迁移专用 `DATABASE_URL_MIGRATE` 用 owner。
- `.env.example` 拆成两条显式连接串。
- 启动时查 `pg_roles`：如果当前角色 superuser/owner，开发环境告警，生产直接拒启动。
- compose 的 app/scheduler 都注入 `DATABASE_URL_MIGRATE`（host 是 `db`，不是 localhost）。

**当前环境实测**：`runtime_db_user=keeper_app`，启动日志只剩 SECRET_KEY 告警，没有 RLS 绕过告警。✅

### R4 · 助理 token 明文存 localStorage —— ✅ 已缓解（残余风险已说明）

**修法**：改存 `sessionStorage`，关闭标签页即清除；页面文案明确写"只在这个标签页记住"。
**残余**：同标签页内 XSS 仍可读取。彻底解决需要后端代理 dsh 并注入 token（V2，成本高）。dsh token 每次重启变化，暴露窗口有限。

### R5 · 前后端类型双轨 —— ⚠️ 部分修复

**修法**：
- 新增 `frontend/scripts/gen-api-types.mjs`，**不需要先起 8000**：后端代码直接导出 OpenAPI → openapi-typescript 生成。
- `npm run gen:types` 已可用，产出 `src/api/schema.d.ts`（已生成并提交）。
- 删除零引用死类型：`LoginData` / `PageMeta` / `ReminderOut` / `AdminUser`。

**残余**：视图仍使用手写 `types.ts`（可读性更好）。目前是"生成物可对照"，不是"单一来源"。彻底切换要重写所有视图类型，收益不抵风险，留 V2。

### R6 · 前端零自动化测试 —— ✅ 已建立最小基线

**修法**：新增 Vitest + happy-dom，`npm test` 7 条：
- `client.test.ts`：envelope 解包、ApiError 形状、401 跳登录/登录页不跳、AbortError → `REQUEST_TIMEOUT`。
- `calendar.test.ts`：events/todos 合并、派生条目 id 与 `canDelete=false`、`patchTodoDue` 只改本地。

**没有做**：组件级/整页级测试。理由：当前 e2e 已覆盖真实交互，组件测试维护成本高。前端测试从 0 到 7，先把最底层的契约和 store 逻辑钉住。

---

## 四、P2（改进项，非阻断）

### R7 · 导出 ZIP 全内存 —— ✅ 已修

`io.BytesIO` → `tempfile.SpooledTemporaryFile(max_size=1MB)`：小包内存，大包落临时磁盘；响应结束由 BackgroundTask 关闭文件，打包异常也关闭。

### R8 · 管理员可把最后一个 owner 改成 member —— ✅ 已修

新增判断：`payload.role == member && user.role == owner && 其他活跃 owner == 0` → 400「不能把最后一个 owner 改成 member」。
测试用 monkeypatch 把"其他 owner 数"置 0，避免动到真实 Admin 账号。

### R9 · 批量确认非原子 —— ✅ 接受现状 + 已有测试

语义就是"逐条确认，非 pending 跳过"，`BatchOut` 返回 `confirmed/skipped/skipped_ids`；`test_batch_confirm_skips_non_pending` 已钉住。改成单事务会让"一条坏数据导致整批回滚"，与当前产品语义冲突，不改。

### R10 · PersonsView 无 catch —— ✅ 已修

`load()` 加 try/catch + 页内错误条；失败时清空列表并显示"人没取到，刷新试试"。同时把标签筛选的 `/tags` 失败做成静默降级（不阻塞人列表）。

### R11 · 前端 18 个 `_tmp_*` 空文件 —— ✅ 已修

已删除 18 个文件；`frontend/.dockerignore` 增加 `_tmp_*`，以后不会再进镜像。

### R12 · fetch 无超时 —— ✅ 已修

`client.ts` 加 `AbortController`，默认 30s，超时抛 `ApiError(408, REQUEST_TIMEOUT, "请求超时，请重试")`；有单测。

### R13 · 登录限流进程内 —— ✅ 接受（单副本部署）

多副本会按副本数放大，文档已注明。当前是单容器 + 20 人，不值得上 Redis。若未来水平扩展，需改共享存储。

---

## 五、追溯矩阵缺口

| 缺口 | 处置 | 证据 |
|---|---|---|
| 综述重写接口无测试 | ✅ 新增 API 测试（monkeypatch 模型调用） | `test_traceability_gaps.py::test_summary_rewrite_endpoint` |
| 提醒 regenerate 无测试 | ✅ 新增（走无人物事件降级路径，不花额度） | `test_traceability_gaps.py::test_reminder_regenerate_replaces_payload` |
| 提醒 done 无测试 | ✅ 新增幂等测试 | `test_traceability_gaps.py::test_reminder_done_is_idempotent` |
| GET /tags 无测试 | ✅ 新增 + 前端接上标签筛选 | `test_tags_filter_by_key`、`PersonsView.vue`、e2e `persons` |
| PATCH /confirmations/{id} 未接线 | ✅ 确认页新增「改」：改内容/归属/类别后确认 | `ConfirmView.vue`、`test_confirmation_patch_reassigns_and_confirms` |
| 管理接口无前端 | ⚠️ 保持 V1 范围外（单用户由 CLI/接口建号） | 报告中明示，不假装做了 |

---

## 六、契约不一致（C1–C12）

**原则：以代码为准反向更新文档。** 已更新 `docs/07`、`docs/06`：

| # | 处置 |
|---|---|
| C1 `reminders/{id}/done` | docs/07 补入提醒表 + 语义说明 |
| C2 `GET /raw-inputs` | docs/07 补入投喂表 |
| C3 `summary/rewrite` | docs/07 补入人表 |
| C4 `GET /tags` 前端未接 | 已接 PersonsView 标签筛选 |
| C5 `PATCH /confirmations/{id}` 未接 | 已接 ConfirmView「改」 |
| C6 确认列表结构过时 | 改为实际 `data: ConfirmationGroup[]` |
| C7 今日 tasks 上限 3 → 20 | docs/07 改为 20 + `tasks_more` |
| C8 提醒转待办状态 | 改为 `sent/clicked/pending` |
| C9 `reminders.last_error` | docs/06 补列 + 解释"失败留 pending" |
| C10 建号密码默认值 | 之前已修，本次复核仍为必填 |
| C11 类型双轨 | 生成 `schema.d.ts` + 离线 gen:types（部分修复，见 R5） |
| C12 死类型 | 已删除 4 个零引用类型 |

---

## 七、待验证项（V1–V4）

| 项 | 结论 | 依据 |
|---|---|---|
| V1 SECRET_KEY / DEEPSEEK_API_KEY 是否强随机 | **SECRET_KEY 已配置但不够强**；DEEPSEEK 已配置 | 只运行脚本打印布尔值，未读取明文。按 R2 换一次 SECRET_KEY |
| V2 compose 引用文件是否存在 | ✅ 全部存在 | `harness/run.sh`、`db/init-keeper-app.sh`、`Caddyfile`、两个 Dockerfile 均存在 |
| V3 文档 15 张表 vs 实际 | ✅ 文档已纠正 | V1 实际 13 张：11 业务 + extraction_logs + notification_channels；V2 三张（temperature/vector/agent_runs）未建，属既定推迟 |
| V4 农历生日年表逐日准确性 | ⚠️ 未逐日验证 | 已覆盖已知节日/闰日退化/跨年；完整年表需要一份权威农历数据做对照，属数据验证而非代码 bug |

---

## 八、新增回归测试清单

```
backend/tests/core/test_config.py                   4 条  数据库角色默认值、SECRET_KEY 无默认
backend/tests/api/test_traceability_gaps.py         5 条  done/regenerate/summary/tags/reassign
backend/tests/api/test_admin.py                     +2 条 最后 owner 降级保护
frontend/src/api/client.test.ts                     5 条  envelope/ApiError/401/超时
frontend/src/stores/calendar.test.ts                2 条  合并/派生不可删/改期只改本地
e2e/cases/persons.mjs                               扩展   标签筛选（种子新增 1 个标签）
```

---

## 九、你还需要做的两件事（代码已就绪，环境未就绪）

1. **换 SECRET_KEY**（见 R2）。不换本地能跑、生产会拒启动。
2. **补 `APP_DB_PASSWORD`**：`docker compose config` 当前警告该变量未设置。
   如果 keeper_app 角色已存在，把它建角色时用的密码写进 `.env`；没有就按
   `db/init-keeper-app.sh` 重建一次角色。否则 app/scheduler 容器连不上库。

除此之外，重启 `./dev.sh` 即可看到本次所有改动。
