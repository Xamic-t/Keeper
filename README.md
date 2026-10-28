# Keeper

> ### 📍 最新进度（2026-10-28 23:15）
> **W2–W10 功能全部完成**：54 个接口操作 + 8 个前端页面 + 部署脚本，88 个测试通过。
> **代码写完了，剩下的是填东西**：密码、模型 Key、域名、真实截图 →
> 看 [`docs/待填写清单.md`](./docs/待填写清单.md)。这一轮做了什么看 [`docs/W3-W10-完成报告.md`](./docs/W3-W10-完成报告.md)。

**面向私域关系维护的 AI Agent 系统。**

> 用户只提供原始材料（聊天截图、一句话），系统完成多模态抽取、双层画像、
> 关系状态判断与主动提醒。人只做两件事：投喂和查看。

**毕设题目**：基于大语言模型的私域客户画像构建及需求感知提醒系统研发

---

## 文档（先读这个）

所有设计与决策在 [`docs/`](./docs/) 里，按顺序读：

| 顺序 | 文档 | 内容 |
|---|---|---|
| **0** | **[ARCHITECTURE](./docs/ARCHITECTURE.md)** | **总纲：业务边界 + 工程规则 + 新增模块清单。接手先读这份** |
| 1 | [00-索引](./docs/00-索引.md) | 导航 + 全部决策清单 + 技术栈一页纸 |
| 2 | [01-立项说明](./docs/01-立项说明.md) | 为什么做、给谁做、爆点与边界 |
| 3 | [02-产品设计](./docs/02-产品设计.md) | 功能清单、场景、流程、界面 |
| 4 | [03-数据与技术](./docs/03-数据与技术.md) | 数据模型、AI 链路、可解释性 |
| 5 | [05-产品形态与技术栈](./docs/05-产品形态与技术栈.md) | **唯一技术方案**，开工必读 |
| 6 | [04-计划与验收](./docs/04-计划与验收.md) | 18 周里程碑、四层验收、止损信号 |

---

## 技术栈

```
前端   Vue 3 + TypeScript + Vite + Tailwind + Pinia + Vue Router
       + FullCalendar + openapi-typescript
后端   Python 3.13 + FastAPI + SQLModel + APScheduler
数据   PostgreSQL 17 + pgvector（RLS 行级隔离）
模型   DeepSeek deepseek-flash（主）→ qwen-vl-max（备）
部署   国内云 2核4G + Docker Compose + Caddy（同域）
```

---

## 目录

```
.
├── docs/                  # 立项与设计文档
├── backend/               # FastAPI
│   ├── app/
│   │   ├── models/        # SQLModel 表定义
│   │   ├── api/           # JSON 路由
│   │   ├── services/      # 业务：抽取 / 画像 / 提醒 / 温度
│   │   └── jobs/          # 定时任务
│   ├── prompts/           # 提示词（带版本号）
│   ├── alembic/           # 数据库迁移
│   └── tests/
├── frontend/              # Vue 3 SPA
├── data/uploads/          # 截图原图（不进 git）
├── docker-compose.yml
├── Caddyfile
├── deploy.sh
└── backup.sh
```

---

## 快速开始（本地开发）

```bash
# ① 数据库
docker compose up -d db

# ② 后端
cd backend
uv sync
cp ../.env.example ../.env        # 填 key
uv run alembic upgrade head       # 建表 + RLS 策略
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# ③ 前端
cd ../frontend
pnpm install
pnpm dev
```

手机同 WiFi 访问 `http://你的电脑IP:5173` 进行真机调试。

---

## 当前进度

| 阶段 | 状态 |
|---|---|
| 需求对齐 + 立项文档 | ✅ 完成（2026-10-27） |
| 技术栈定稿 | ✅ 完成（2026-10-27） |
| W1 项目骨架 + 数据库 schema | ✅ 完成（12 张表 + RLS） |
| W2 认证 + 人模块（10 个接口） | ✅ 完成 |
| W3–W10 投喂/抽取/确认/画像/往来/日历/提醒/待办/今日 | ✅ 完成（45 个接口，88 个测试） |
| 前端 8 个页面 | ✅ 完成（配色对齐 v5 设计稿） |
| W3 部署上线 | 🟡 脚本就绪，**待填域名与 Key 后上线** |
| 真实数据验收（30 张截图准确率 ≥75%） | ⬜ 待做 |

---

## 版本管理约定

- **每次改动完成即提交并推送**，不攒批
- commit message 用中文，格式：`类型: 简述`（类型取 `feat` / `fix` / `docs` / `chore` / `refactor`）
- **绝不入库**：`.env`、`data/`、`backup/`、`node_modules/`、`frontend/dist/`
- 数据库备份靠 `pg_dump`，不靠 git

---

## 一条铁律

**人只做两件事：投喂和查看。** 任何要求用户"先整理好再录入"的设计都是失败的。
