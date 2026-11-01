# 22 · Harness 接入记录（W16）

> **来源**：`HANDOFF2` W16 / `docs/16-Harness接入方案.md` / `docs/ARCHITECTURE.md` 19.7
> **格式**：照 `docs/17` 的「现象 → 根因 → 修复 → 验证」。
> **时间**：2026-11-02
> **用户决定**：不等域名备案、不等服务器，"现在就接，接了再测"。

---

## 一、总览：四个阶段做到哪

| 阶段 | 做什么 | 状态 |
|---|---|---|
| **A · 跑起来** | `npx @deepseek-ai/dsh web`，配 `DEEPSEEK_API_KEY` | ⬜ **被环境挡住**（见第四节） |
| **B · 第一个工具** | 写工具插件，`fetch` 调主服务 | ✅ 代码写完（未实跑） |
| **C · 接主服务** | 5 个只读接口 + service token + 侧车容器 | ✅ 接口已验证（7 条用例） |
| **D · 前端入口 + 灰度** | 前端加"助理"入口 | ⬜ 没做（等 A/B 通了再上，否则是个点进去 404 的按钮） |

**卡点只有一个**：`dsh` 装不上 / 起不来（第四节）。主服务这半边是完整可用的。

---

## 二、做了什么

### 2.1 主服务：5 个只读接口（已完成并验证）

按 19.7.8 的清单，新建 `/api/assistant/*`，**不是**复用现成接口：

| 接口 | 返回 |
|---|---|
| `GET /api/assistant/today` | 今天该做什么（任务 + 待确认条数） |
| `GET /api/assistant/persons?keyword=` | 找人 |
| `GET /api/assistant/persons/{id}` | 档案（阶段、角色、生日、禁忌、标签、收支合计、计数） |
| `GET /api/assistant/persons/{id}/facts` | **只回已确认的事实**，带权重与依据 |
| `GET /api/assistant/ledger?person_id=` | 逐笔 + 收/支合计 + 未结清笔数（`person_id` 必填） |

**为什么不复用现成接口**（三条理由，都写进了代码注释）：

1. 现成接口要 cookie 会话 + CSRF，侧车没有浏览器
2. 现成接口的字段是给前端页面用的，塞给模型全是噪音
3. 助理需要一组**能一眼看出边界**的路径：看到 `/assistant/` 就知道是只读的

**认证**：`X-Assistant-Token` + `ASSISTANT_TOKEN`，常量时间比对。
不配 → 503（"没启用"），配错 → 401（"token 不对"）。两者分开，不让用户去查错方向。

**token 只绕过登录，绕不过 RLS**：解析出 `ASSISTANT_USERNAME` 之后照样
`session.info["rls_user_id"] = user_id`，和正常请求一条路径。有一条用例专门打这个。

**没有写接口**。`test_no_write_endpoints` 断言 POST/PATCH/DELETE 都是 404/405 ——
写操作要先把"要不要进确认队列"想清楚（19.7.9），在那之前一条都不开。

### 2.2 侧车：`harness/`

| 文件 | 作用 |
|---|---|
| `keeper-tools/index.js` | 插件本体：5 个 `defineTool`，每个 `execute` 里 `fetch` 一个只读接口 |
| `cordis.patch.yml` | 启动时叠的覆盖层，把插件挂进 web profile |
| `run.sh` | 装依赖 → 凑环境变量 → `npx dsh web --patch ./cordis.patch.yml` |
| `README.md` | 怎么跑、三条验收、五个常见故障 |
| `.env` | `KEEPER_API` + `KEEPER_ASSISTANT_TOKEN`（**不进 git**） |

`docker-compose.yml` 加了 `harness` 服务，`profiles: ["assistant"]` ——
**默认不启动**，要跑才 `docker compose --profile assistant up -d harness`。

**为什么用 `--patch` 而不是 `dsh plugin add`**：`plugin add` 要把插件打成 npm 包装进 profile，
dsh 一升级就容易跟着坏；`--patch` 只是启动时叠一层，删掉文件就回到原样。侧车随时可拆，这是前提。

### 2.3 工具的关键设计（19.7.5 的落地）

每个工具的 `description` 里都写死了"必须先查"：

> `keeper_facts`：回答"她喜欢什么""有什么要注意的"**必须调它，不许凭记忆答**

并且 `execute` 每次都真的打 HTTP，**没有任何缓存**。业务事实只有 Postgres 一份。

---

## 三、已验证的部分

```bash
cd backend && uv run pytest tests/api/test_assistant.py -q   # 7 passed
cd backend && uv run ruff check --no-cache .                  # All checks passed
```

| 用例 | 钉住什么 |
|---|---|
| `test_unconfigured_returns_503` | 没配 token 是"没启用"，不是"密码错" |
| `test_wrong_token_returns_401` | 错 token 和不带 token 都是 401 |
| `test_today_works_with_token` | token 对了能拿到今日数据 |
| `test_persons_only_returns_token_owner_data` | **token 绕不过 RLS**：别人的人一条都不出现 |
| `test_facts_only_confirmed` | 只回已确认事实 |
| `test_no_write_endpoints` | 只有 GET |
| `test_create_user_role_helper_still_works` | 顺手确认测试夹具没被改坏 |

---

## 四、没验证的部分（**如实说**）

**`dsh` 本体没装上，也没跑起来。** 原因是工具链环境故障，不是代码问题：

```
sandbox-exec: data object length 67234 exceeds maximum (65535)
```

这个错误从开始装 `npm install` 之后出现，之后**所有 shell 命令**（`echo` 也算）都返回它，
连 `dangerouslyDisableSandbox` 也一样。也就是说这台机器上的命令执行通道被卡死了，
装包、起服务、跑测试都做不了（后端那 7 条用例是在故障**之前**跑的）。

已经确认走过的路：

| 尝试 | 结果 |
|---|---|
| `pnpm add @deepseek-ai/dsh` | 下载 568 个包后被沙箱拒绝写 pnpm 全局 store（`Operation not permitted`），且 pnpm 12 的 `ERR_PNPM_IGNORED_BUILDS` 闸门要交互批准 |
| `npm install`（换 npm 绕开 store 问题） | 跑到 6 分 37 秒，命令通道整体失效，被强行终止；`node_modules` 没落地 |

**所以现在的状态**：主服务侧的接口是**真的可用且被测试覆盖**的；
侧车侧的代码是**按官方类型定义写的、但没跑过一次**。

---

## 五、恢复后要做的三步（顺序别换）

```bash
# 1) 装（约 600 个包 / 100MB+，第一次慢）
cd /Users/xamic/Customers_System/harness && npm install --no-audit --no-fund

# 2) 主服务重启（让新加的 ASSISTANT_TOKEN 生效；8000 上的旧进程没有 /api/assistant 路由）
cd .. && ./dev.sh

# 3) 起侧车
cd harness && ./run.sh        # 然后打开 http://127.0.0.1:3080
```

**第 4 步（唯一不确定的地方）**：`cordis.patch.yml` 里那一行 `- name: ./keeper-tools/index.js`
是照 `docs/16` 的 bundle 写法推的。如果起来后**5 个工具一个都不出现**，说明 dsh 这一版的
patch 格式不同，用这条命令对照真实结构再改：

```bash
npx dsh --profile web --dump-default-config
```

三条验收（在 Web UI 里问）：

| # | 问 | 期望 |
|---|---|---|
| 1 | 「你今天能查什么」 | 列出 5 个 `keeper_*` 工具 |
| 2 | 「我今天该联系谁」 | **界面上看到工具调用**，再按返回的任务回答 |
| 3 | 「孟雅楠喜欢喝什么」 | 先 find 再 facts，答案带依据 |

第 2 条的关键是"**看到工具调用**"——没调就是插件没加载，不是模型笨。

---

## 六、改动清单

| 文件 | 变化 |
|---|---|
| `backend/app/api/assistant.py` | 新建：5 个只读接口 + service token 依赖 |
| `backend/app/config.py` | 加 `assistant_token` / `assistant_username` |
| `backend/app/api/router.py` | 挂上 assistant |
| `backend/tests/api/test_assistant.py` | 新建 7 条用例 |
| `docs/07-后端设计.md` | 新增「助理」一节：接口 + 认证 + 为什么只读 |
| `.env.example` | 补 `ASSISTANT_USERNAME`（token 要自己生成，不写示例值） |
| `.gitignore` | 加 `harness/.env`、`harness/node_modules/` |
| `harness/`（新目录） | 插件 + 覆盖层 + 启动脚本 + README |
| `docker-compose.yml` | 加可选 `harness` 侧车（`profiles: ["assistant"]`） |

> 项目根 `.env` 已写入一个随机 `ASSISTANT_TOKEN`（48 位十六进制），
> 与 `harness/.env` 里的 `KEEPER_ASSISTANT_TOKEN` 同一个值。**没有在文档或日志里出现明文。**
