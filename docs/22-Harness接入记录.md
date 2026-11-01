# 22 · Harness 接入记录（W16）

> **来源**：`HANDOFF2` W16 / `docs/16-Harness接入方案.md` / `docs/ARCHITECTURE.md` 19.7
> **格式**：照 `docs/17` 的「现象 → 根因 → 修复 → 验证」。
> **时间**：2026-11-02
> **用户决定**：不等域名备案、不等服务器，"现在就接，接了再测"。

---

## 一、总览：四个阶段做到哪

| 阶段 | 做什么 | 状态 |
|---|---|---|
| **A · 跑起来** | `npx @deepseek-ai/dsh`，配 `DEEPSEEK_API_KEY` | ✅ 已跑（`0.2.1-rc.5`，618 个包） |
| **B · 工具插件** | 写工具插件，`fetch` 调主服务 | ✅ 已跑，5 个工具都被认出来 |
| **C · 接主服务** | 5 个只读接口 + service token + 侧车容器 | ✅ 接口 7 条用例 + 真调用通过 |
| **D · 前端入口 + 灰度** | 前端加"助理"入口 | ⬜ 没做（先用 Web UI，见第四节） |

**一句话**：后端接口、工具插件、模型调用、真实数据回流，全链路通了。

---

## 二、做了什么

### 2.1 主服务：5 个只读接口

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
不配 → 503（"没启用"），配错 → 401（"token 不对"）。两者分开，不让用户查错方向。

**token 只绕过登录，绕不过 RLS**：解析出 `ASSISTANT_USERNAME` 之后照样
`session.info["rls_user_id"] = user_id`。有一条用例专门打这个。

**没有写接口**。`test_no_write_endpoints` 断言 POST/PATCH/DELETE 都是 404/405 ——
写操作要先把"要不要进确认队列"想清楚（19.7.9），在那之前一条都不开。

### 2.2 侧车：`harness/`

| 文件 | 作用 |
|---|---|
| `keeper-tools/index.js` | 插件本体：5 个 `defineTool`，每个 `execute` 里 `fetch` 一个只读接口 |
| `cordis.patch.yml` | 启动时叠的覆盖层，把插件插进配置树 |
| `run.sh` | 装依赖 → 凑环境变量 → `npx dsh web --patch ./cordis.patch.yml` |
| `README.md` | 怎么跑、三条验收、五个常见故障 |
| `.env` | `KEEPER_API` + `KEEPER_ASSISTANT_TOKEN`（**不进 git**） |

`docker-compose.yml` 加了 `harness` 服务，`profiles: ["assistant"]` ——
**默认不启动**，要跑才 `docker compose --profile assistant up -d harness`。

**为什么用 `--patch` 而不是 `dsh plugin add`**：`plugin add` 要把插件打成 npm 包装进
`~/.dsh/profiles/web`，dsh 一升级就容易跟着坏；`--patch` 只是启动时叠一层，删掉文件就回到原样。

---

## 三、踩到的两个坑（都是配置格式，不是代码）

### 坑 1：patch 条目必须有 `id`……但**新增**的条目不能带

```
patch: id is required for non-insert patches     ← 只写 name 时
patch: entry "keeper-tools" not found            ← 加了 id 之后
```

两个报错看着像自相矛盾，其实说的是：

- **改/删已有条目** → 顶层要 `id` 指明改谁
- **新增条目** → 走 `insert` 列表，不写顶层 `id`

正确答案（最终采用）：

```yaml
- insert:
    - id: keeper-tools
      name: ./keeper-tools/index.js
```

依据不是我猜的，是 `~/.dsh/profiles/web/cordis.patch.yml` 里那句原话：

> a top-level YAML array of loader patch entries
> (id-targeted config overrides, disables, **and insert lists**; `!!js` expressions allowed)

**自查命令**（不用起服务、不花模型额度）：

```bash
npx dsh --profile web --patch ./cordis.patch.yml --dump-config | grep keeper
# 期望：- id: keeper-tools
#       name: file:///.../harness/keeper-tools/index.js
```

### 坑 2：`pnpm` 装不上，`npm` 能装

| 包管理器 | 结果 |
|---|---|
| `pnpm add` | 下完 568 个包后被沙箱拒绝写 pnpm 全局 store（`Operation not permitted`），且 pnpm 12 有 `ERR_PNPM_IGNORED_BUILDS` 交互闸门 |
| `npm install` | ✅ 成功：`added 618 packages in 13m` |

另外：装之前如果 `harness/node_modules` 里有上次失败留下的半成品，`npm` 会因为
**无权 unlink** 而失败（`CODEBUDDY_BROKER_DENY`）。必须先删干净再装。
删 `node_modules` 会触发"批量删除确认"，需要在交互终端里执行。

---

## 四、实跑结果（2026-11-02 03:10）

用 `dsh headless` 跑，不依赖浏览器：

```bash
cd harness
set -a && . ./.env && set +a
export KEEPER_API=http://127.0.0.1:8001     # 带 /api/assistant 的那份后端
export DEEPSEEK_API_KEY=…                   # 从 backend settings 取，不落盘
npx dsh headless --patch ./cordis.patch.yml "…"
```

**验证 1 · 工具是否加载**

> 问：「你现在有哪些 keeper 开头的工具？只列工具名，不要解释」
>
> 答：`keeper_facts` / `keeper_find_person` / `keeper_ledger` / `keeper_person` / `keeper_today`

5 个全中 → 插件确实挂进了运行时，不是只写进 YAML。

**验证 2 · 是否真的去查库（关键的一条）**

> 问：「我今天该联系谁？该做什么？」
>
> 答（节选）：
> 今天没有已排定必须联系的人……今天清单 4 条待办，其中"回访"没指定人。
> **该联系谁**：小赵（最推荐）— 最近睡得晚、有失眠，喜欢听恋爱文……
> 孟雅楠 — 最近才开始特别喜欢喝苹果汁……小包老师 — 健身、体重这话题要避开……
> 王姐 — 档案是空的。
> 另外 **1 条新事实处于待确认状态**。

回答里的 4 个人、每个人的具体事实、"待确认 1 条"**全部来自数据库**，模型编不出这些。
这就是 19.7.5 要的效果：模型负责组织语言，事实永远从 Postgres 读。

**顺带验到一件事**：它把"王姐档案是空的"也如实说了，没有硬凑 —— 符合业务三原则第 3 条。

---

## 五、还要做什么

### 想用 Web UI 的话（一条命令）

```bash
cd /Users/xamic/Customers_System && ./dev.sh      # 让 8000 上的后端带上 /api/assistant
cd harness && ./run.sh                            # 开 http://127.0.0.1:3080
```

`run.sh` 里 `KEEPER_API` 默认指 8000，所以**主服务必须先重启**——现在 8000 上跑的还是
加助理接口之前的进程。

### 没做的

| 项 | 说明 |
|---|---|
| W16-D 前端「助理」入口 | 现在只有 dsh 自带的 Web UI。要进主站前端，等用了几天再定放哪儿 |
| 会话持久化 / persona 调优 | 用的是 web profile 默认配置，没写 persona。prompt/context engineering 的空间在这儿 |
| 容器的实跑 | compose 里的 `harness` 服务没 build 过（只在宿主机跑通了） |

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
