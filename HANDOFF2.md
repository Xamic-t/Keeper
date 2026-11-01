# HANDOFF2 · 交接与排期（2026-11-01）

> 这是第二份交接说明，替代 `HANDOFF.md` 作为**当前排期依据**。
> `HANDOFF.md` 保留架构背景和 W1–W12 的历史记录，这份只讲：**现在在哪、下一步做什么、怎么算做完**。
>
> 最后更新：2026-11-01 19:40（日历页接入完成）

---

## 一、一句话现状

> **代码全部写完，主线（投喂→抽取→确认→档案）已在真实数据上跑通，卡在"规模验收"和"体验打磨"两道关。**
>
> 进度：**W1–W12 完成，W13 进行中**（真实验收只跑了 3 张，Harness 未开始）。

---

## 二、今天（2026-11-01）做完的六件事

| # | 事 | 结果 | 证据 |
|---|---|---|---|
| 1 | 模型主通道接通 | DeepSeek `deepseek-flash`，**实测能看图**（能读出图上的字） | `backend/scripts/check_model.py` 四步全绿 |
| 2 | 3 张真实截图端到端跑通 | 归属**全判对**，7 条事实落库，30 段对话存下 | `~/testdata/screenshots/抽取核对.md` |
| 3 | 体验反馈 5 条修完 | 见 `docs/17-体验反馈修复记录.md` | commit `c2822a8` |
| 4 | 管理员账号 | `Admin` / `adminadmin`（owner） | commit `ff2fa30` |
| 5 | **日历页换成月网格** | 网格 + 待办上格 + 拖拽改期 + 四档配色 | `docs/19-日历页接入记录.md` |
| 6 | FullCalendar 依赖入架构文档 | 补 `ARCHITECTURE` 7.6.1（纪律 2 欠账） | 同上 |

顺带升级：抽取 prompt 从 v1 → **v2**（补上 `said_at` 取值规则，现在能读出截图里的聊天时间）。

---

## 三、现在什么能用、什么不能用

| 功能 | 代码 | 真实状态 | 卡在哪 |
|---|---|---|---|
| 登录 / 人 / 往来 / 待办 / **日历** | ✅ | ✅ 能跑（日历已换 FullCalendar 月网格，可拖拽改期） | — |
| 投喂 → 抽取 → 确认 | ✅ | ✅ **3 张跑通**，⬜ 未做规模验收 | 需要 30 张真实截图 |
| **对话式投喂 / 查询** | ✅ | ✅ 查询与发图（重复拦截）验过；⬜ 真图完整抽取未验 | 省模型额度，留给你自己试 |
| 人物综述 | ✅ | ✅ 手动触发能跑 | 懒生成没做（刻意取舍） |
| 每日提醒（生成+落库） | ✅ | ✅ 能跑（W11 修好） | — |
| **提醒真机推送** | ✅ | ⬜ **从没在真手机上收到过** | 需配推送通道 |
| 节日提醒 | ✅ | ✅ 能写库 | — |
| 今日页待办 | ✅ | ✅ 能勾 | 今天刚重做 |
| **上线部署** | ✅ | ⬜ **从没真部署过** | 域名 + 备案（20 天）|

**没有被验证过的东西，等于不存在。** 上表两个 ⬜ 是这轮之后最该消掉的。

---

## 四、排期（按这个顺序做）

### W13 · 真实验收 + 对话框（进行中）

**W13-A · 30 张截图全量验收** —— 半天

- 把 30 张真实截图拖进 `~/testdata/screenshots/`
- 跑 `uv run python scripts/test_extraction.py --dir ~/testdata/screenshots`
  （不填 `--user`，自动取 owner）
- 生成核对表 → 用户勾错项 → `--score` 算准确率

**完成判据**：事实准确率 ≥75%，金额/日期 ≥95%。
**如果没过 75%**：先看错在哪一类，再改 `prompts/v2_extract.md`（**新建 v3，不原地改**），然后重跑。

**W13-B · 对话式投喂** —— ✅ **已完成（2026-11-01）**，记录见 `docs/20-对话式投喂记录.md`

用户原话：

> 我给这个 Agent 讲，然后它把我投喂的图片也给我识别，然后我说的话也给我识别，
> 我还能在对话框跟它聊天，问喜好什么的。

已做：

| 层 | 内容 |
|---|---|
| 后端 | `POST /api/chat`（multipart）：判意图 → 投喂走 ingest+抽取（同步）/ 查询按人检索 |
| 后端 | 查询复用 `facts` / `tags` / `ledger`，**没做 RAG**；只用已确认的事实，每条带依据 |
| 前端 | `ChatView.vue`：气泡、图片缩略、发送中、失败重试；投喂页页头加「切换到对话」 |

**还没验的**：真图在对话框里跑一次完整抽取（会用掉模型额度、会往确认页塞待确认条目）。
抽取链路本身在 W13-A 的 3 张截图里验过，本次新增的只有"同步调用"那层，已用假模型覆盖。

---

### W14 · 消掉两个「从没验证过」

**W14-A · 提醒真机推送** —— 半天

现在提醒能生成、能落库、能在今日页看到，但**从没推到手机上**。
配 `WECOM_WEBHOOK` 或 `PUSHPLUS_TOKEN`，跑 `uv run python -m app.jobs.daily`，手机真收到一条。

**完成判据**：手机收到一条带"为什么提醒你"的推送，点开能进档案。

**W14-B · 上线部署** —— 依赖域名备案

- `docs/待填写清单.md` 里第 1–3 项（域名 / 备案 / 服务器）
- **备案要 20 天，是整条链路上最长的等待**。哪怕后面功能还要改，现在就该启动
- 部署后立刻验证：手机浏览器打开 HTTPS 不报警、刷新任意前端路由不 404、`backup.sh` 能 dump 出可恢复文件

**完成判据**：用 4G（不是家里 WiFi）在手机上打开，三分钟内知道今天该联系谁。

---

### W15 · 清债（可选，按需插入）

`docs/15-代码评审报告.md` 还剩 **P2 十二条 / P3 八条**。2026-11-01 先清了两条最该清的：

| 条目 | 状态 | 做了什么 |
|---|---|---|
| P2-2 `_clear_previous` 不过滤 status | ✅ 已修 | 只删 `status='pending'` 的事实，已确认的不再被重抽静默删掉；`tests/services/test_extract_clear.py` 钉住 |
| P2-1 初始密码硬编码进 git | ✅ 已修 | 删掉 `INITIAL_PASSWORD` 常量与默认值，`password` 改为必填；`tests/api/test_admin.py` 验证缺字段被拒 |
| P2-12 导出 ZIP 文件名可 Zip Slip | ✅ 已修 | `safe_name()` 清洗昵称，只放行文字/数字/下划线/汉字 |
| P2-6 图片 MIME 永远写 `image/jpeg` | ✅ 已修 | 按字节嗅探真实类型，png/webp 不再标成 jpeg |
| P2-8 提醒模板 `{tags}` 永远空 | ✅ 已修 | 查 tags 并过滤过期，渲染成 `[{"key","value"}]` |
| P3-1 前端页面零错误处理 | ✅ 已修（实际只剩两页） | 待办页 / 设置页加 catch + 页内错误条 |
| P2-4 `ensure_daily_template` 并发重复 | ✅ 已修 | **评审建议的唯一索引是错的**（模板一天 3 条会被压成 1 条）；改用事务级 advisory lock，不动表结构 |

细节见 `docs/21-剩余清债与业务时区.md`。同一份文档里还记了一条更严重的：

> **T1 · "今天是哪天"按 UTC 算** —— 容器 TZ 是 UTC，北京时间 0–8 点之间今日页会显示昨天的任务、
> 生日当天的 T-0 提醒会提前一天发。已统一走 `app/core/clock.py` 的 `local_today()`（Asia/Shanghai），
> 时间戳仍用 UTC。`ARCHITECTURE` 8.6 已加这条规则。

剩下 P2/P3 里还值得做但没动的：

| 条目 | 为什么值得做 |
|---|---|
| P3-2 `limit` 没有上限 | `api/persons.py` 可传 `limit=99999` |
| P3-5 `ratelimit._hits` 只增不减 | IP 多了会慢慢涨 |
| P3-7 `avatar_path` 只出不进 | 全项目没有写入点，永远为 None |

---

### W16 · 接 Harness（已立项，不急着做）

方案在 `docs/16-Harness接入方案.md`，四阶段：A 跑起来 → 写工具插件 → C 接主服务侧车 → D 灰度。
落地记录见 `docs/22-Harness接入记录.md`。

**前置条件改了（2026-11-02 用户拍板）**：原写"等 W13-A 验收通过"，用户明确否掉：
30 张截图只能验"API 能不能识图"，而他要的是 Harness 来管这件事。**先接，接了再测。**

| 阶段 | 状态 |
|---|---|
| C 主服务侧 5 个只读接口 + service token | ✅ 已完成，7 条用例覆盖 |
| B 工具插件（5 个 `defineTool`）+ 侧车文件 | ✅ 代码写完 |
| A `dsh` 装起来跑通 | ⬜ **被环境挡住**：`sandbox-exec: data object length ... exceeds maximum`，全部 shell 命令失效，`npm install` 没跑完 |
| D 前端"助理"入口 | ⬜ 等 A/B 通了再上，否则是个点进去 404 的按钮 |

**恢复后三步**（顺序别换，详见 `docs/22` 第五节）：

```bash
cd harness && npm install --no-audit --no-fund   # 约 600 包 / 100MB+
cd /Users/xamic/Customers_System && ./dev.sh      # 让 ASSISTANT_TOKEN 生效
cd harness && ./run.sh                            # 开 http://127.0.0.1:3080
```

唯一不确定的地方：`cordis.patch.yml` 里 `- name: ./keeper-tools/index.js` 那一行是照
`docs/16` 推的；若 5 个工具都没出现，用 `npx dsh --profile web --dump-default-config` 对照真实格式。

**完成判据**：问"我今天该联系谁"，它自己调接口查了再回；且 Harness 挂了主流程不受影响。


---

## 五、优先级建议（如果只能做三件）

1. **W13-A 30 张验收** —— 不验就是盲飞，后面所有判断都建在"3 张跑通了"上
2. **W14-B 启动备案** —— 20 天等待，越早启动越好，不占你时间
3. **W13-B 对话框** —— 你自己最想要的，而且它决定了你会不会天天用

顺序上 1 和 2 可以并行（备案是等，不是做）。

---

## 六、已知的坑（别再踩一遍）

### 环境

- **装了新前端依赖后必须重启 vite**：正在跑的 dev server 会对新包返回 `504 (Outdated Optimize Dep)`，
  页面白屏、控制台报 `Failed to fetch dynamically imported module`。刷新没用，`Ctrl+C` 重开或 `npx vite --force`
- **沙箱读不了 `~/Downloads`**（macOS 保护，`PermissionError`）。绕法：拖进聊天 →
  WorkBuddy 落一份到 `~/.workbuddy/blobs/`，那个路径可读；或拖进 `~/testdata/screenshots/`
- **用户明确要求：不要读 `.env`**。要看配置状态，让脚本自己读并只打印「已填/未填」
- 本地启动：`./dev.sh`，或分开起（后端 8000 / 前端 5173 / 数据库 5432）
- 提交推送要带代理：`git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 push`

### 代码约定（违反会立刻出问题）

- **列表页操作后不要 `await load()`**。本地更新视图，否则后端排序一抖，屏幕就跳
- **查询不许写 `where user_id`**，RLS 会自动加
- **services 层不许 import fastapi**
- **改 prompt 必须新建 `vN` 文件 + 升 `PROMPT_VERSION`**，不原地改
- **prompt 里的字段必须写清取值规则**，否则模型一律给 null 且不报错（v1 的 `said_at` 就是这么废的）
- `roles` 只有 `ge_jie` / `customer` 两个合法值（`api/persons.py` 的 `Literal`），库里没 CHECK，写错了不会报错

### 设计上的硬规则

- **AI 判不出归属就丢**，不许猜。要让它能判对，就得先有人（这是"新人自动建"要解决的问题）
- **抽错比不抽更糟**：没 evidence 就丢，没 reasons 就降级，Key 没配宁可 failed 也不编
- 提醒幂等靠唯一约束，不靠应用层判断

---

## 七、待定的产品问题（用户还没拍板）

1. **新人自动建、熟人自动合并**
   用户明说过这是他要的行为：「截图里是新人就自己新建，是熟人就合并到之前的人」。
   现在代码是**只认不建**，认不出就丢事实。改法：extract 让模型多输出 `detected_name`，
   落库前按 `nickname + aliases` 查，命中就挂、没命中就 `create_person` 标成待确认。约 40 行。
   **建议等 30 张验收后做**——先知道模型认昵称准不准，再决定要不要信它。

2. **数据隔离账号还留着**
   库里还有 `vb@test.com` / `u1@test` / `u2@test` / `13800138001` / `lockme` 五个测试账号。
   上线前清理，否则 RLS 验证留下的垃圾数据会跟着上生产。

---

## 八、验收命令速查

```bash
# 后端测试与静态检查
cd backend && uv run pytest -q && uv run ruff check --no-cache .

# 前端类型检查
cd frontend && npx vue-tsc --noEmit -p tsconfig.json

# 每日任务（提醒生成）
cd backend && uv run python -m app.jobs.daily
# 期望：created=N sent=N failed=0

# 抽取验收
cd backend && uv run python scripts/test_extraction.py --dir ~/testdata/screenshots
cd backend && uv run python scripts/test_extraction.py --score ~/testdata/screenshots/抽取核对.md

# 迁移状态
cd backend && uv run alembic current   # 应为 0005 (head)
```

**当前基线**：131 测试通过 · ruff 干净 · vue-tsc 无错 · 迁移 0005

---

## 变更记录

| 日期 | 内容 |
|---|---|
| 2026-11-01 | 首版：W12 完成、W13 进行中的真实状态；重排 W13–W16；补环境坑与产品待定项 |
| 2026-11-01 | 日历页接入 FullCalendar 6.x（`docs/19`）；新增环境坑：装完依赖必须重启 vite |
| 2026-11-01 | W13-B 对话式投喂完成（`docs/20`）；W15 清掉 P2-1 / P2-2；基线 106 → 120 |
| 2026-11-02 | 清 P2-6 / P2-8 / P2-12 / P3-1 + P2-4（改用 advisory lock），修业务时区（`docs/21`）；设置页可配 PushPlus；基线 120 → 131 |
