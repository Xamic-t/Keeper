# Keeper · 项目架构设计文档

> **这份文档是项目的总纲。** 任何人（包括三个月后的你）接手这个项目，先读这一份。
> 它讲两类东西：**业务边界**（做什么）和 **工程规范**（怎么建）。
> 细节在分文档里，本文负责给规则、给索引，不重复细节。
>
> **本文的效力**：新增依赖、调整目录、自定义错误码、更换框架——**都必须先改本文，写明原因，再动代码。**
>
> 版本：v2.1 · 2026-10-28

---

# 第一部分 · 业务边界

## 1. 一句话

> **人只提供原始材料（聊天截图、一句话），系统负责记住、判断、在该出手时提醒。**

它回答的不是"他是什么人"，而是"**我现在该做什么**"。

## 2. 用户与场景

### 2.1 谁在用

一个人经营私域的主播 / 小生意人。维护 **20 人以内**（上限）的核心哥姐与客户。
不是团队，不是公司，没有运营助理。

### 2.2 三个真实场景

| 场景 | 时刻 | 用户动作 | 系统动作 |
|---|---|---|---|
| **收提醒** | 早上 7-9 点，还没起床 | 看一眼手机 | 推送"今天该管谁" |
| **投喂** | 晚上 11 点后，刚下播，困 | 批量传截图 | 认出是谁、抽出事实、列成卡片 |
| **查档案** | 见人前 10 分钟，在车上 | 翻一眼 | 一句话说清这人是谁、别踩什么雷 |

**共同点**：都在手机上进行，都发生在用户**没耐心**的时候。整个产品的信息密度必须为"扫一眼"设计，不是"坐下来研究"。

### 2.3 三条不可妥协的原则

1. **人只做两件事：投喂和查看**——任何要求"先整理再录入"的设计都是失败的
2. **AI 的建议必须带依据**——没有出处的建议，用户不会信
3. **不确定的交给人，AI 不许硬猜**——生日、金额、禁忌抽错，比没有系统更糟

## 3. 主要业务逻辑

后端全部代码服务于这六条线。**判断一个函数该不该存在：它属于哪条线？说不出来就是多余的。**

```
① 投喂线   图片/文字 → 存储 → 异步抽取 → 确认队列 → 落库
② 画像线   事实变更 → 综述重写 → 标签派生 → 关系价值重算
③ 时间线   事件生成 → 日历查询 → 提醒扫描 → 推送 → 转待办
④ 查询线   人列表 / 档案 / 时间线 / 标签筛选
⑤ 治理线   认证 / 隔离 / 删除 / 导出 / 设置
⑥ 支撑线   定时任务 / 文件 / 审计
```

### 3.1 每条线的关键约束

| 线 | 最难的地方 | 约束 |
|---|---|---|
| 投喂 | VLM 抽不准 | **宁可少抽，不可错抽**；低置信度全进确认队列 |
| 画像 | 档案会变脏 | 事实带**半衰期**，过期自动降权 |
| 时间线 | 漏一条就是事故 | 提醒**幂等**，定时任务重跑不重复发 |
| 查询 | "谁喜欢茶"这类跨人筛选 | 走 **SQL + 标签**，不走 RAG |
| 治理 | 多用户串数据 | **RLS 在数据库层强制**，不靠应用层自觉 |
| 支撑 | 模型挂了 | **模型故障不能导致数据丢失** |

## 4. 主要功能

| 模块 | 做什么 | 版本 |
|---|---|---|
| **投喂** | 截图（多选）/ 一句话 / 手动补录 | V1 |
| **确认队列** | 抽取结果逐条 ✓ / ✗ / 改 | V1 |
| **人** | 档案、综述、标签、时间线、多重关系 | V1 |
| **往来** | 打赏 / 订单 / 礼物 / 收付款 | V1 |
| **日历** | 生日（含农历）/ 节日 / 结款 / 自定义 | V1 |
| **提醒** | 日期驱动 + AI 建议（带依据）+ 话术 | V1 |
| **待办** | 固定模板 + AI 生成 | V1 |
| **今日** | 一屏看全今天该做什么 | V1 |
| **助理** | 对话式投喂 + 查询 | V1 基础 / V2 完整 |
| **关系温度** | 五维打分驱动活跃窗口 | V2 |
| **需求感知** | 温度 × 场景的 2×2 判断 | V2 |
| **对话式问答** | 混合检索路由 | V2 |

## 5. 边界：明确不做

| 不做 | 为什么 |
|---|---|
| 自动抓取微信/抖音聊天 | 违反平台协议，会封号 |
| 全自动归档（AI 抽完直接落库） | 抽错了你不知道，比没系统更糟 |
| AI 替我聊天 | 只帮记和提醒，说话的还是人 |
| 团队协作（多人共享客户库） | 多用户是"每人独立空间"，不是共享 |
| 原生 App / 小程序 | 响应式 Web + PWA 够用 |
| 做平台 / 做社区 | 一个极锋利的个人工具，比平庸平台有价值 |

---

# 第二部分 · 工程规范

## 6. 技术架构总览

```
┌──────────────────────────────────────────────────┐
│  浏览器（手机 / 电脑）                              │
│  Vue 3 SPA + Tailwind + PWA                      │
└────────────────────┬─────────────────────────────┘
                     │ /api/*  JSON
┌────────────────────┴─────────────────────────────┐
│  Caddy（自动 HTTPS + 托管前端静态文件 + 反代 API）    │
└────────────────────┬─────────────────────────────┘
                     │
┌────────────────────┴─────────────────────────────┐
│  Uvicorn + FastAPI（单进程）                       │
│  ├── api/      路由（薄）                          │
│  ├── services/ 业务逻辑（不 import fastapi）        │
│  ├── models/   SQLModel 表定义                     │
│  ├── core/     安全、错误、日志                     │
│  └── jobs/     APScheduler 定时任务                │
├──────────────────────────────────────────────────┤
│  PostgreSQL 17 + pgvector（RLS 行级隔离）          │
│  data/uploads/  截图原图                           │
└──────────────────────────────────────────────────┘
                     │
        企业微信 webhook → PushPlus（降级）
```

**一句话**：一个 Python API + 一个 Vue 静态包 + 一个 Postgres 实例。

---

## 7. 后端语言与框架

> **规则：更换语言、框架或核心 SDK，必须先改本节，写明原因，再动代码。**
> 允许的理由：安全漏洞无补丁 / 核心依赖停止维护 / 已确认的性能瓶颈。
> 不允许的理由："看起来更先进" / "网上说那个更快" / "想试试新技术"。

### 7.1 定稿

| 项 | 选择 | 版本 |
|---|---|---|
| 语言 | **Python** | 3.13 |
| 框架 | **FastAPI** | 最新稳定 |
| ORM | **SQLModel** | 最新稳定 |
| 迁移 | **Alembic** | 最新稳定 |
| 数据库 | **PostgreSQL + pgvector** | 17 / 0.8+ |
| 定时任务 | **APScheduler** | 3.x |
| 模型 | DeepSeek `deepseek-flash`（主）→ 通义 `qwen-vl-max`（备） | — |

### 7.2 为什么是 Python

| 理由 | 具体到这个项目 |
|---|---|
| **AI 生态是 Python 优先的** | VLM 调用、向量、图像处理，Python 的 SDK 永远最新最全 |
| **开发者已有实操经验** | 学习成本为零——这一条比任何技术优势都重要 |
| **脚本化能力强** | 抽取调试、数据修复、批量回填，写个脚本就能跑 |
| **类型系统够用** | Python 3.13 + Pydantic 的类型校验，对业务代码完全够 |

**不选 Node/TypeScript 统一全栈**：前后端同语言听起来省事，但 AI 生态（模型 SDK、图像处理）在 Node 侧明显薄弱。**为了语言统一去牺牲 AI 能力，是本末倒置。**

**不选 Go / Rust**：性能不是这个项目的瓶颈。20 人规模的瓶颈在"AI 抽得准不准"，不在"每秒能处理几个请求"。用 Go 写，你会为每个 AI 功能找库、封装、踩坑。

### 7.3 为什么是 FastAPI

**先说清它是什么：微框架，不是全家桶。** 它只解决一段——`HTTP 请求进来 → 校验参数 → 调你的函数 → 返回 JSON`。

选它的四个真理由：

| # | 理由 | 落到哪一行代码 |
|---|---|---|
| 1 | **Pydantic 校验抽取结果** | VLM 返回的 JSON 字段缺失、类型错、多抽少抽都是常态，schema 写一次校验就有了 |
| 2 | **自动 OpenAPI** | 前端 TS 类型从 `/openapi.json` 生成，前后端字段永远对得上 |
| 3 | **依赖注入适配 RLS** | 每个请求注入带 `SET LOCAL app.user_id` 的 session，隔离不靠自觉 |
| 4 | **异步原生** | 调模型等几秒不阻塞其他请求 |

**不是因为它快。** 你这个并发量，Starlette 的性能优势一点都用不上。**单纯为了性能选它，就是过度选型。**

### 7.4 为什么不选 Django

Django 是全家桶：自带 ORM、Admin、用户系统、权限、模板、迁移。为什么不用？

| 维度 | Django 给的 | 对这个项目的实际影响 |
|---|---|---|
| **自带 Admin 后台** | 开箱即用的数据管理界面 | **用不上**——这个产品自己的界面就是全部，不需要第二个后台 |
| **自带用户/权限体系** | 完整的 auth、组、权限 | **太重**——我们只要"一个账号 + 一个密码 + 两种角色" |
| **Django ORM** | 和框架绑死 | **不合适**——异步支持是后补的，和 async 路由配合别扭 |
| **模板 + 表单** | 服务端渲染 | **用不上**——前端是 Vue SPA |
| **约定优于配置** | 目录、命名都定死 | **双刃剑**——你的业务形状（Person × Relationship）不贴 Django 的约定 |
| **自动 API 文档** | 需第三方插件 | FastAPI 自带 |
| **学习曲线** | 陡，要学一堆"魔法" | 一个人做，学 Django 的时间够写两个功能 |

**最关键的一条**：Django 的好处全部集中在"快速做一个带后台的内容管理系统"。**而我们的产品恰恰不需要后台**——它的界面就是它的全部。

> 反过来说：如果哪天需求变成"要给运营团队做后台管理界面"，Django 会立刻变成更好的选择。**但现在不是。**

### 7.5 为什么不选 Flask

Flask 和 FastAPI 同属微框架，为什么不选更"经典"的？

| 维度 | Flask | FastAPI |
|---|---|---|
| **类型校验** | 无，要自己装 marshmallow / pydantic | **Pydantic 内置** |
| **自动 API 文档** | 需 flasgger 等插件，配置繁琐 | **自动生成，零配置** |
| **异步** | 需换 Quart，或 ASGI 适配 | **原生** |
| **依赖注入** | 无 | **`Depends` 内置** |
| **生态** | 更老、更广 | 更现代、增长更快 |
| **AI 熟悉度** | 高 | **很高** |

**一句话**：Flask 能做的 FastAPI 都能做，而且自带了三样 Flask 要额外装的东西（校验、文档、依赖注入）。**没有理由选一个需要更多胶水的方案。**

### 7.6 框架自带 vs 需要额外库

**自带（不用装）**：

| 能力 | 用什么 |
|---|---|
| 路由分组 | `APIRouter` |
| 请求/响应校验 | Pydantic v2 |
| 依赖注入 | `Depends` |
| API 文档 | `/docs`、`/redoc`、`/openapi.json` |
| 异步 | `async def` |
| 后台任务 | `BackgroundTasks` |
| CORS | `CORSMiddleware` |
| 生命周期 | `lifespan` |
| 测试客户端 | `TestClient` |
| 文件上传解析 | `python-multipart`（唯一必装的小依赖） |

**需要额外库**：

| 能力 | 库 | 用在哪 |
|---|---|---|
| ORM | SQLModel | 15 张表的定义与查询 |
| 迁移 | Alembic | 建表、改表 |
| 配置 | pydantic-settings | 读 `.env` |
| 密码哈希 | argon2-cffi | 登录 |
| Cookie 签名 | itsdangerous | 会话、CSRF |
| 定时任务 | APScheduler | 每日 08:00 扫事件 |
| 图片处理 | Pillow | 压缩、裁边、转格式 |
| 出站 HTTP | httpx | 调 DeepSeek / 通义 |
| 模型 SDK | openai（官方 Python SDK） | DeepSeek 兼容 OpenAI 格式 |
| 农历 | lunar-python + chinesecalendar | 农历生日、法定节假日 |
| 向量 | pgvector（SQLAlchemy 扩展） | V2 的 RAG |

**明确不用**：Redis、Celery、Django、Flask、Nginx、Kubernetes、GraphQL。

---

## 8. Python 语言工程规范

> 这一节管"写法"。格式交给工具，风格交给这里。

### 8.1 工具链（不许手工调格式）

| 工具 | 作用 | 什么时候跑 |
|---|---|---|
| **ruff format** | 格式化 | 保存 / commit 前 |
| **ruff check** | Lint（含 import 排序、未使用变量） | commit 前 |
| **pytest** | 测试 | 每次改 service 后 |

**规则：不允许手工调缩进和对齐。** 格式不一致 = 没跑 ruff。

### 8.2 类型注解（强制）

**所有函数签名必须标注类型，包括返回值。**

```python
# ✅
def calculate_total(entries: list[LedgerEntry]) -> Decimal: ...

# ❌
def calculate_total(entries): ...
```

| 场景 | 规则 |
|---|---|
| 参数 | 必须标注 |
| 返回值 | 必须标注（无返回值写 `-> None`） |
| 局部变量 | 能推断的不标（`x = 1` 不用写 `x: int`） |
| 复杂结构 | 用 `TypedDict` 或 Pydantic 模型，不用裸 `dict` |

**为什么强制**：类型是给 AI 看的，也是给你三个月后看的。**没有类型的函数，改起来像拆炸弹。**

### 8.3 命名

| 对象 | 规则 | 例子 |
|---|---|---|
| 模块 / 包 | 全小写，可下划线 | `relationship.py` |
| 函数 / 变量 | snake_case | `get_person_by_id` |
| 类 | PascalCase | `PersonService` |
| 常量 | UPPER_SNAKE | `MAX_UPLOAD_SIZE` |
| 私有成员 | 前缀下划线 | `_recompute_totals` |

**禁止**：拼音命名（`wangjie`）、无意义缩写（`rel_stg`）、单字母变量（循环里的 `i` 除外）。

### 8.4 导入

**顺序固定**：标准库 → 第三方 → 本项目（ruff 自动排）。

```python
# 标准库
from datetime import datetime
from decimal import Decimal

# 第三方
from fastapi import APIRouter, Depends
from sqlmodel import Session

# 本地
from app.models.person import Person
from app.services.profile import rewrite_summary
```

**禁止**：`from xxx import *`。

### 8.5 异常

| 规则 | 说明 |
|---|---|
| **不裸 `except:`** | 至少 `except Exception`，且必须记日志 |
| **不用 `assert` 做业务校验** | `assert` 在 `-O` 下会被移除 |
| **业务异常继承 `AppError`** | 统一带 code 和 http_status |
| **异常里不放敏感信息** | 不把 SQL、文件路径、堆栈塞进 message |

### 8.6 数字与时间

| 规则 | 例子 |
|---|---|
| **金额用 `Decimal`，不用 `float`** | `Decimal("520.00")` |
| 金额和数据库类型对齐 | `numeric(12,2)` ↔ `Decimal` |
| **时间必须带时区** | `datetime.now(timezone.utc)`，不用 `datetime.now()` |
| 用 f-string | `f"person {pid}"`，不用 `%` 或 `.format()` |

### 8.7 路径与文件

| 规则 | 说明 |
|---|---|
| 用 `pathlib.Path` | 不用 `os.path.join` 拼字符串 |
| 拼接用户可控路径前必须校验 | 防止 `../` 穿越 |
| 用 `with open(...)` | 保证句柄释放 |

### 8.8 异步

| 规则 | 为什么 |
|---|---|
| **异步函数里不调同步阻塞 API** | 一个阻塞会卡住整个事件循环 |
| 必须调时用 `run_in_threadpool` | 显式丢到线程池 |
| 模型调用用 `httpx`，不用 `requests` | 后者是同步的 |

### 8.9 文档字符串

| 规则 | 说明 |
|---|---|
| 公开函数写**一行**中文说明 | 不写长篇，一行够了 |
| 复杂业务函数写清"为什么" | 代码已经说了做什么 |
| 不写"TODO: 以后优化" | 要么现在做，要么进文档待办 |

### 8.10 禁止清单

| 禁止 | 因为 |
|---|---|
| 可变对象做默认参数（`def f(x=[])`） | 会被所有调用共享 |
| 用 `print()` 调试 | 用日志 |
| 硬编码密钥、密码、路径 | 走 `.env` |
| 在 model 里写数据库查询 | 违反分层（见 11.2） |
| 用 `time.sleep()` 等异步结果 | 阻塞事件循环 |

---

## 9. FastAPI 框架最佳实践

> 这一节管"用法"。每一条都是踩过的坑。

### 9.1 路由

| # | 规则 | 为什么 |
|---|---|---|
| 1 | 用 `APIRouter` 分组，一个模块一个文件 | `main.py` 超过 100 行就没法读 |
| 2 | 路由函数**不超过 30 行** | 超了说明业务逻辑塞进来了 |
| 3 | 路由里**只做三件事**：取参数、调 service、返回 | 第四件事就是错的 |
| 4 | 路径前缀定义在 `APIRouter` 上 | 改前缀只改一处 |

```python
router = APIRouter(prefix="/api/persons", tags=["persons"])

@router.get("/{pid}", response_model=PersonDetail)
async def get_person(pid: int, svc: PersonService = Depends()):
    return await svc.get_detail(pid)
```

### 9.2 Pydantic 模型

| # | 规则 | 为什么 |
|---|---|---|
| 1 | **请求模型和响应模型分开** | 否则 `password_hash` 会跟着响应出去 |
| 2 | 响应模型必须写 `response_model` | 显式声明 = 自动过滤多余字段 |
| 3 | 输入模型不写 `id`、`created_at` | 这些是服务端生成的 |
| 4 | 枚举用 `Literal` 或 `Enum` | 自动校验，不在白名单直接 422 |
| 5 | 校验规则写在模型上，不写在路由里 | 一处定义，多处复用 |

### 9.3 依赖注入

| # | 规则 | 为什么 |
|---|---|---|
| 1 | 当前用户、db session、分页参数都做成依赖 | 不用每个函数重复写 |
| 2 | **不用全局变量传状态** | 异步下不安全 |
| 3 | 依赖可以依赖依赖（`get_session` 依赖 `get_current_user`） | 组合复用 |
| 4 | 需要清理的资源用 `yield` | 保证请求结束释放 |

### 9.4 生命周期与异常

| # | 规则 | 为什么 |
|---|---|---|
| 1 | 用 `lifespan`，**不用 `@app.on_event`** | 后者已废弃 |
| 2 | 异常处理用 `exception_handler` 全局注册 | 不在每个路由 try/except |
| 3 | 自定义异常继承 `AppError` | 统一格式 |

### 9.5 配置

| # | 规则 | 为什么 |
|---|---|---|
| 1 | 用 `pydantic-settings`，**不读 `os.environ`** | 有类型、有默认值、有校验 |
| 2 | 配置类模块级初始化一次 | 不重复读 `.env` |
| 3 | 密钥字段用 `SecretStr` | 打印时自动遮蔽 |

---

## 10. 框架最大化利用原则

> **核心一句：框架已经解决的，一行都不要自己写。**

判断标准：**动手之前先问"框架有没有现成的？"** 有就用，哪怕写法你不太熟，去查文档。

### 10.1 对照表（写代码前对着这张表查一遍）

| 需求 | ❌ 不要自己写 | ✅ 用框架的 |
|---|---|---|
| 参数必填 / 可选 | `if not name: raise` | Pydantic 类型 + 默认值 |
| 参数类型转换 | `int(request.args["id"])` | 函数签名 `pid: int` |
| 参数范围校验 | 手写 if 判断 | `Field(ge=1, le=100)` |
| 请求体解析 | `json.loads(await request.body())` | 签名直接收 Pydantic 模型 |
| 文件上传解析 | 手动解析 multipart | `UploadFile` + `python-multipart` |
| 拿当前用户 | 每个函数里查 cookie | `Depends(get_current_user)` |
| 数据库会话 | 每个函数 `create_session()` | `Depends(get_session)` |
| 分页参数 | 每个函数 `request.query_params` | 做成 `Pagination` 依赖 |
| 写 API 文档 | 手写 swagger 注释 | 自动生成 |
| 异常转响应 | 每个路由 `try/except` | 全局 `exception_handler` |
| CORS | 手写响应头 | `CORSMiddleware` |
| 启动/关闭钩子 | 模块级 `init()` | `lifespan` |
| 后台任务 | 自己开线程 | `BackgroundTasks` |
| 并发调多个模型 | 手写线程池 | `asyncio.gather` |
| 测试接口 | 手动构造 HTTP 请求 | `TestClient` |
| 连接池 | 自己管理连接 | SQLAlchemy `engine` |
| 事务 | 手写 `BEGIN/COMMIT` | `session.begin()` |
| 表结构版本 | 手写 SQL | Alembic |
| 读配置 | `os.environ.get` | `pydantic-settings` |
| 密码哈希 | 自己写哈希 | `argon2-cffi` |
| Cookie 签名 | 自己写 HMAC | `itsdangerous` |
| 定时任务 | `while True: sleep(86400)` | `APScheduler` |

### 10.2 三条判断规则

| 情况 | 怎么办 |
|---|---|
| 框架**有**这个能力 | **必须用**。写法不熟就去查文档 |
| 框架**没有**，社区有成熟库 | 用库，但先写进本文 7.6 节 |
| 框架和库**都没有** | 自己写，放 `services/`，并说明为什么必须自己写 |

### 10.3 反面例子

| 反模式 | 问题 |
|---|---|
| 每个路由 `try/except` 包一层返回 JSON | 框架已有全局异常处理，重复劳动且格式不一致 |
| 自己写 `require_login` 装饰器 | `Depends` 就是干这个的，还能自动进文档 |
| 手动 `json.loads` 请求体 | Pydantic 做校验、转换、错误提示，手写全丢 |
| 用 `print` 打日志 | 没法分级、没法带 trace_id、上线后看不到 |

---

## 11. 目录约束

### 11.1 目录结构

```
backend/
├── app/
│   ├── main.py           # 入口：lifespan、中间件、挂路由
│   ├── config.py         # pydantic-settings 读 .env
│   ├── db.py             # 引擎、SessionLocal
│   ├── deps.py           # 依赖：当前用户、session、分页
│   ├── core/
│   │   ├── security.py   # argon2、cookie、CSRF
│   │   ├── errors.py     # AppError + 错误码
│   │   └── logging.py    # 日志配置
│   ├── models/           # SQLModel 表定义
│   ├── api/              # 路由（薄）
│   ├── services/         # 业务逻辑
│   └── jobs/             # 定时任务
├── prompts/              # 提示词（带版本号）
├── alembic/              # 迁移
└── tests/
```

### 11.2 三条分层纪律（带违规判断标准）

| # | 规则 | 违规判断标准 |
|---|---|---|
| 1 | **`api/` 不写业务逻辑** | 路由函数超过 30 行 |
| 2 | **`services/` 不 import fastapi** | 出现 `from fastapi import Request`（`HTTPException` 例外） |
| 3 | **`models/` 只定义表，不写查询** | 出现 `select()` 调用 |

**第 2 条最重要**：它保证核心逻辑（抽取、画像、提醒、温度）能脱离 HTTP 单独测试。**这也是后面做对比实验的基础。**

### 11.3 目录责任表

来源标记：**【框架】** = FastAPI / Starlette 官方推荐或框架本身约定 · **【项目】** = 本项目自定义 · **【工具】** = 第三方工具的约定

| 目录 / 文件 | 主要职责 | 以后应该放什么 | 禁止放什么 | 对应的框架机制 | 示例文件 | 来源 |
|---|---|---|---|---|---|---|
| `app/main.py` | 应用入口 | `lifespan`、中间件、CORS、异常处理器注册、`include_router` | 任何路由函数、任何业务逻辑、任何数据库查询 | `FastAPI()`、`@app.middleware`、`include_router` | `main.py`（99 行） | 【框架】 |
| `app/config.py` | 读配置 | 所有环境变量字段 + 默认值 + 校验 | 业务常量（如预算档位）、密钥明文 | `pydantic-settings.BaseSettings` | `config.py`（65 行） | 【项目】 |
| `app/db.py` | 数据库连接 | 引擎、`SessionLocal`、连接池参数、连通性探测 | 查询语句、表定义、业务逻辑 | SQLAlchemy `create_async_engine` / `async_sessionmaker` | `db.py`（49 行） | 【项目】 |
| `app/deps.py` | 请求级依赖 | **只放骨架**：认证入口、RLS 会话开关 | 功能专用工具（分页、角色校验、raw session），等有调用者再加（见 19.6） | FastAPI `Depends` + `yield` 依赖 | `deps.py`（63 行） | 【框架】<br>（官方叫 `dependencies.py`） |
| `app/core/errors.py` | 错误体系 | `AppError`、`ErrorCode` 枚举、全局异常处理器 | 具体业务错误文案、SQL | `@app.exception_handler` | `errors.py`（99 行） | 【项目】 |
| `app/core/logging.py` | 日志体系 | `JsonFormatter`、`setup_logging` | 业务日志调用（应在各处 `logger.info`） | 标准库 `logging` | `logging.py`（53 行） | 【项目】 |
| `app/core/response.py` | 响应包装 | `ok()`（分页等真有调用者时再加） | 具体接口的字段定义 | 无（纯工具函数） | `response.py`（15 行） | 【项目】 |
| `app/core/security.py` | 安全原语 | argon2 哈希、cookie 签发、CSRF token | 登录流程编排（属 service） | 无（`argon2-cffi` / `itsdangerous`） | ⚠️ 未创建 | 【项目】 |
| `app/api/router.py` | 路由汇总 | 每个模块一行 `include_router` | 任何路由函数本体 | `APIRouter(prefix="/api")` | `router.py`（15 行） | 【框架】 |
| `app/api/*.py` | 路由层 | 一个业务模块一个文件：取参数、调 service、返回；**该模块的请求/响应模型** | 数据库查询、业务判断、模型调用、超过 30 行的函数 | `APIRouter`、`response_model`、`Depends` | `api/health.py`（35 行） | 【框架】 |
| `app/models/` | 表定义 | SQLModel 类：字段、约束、关系、`__tablename__` | 任何 `select()` / `session.exec()`、业务方法 | SQLModel / SQLAlchemy 2.0 | ⚠️ 待填（12 张表） | 【项目】 |
| `app/services/` | 业务逻辑 | 一条业务主线的完整实现，可脱离 HTTP 单测 | `from fastapi import Request`、`JSONResponse` | 无（纯 Python，方便 pytest） | ⚠️ 待填 | 【项目】 |
| `app/jobs/` | 定时任务 | 任务入口函数，内部调 service | 业务逻辑（应下沉到 service） | `APScheduler` | ⚠️ 待填（每日扫事件） | 【项目】 |
| `alembic/versions/` | 迁移脚本 | 每个版本一个文件，顶部写清对应文档与回滚影响 | 手工改库留下的临时 SQL | Alembic | ⚠️ 待填（`0001_initial_schema`） | 【工具】 |
| `prompts/` | 提示词 | 带版本号的 prompt 文件（`v1_extract.md`） | prompt 字符串常量散落在代码里 | 无 | ⚠️ 待填 | 【项目】 |
| `tests/` | 测试 | `tests/services/` 单测、`tests/api/` 接口测试 | 依赖真实网络的测试 | `pytest` + `TestClient` | `test_health.py`（40 行） | 【工具】 |

### 11.3.1 请求/响应模型放哪（一个待定项）

ARCHITECTURE 9.2 要求"请求模型和响应模型分开"，但**没说放哪个目录**。本项目的决定：

| 情况 | 放哪 |
|---|---|
| 该模块只有 1-3 个模型 | **直接写在 `api/xxx.py` 顶部**（接口契约跟路由走） |
| 超过 50 行 | 拆到 `api/schemas/xxx.py` |
| 是表的直接映射（增删改查不需要额外字段） | 复用 `models/` 里的 SQLModel |

**不新建 `app/schemas/` 根目录**——模型是接口契约，跟 `api/` 走更近。

### 11.4 六个关键位置（请求怎么流转）

```
HTTP 请求
   │
   ▼
① 请求入口 ────────────── app/main.py
   │                       · access_log 中间件：生成 trace_id、记耗时、打日志
   │                       · CORS 中间件
   ▼
② 路由 ────────────────── app/api/router.py → app/api/xxx.py
   │                       · APIRouter 匹配路径
   │                       · 参数校验（Pydantic 自动，失败 → 400 VALIDATION_ERROR）
   ▼
③ 权限校验 ────────────── app/deps.py（通过 Depends 注入）
   │                       · get_current_user   → 认证，失败 401
   │                       · get_session        → SET LOCAL app.user_id，RLS 生效
   │                       · （角色校验：待 admin 接口出现时再加，见 19.6）
   ▼
④ 业务规则 ────────────── app/services/xxx.py
   │                       · 业务判断、状态流转、调外部 API
   │                       · 不 import fastapi（保证可单测）
   ▼
⑤ 数据库访问 ──────────── app/services/（查询） + app/models/（表定义）
   │                       · session 由 get_session 注入，已带 RLS 变量
   │                       · 查询**不写** where user_id
   ▼
⑥ 错误处理 ────────────── app/core/errors.py
   │                       · service 抛 AppError → 全局 handler
   │                       · Pydantic 抛 ValidationError → 全局 handler
   │                       · 未捕获 → 500，堆栈只进日志
   ▼
返回
   · 成功：{ "data": ..., "meta": ... }
   · 失败：{ "error": { "code": ..., "message": ... } }
```

#### 六个问题，逐条对应到文件和机制

| 问题 | 答案 | 文件 | 机制 |
|---|---|---|---|
| **请求入口在哪？** | HTTP 进来的第一站是 `main.py`，路由匹配在 `api/` | `app/main.py`<br>`app/api/*.py` | 中间件链 + `include_router` |
| **业务规则在哪？** | 全在 `services/`，一行都不写在路由里 | `app/services/` | 无框架机制，纯 Python（这是设计目的） |
| **数据库访问在哪？** | 引擎在 `db.py`，session 在 `deps.py` 注入，**查询在 `services/`** | `app/db.py`<br>`app/deps.py`<br>`app/services/` | SQLAlchemy 引擎 + FastAPI `Depends` + `SET LOCAL` |
| **参数校验在哪？** | 路由函数签名上的 Pydantic 模型，**框架自动执行** | `app/api/*.py`（模型定义在同文件） | Pydantic v2 + FastAPI 自动绑定 |
| **错误处理在哪？** | 四个全局处理器集中在 `core/errors.py`，在 `main.py` 注册 | `app/core/errors.py` | `@app.exception_handler` |
| **权限校验入口在哪？** | 两层已实现（认证 + RLS），第三层待 admin 接口；全部在 `deps.py`，通过路由的 `Depends()` 挂载 | `app/deps.py` | FastAPI `Depends` + PostgreSQL RLS |

#### 三层权限的分工（再强调一次）

| 层 | 谁做 | 失败表现 |
|---|---|---|
| ① 认证 | `get_current_user` | 401 `UNAUTHENTICATED` |
| ② 数据隔离 | `get_session` 的 `SET LOCAL` + **数据库 RLS** | 查不到 → **404**（不是 403） |
| ③ 功能权限 | 待实现（当前无 admin 路由，见 19.6） | 403 `FORBIDDEN` |

---

## 12. 接口响应规则

### 12.1 成功

```json
{ "data": [...], "meta": { "total": 18, "page": 1, "size": 20, "total_pages": 1, "has_next": false } }
```

单条资源可省 `meta`。`204 No Content` 直接空响应（**不能有 body**）。

### 12.2 失败

```json
{ "error": { "code": "PERSON_NOT_FOUND", "message": "找不到这个人", "detail": {} } }
```

### 12.3 四条硬规则

| # | 规则 |
|---|---|
| 1 | `code` **大写下划线，稳定不变**；前端判断分支看 `code`，**不看 `message`** |
| 2 | `message` 是给人看的中文，可以改 |
| 3 | **500 不返回堆栈**，只返回 `INTERNAL_ERROR` |
| 4 | 越权访问返回 **404 而不是 403**——403 等于告诉对方"这个资源存在" |

完整错误码表见 [`07-后端设计.md`](./07-后端设计.md) 第 4.3 节（14 个）。
**完整响应样例（列表/对象/创建/更新/删除/空列表/5 类错误）见 [`10-接口响应规范.md`](./10-接口响应规范.md)。**

---

## 13. 错误处理规则

### 13.1 三层结构

```
① 参数错误    Pydantic 自动抛 → 全局 handler 转成 VALIDATION_ERROR
② 业务错误    service 里 raise AppError(code, message, http_status)
③ 未捕获      全局兜底 → 500 INTERNAL_ERROR + 日志记堆栈
```

### 13.2 五条规则

| # | 规则 |
|---|---|
| 1 | **业务错误一律用 `AppError`**，不裸 `raise HTTPException`（框架层 401/404 例外） |
| 2 | 错误码集中定义在 `core/errors.py`，**业务代码里不写字符串** |
| 3 | 唯一约束冲突要转成 **409**，不能让它变成 500 |
| 4 | 外部服务失败用 **502/504**，和自身 500 区分开 |
| 5 | 错误信息**不放内部细节**（SQL、文件路径、堆栈） |

### 13.3 新增错误码的流程

```
1. 在 core/errors.py 加常量
2. 在 07 第 4.3 节的表里加一行
3. 前端按 code 写分支
```

**不允许**在业务代码里临时写 `code="XXX_ERROR"`。

---

## 14. 日志规则

### 14.1 格式

**JSON lines**，一行一条，便于 grep 和后续接日志系统。

```json
{"ts":"2026-10-28T00:10:00+08:00","level":"INFO","trace_id":"a1b2",
 "method":"POST","path":"/api/ingest/image","status":202,
 "duration_ms":38,"user_id":1,"msg":"upload accepted","count":3}
```

### 14.2 级别

| 级别 | 记什么 | 例子 |
|---|---|---|
| **INFO** | 每个请求一行 | method、path、status、duration_ms、user_id、trace_id |
| **WARNING** | 可恢复的异常 | 模型降级、推送失败、抽取置信度全低、RLS 查询为空 |
| **ERROR** | 未捕获异常 | 带 trace_id + 堆栈（只进日志，不进响应） |

### 14.3 隐私红线（绝对不记）

| 不记 | 为什么 |
|---|---|
| 密码、password_hash | 安全 |
| 完整 cookie / CSRF token | 安全 |
| **聊天原文、OCR 全文** | 这是别人的私事，日志不该留 |
| 图片 base64 | 体积 |
| **`nickname` + 具体事实的组合** | 日志泄露比数据库泄露更难察觉 |

> **日志里只能出现 `person_id`，不能出现人名和他说过的话。** 排查时用 id 去库里查。

### 14.4 trace_id

每个请求生成一个，贯穿全链路（包括异步任务），出问题时能串起来。

---

## 15. 数据库连接与权限校验入口

### 15.1 连接方式

```python
engine = create_async_engine(
    settings.database_url,
    pool_size=5,          # 20 人规模，5 个连接足够
    max_overflow=5,
    pool_pre_ping=True,   # 防连接被中途掐断
    echo=settings.debug,  # 生产必须 false（会把带隐私的 SQL 打进日志）
)
```

| 参数 | 值 | 为什么 |
|---|---|---|
| `pool_size` | 5 | 用户只有 20 人，并发请求个位数 |
| `max_overflow` | 5 | 突发时临时扩到 10 |
| `pool_pre_ping` | True | 云数据库会掐空闲连接，开了才不会突然报错 |
| `echo` | 仅本地 true | 生产开 echo 会把 SQL 全打进日志 |

### 15.2 会话注入（RLS 的唯一入口）

```python
# deps.py
async def get_session(user: User = Depends(get_current_user)) -> AsyncGenerator[Session, None]:
    async with SessionLocal() as session:
        # 这一行是 RLS 的开关，不能省
        await session.execute(text("SET LOCAL app.user_id = :uid"), {"uid": user.id})
        yield session
```

**三条铁律**：

| # | 规则 | 后果 |
|---|---|---|
| 1 | **必须在 session 开始后立刻 `SET LOCAL`** | 忘了 → RLS 拿不到变量 → 查询报错 |
| 2 | **用 `SET LOCAL`，不是 `SET`** | `SET` 是会话级，连接归还池子后**下一个人还在用你的 user_id** |
| 3 | **应用连库用普通角色 `keeper_app`** | 用 `postgres` 超级用户 → RLS 被绕过，且不报任何错 |

> 第 2 条是最隐蔽的坑。`SET LOCAL` 只在当前事务内有效，事务结束自动失效。

### 15.3 权限校验的三个入口

| 入口 | 位置 | 管什么 | 失败返回 |
|---|---|---|---|
| **① 认证** | `deps.get_current_user` | 有没有登录 | 401 |
| **② 数据隔离** | `deps.get_session` 里的 `SET LOCAL` + 数据库 RLS | 能不能看别人的数据 | 查不到 → 404 |
| **③ 功能权限** | 待实现（当前无 admin 路由，按 19.6 不预留） | 能不能用这个功能 | 403 |

**用法**：

```python
# 普通业务路由：声明 get_session 就等于要求登录（它内部依赖 get_current_user）
@router.get("/{pid}")
async def get_person(pid: int, session: Session = Depends(get_session)):
    ...

# 管理员路由：等第一个 admin 接口出现时，再实现 require_owner（见 19.6）
# @router.post("/users")
# async def create_user(..., _: User = Depends(require_owner)):
```

### 15.4 每个请求的执行顺序

```
请求进来
  ↓
① 中间件：解析 cookie → 拿 user_id（拿不到就 401，白名单除外）
  ↓
② 依赖 get_session：开 session → SET LOCAL app.user_id   ← RLS 靠这一步生效
  ↓
③ 依赖校验 CSRF（写操作）
  ↓
④ 路由函数：Pydantic 自动校验参数
  ↓
⑤ service：业务逻辑 + 业务校验
  ↓
⑥ 返回：统一包装
```

### 15.5 不依赖"应用层记得过滤"

所有 `SELECT` 都**不写** `WHERE user_id = ?`——RLS 会加。

**唯一例外**：`users` 表没开 RLS（登录前还不知道 user_id），登录查询要手动按 `username` 过滤。

---

## 16. 新增模块怎么放文件

### 16.1 决策树

```
要加一个新功能，文件放哪？

有新的数据要存吗？
├─ 有 → models/xxx.py 加表 + alembic 迁移 + 改 06 文档
└─ 无 → 跳过

有业务逻辑吗（超过 3 行）？
├─ 有 → services/xxx.py（不 import fastapi，能单测）
└─ 无 → 直接写在路由里（这种情况很少）

要对外暴露 HTTP 接口吗？
├─ 要 → api/xxx.py，挂到 main.py
└─ 不要 → 只被别的 service 调用，不建 api 文件

要定时跑吗？
├─ 要 → jobs/xxx.py，在 lifespan 里注册
└─ 不要 → 跳过
```

### 16.2 十步清单（照着走一遍）

```
□ 1. 先改文档：02（功能）→ 06（表）→ 07（接口），三处都改完再动代码
□ 2. models/ 加表定义（对应 06 的字段表，一个字段都不能少）
□ 3. alembic 加迁移（不直接在库里 ALTER）
□ 4. services/ 写业务逻辑（不 import fastapi，能单测）
□ 5. api/ 写路由（≤30 行，只解析参数、调 service、返回）
□ 6. 路由必须依赖 get_session 或 get_current_user
□ 7. 写操作必须校验 CSRF
□ 8. 错误用 AppError，加新错误码到 core/errors.py + 07 文档
□ 9. tests/services/ 写单测（不用起 HTTP）
□ 10. 前端 pnpm gen:types 同步类型 → 再写页面
```

### 16.3 例子：加一个"送礼推荐"模块

```
1. 文档
   02 加功能「送礼推荐」
   06 如果要把推荐结果存下来 → 加表；每次现算 → 不加表
   07 加接口 POST /api/persons/{id}/gift-suggestion

2. models/
   现算就不需要新表

3. services/gift.py
   def suggest_gifts(person_id: int, budget: tuple[Decimal, Decimal]) -> list[GiftSuggestion]
   —— 内部调档案、记忆层、知识层

4. api/persons.py 加一个路由（复用现有 router，不新建文件）

5. tests/services/test_gift.py
   给一个假的 person，断言返回 2-3 个建议，且每个都带 reason

6. 前端 pnpm gen:types → 写组件
```

### 16.4 五条禁令

| 禁止 | 因为 |
|---|---|
| 在路由里写业务逻辑 | 无法单测，逻辑会散 |
| 在 service 里 `from fastapi import Request` | 绑死 HTTP，测试要起服务 |
| 直接 `ALTER TABLE` 改线上库 | 第二天没人知道线上结构 |
| 在业务代码里硬编码错误字符串 | 前端判断不到，改一处漏一处 |
| 新建表不加 `user_id` | RLS 保护不了它，必然串数据 |

### 16.5 加一张表的检查项

```
□ user_id NOT NULL ？（业务表必须）
□ 外键写了 ON DELETE 行为？（CASCADE 还是 SET NULL，必须显式）
□ 开了 RLS 并写了策略？（USING + WITH CHECK 都要）
□ 外键和查询条件建了索引？
□ 金额用 numeric(12,2) 且永远正数？
□ 状态用 text + CHECK，不用 DB ENUM？
□ 能解释每个字段为什么存在？（解释不了就删掉）
```

---

## 17. 安全与隔离（不可绕过）

| # | 规则 |
|---|---|
| 1 | 数据隔离靠 **RLS**，不靠应用层 `where user_id` |
| 2 | 每个请求必须 `SET LOCAL app.user_id`，在 `deps.py` 统一做 |
| 3 | 应用连库用**普通角色 `keeper_app`**，不能用 `postgres`（会绕过 RLS） |
| 4 | 密码用 **argon2id**，登录失败 5 次锁 15 分钟 |
| 5 | 会话用 httpOnly cookie，不用 JWT 存 localStorage |
| 6 | 文件按 `uploads/{user_id}/` 分目录 |
| 7 | 删除一个人 = 真删 + 级联 + 删文件（用户要求"不留痕"） |

---

## 18. 性能与复杂度（不要过早优化）

| 原则 | 说明 |
|---|---|
| **V1 不做缓存** | 18 个人数据，每次查库毫秒级 |
| **不做分页游标** | offset 分页够用 |
| **不上消息队列** | 上传用 BackgroundTasks，定时用 APScheduler |
| **不建全文索引** | `ILIKE` 够用 |
| **不为性能反范式** | 除非在文档里写明理由（`06` 第 7 节有 6 条，都给了原因） |

> **判断标准**：如果优化后用户感知不到差别，就不要优化。
> **这个项目最难的不是性能，是你三个月后还能看懂自己的代码。**

---

## 19. 技术选型复核（防过度设计）

### 19.1 复核方法

面对任何一个技术选择，问三个问题：

1. 它解决的是**真问题**，还是**想象的问题**？
2. 有没有**更轻**的方案能解决同一个问题？
3. 如果用更轻的方案，**代价**是什么？

**判断标准**：如果答不出"不用它会具体损失什么"，那就是过度选型。

### 19.2 逐项结论

| 组件 | 是否过度 | 依据 | 结论 |
|---|---|---|---|
| **Python + FastAPI** | ❌ 不过度 | 选它不是因为性能，而是 Pydantic 校验 + 自动 OpenAPI | 保留 |
| **SQLModel** | ❌ 不过度 | 比纯 SQLAlchemy 少写代码，是"减"不是"加" | 保留 |
| **PostgreSQL** | ⚠️ 偏重，但理由成立 | 纯数据量 SQLite 够。**但 RLS 是 SQLite 给不了的** | 保留，**理由是隔离，不是性能** |
| **pgvector** | ⚠️ **现在用不上** | V2 的 RAG 才需要 | 建议推迟，见 19.3 |
| **APScheduler** | ❌ 不过度 | 比 Celery 轻一个数量级 | 保留 |
| **Alembic** | ❌ 不过度 | "改表先改文档再改迁移"的铁律需要它 | 保留 |
| **Docker Compose** | ⚠️ 偏重 | 单机项目可以 systemd 跑 | 保留（环境一致 + 迁移方便） |
| **Caddy** | ❌ 不过度 | 配置量是 Nginx 的 1/10 | 保留 |
| **Vue 3 + TypeScript** | ❌ 不过度 | 产品负责人指定；学习成本靠类型生成抵消 | 保留 |

### 19.3 三处可以减负（建议）

| 项 | 现状 | 建议 | 收益 |
|---|---|---|---|
| `vector_chunks` 表 | W1 就建 | **推迟到 V2** | W1 建表从 15 张降到 12 张 |
| `temperature_snapshots` 表 | W1 就建 | **推迟到 V2** | 同上 |
| `agent_runs` 表 | 已标注 V2 | 保持 | — |

**理由**：这三张表 V1 一行数据都不会写。

### 19.4 真正的风险不是"过度"，是"组件多"

这套栈单个组件都不高级，但**要同时跑起来的东西有 5 个**：

```
前端构建产物 · 后端进程 · 数据库 · 反向代理 · 容器编排
```

**一个人维护 5 个组件，出问题时定位成本是指数的。**

三条对策：一条命令起全栈 / 一个 `/api/health` 查全部 / 日志集中看。

### 19.5 结论

- **没有为了"高级感"选任何东西**——全是主流方案
- **没有为了"性能感"选任何东西**——FastAPI 的性能用不上，Postgres 不是因为它快
- **有一处接近"安全感"，但站得住**：PostgreSQL。理由是 RLS，不是"企业级"

### 19.6 最小必要封装原则（硬约束）

> **一句话：能用框架的，绝不自己写；要自己写的，必须说清理由且当前就在用。**
>
> **后端架构要稳，不是要复杂。**

#### 19.6.1 三条判定

新增任何封装前，依次回答：

| # | 问题 | 答不上来就别写 |
|---|---|---|
| 1 | **框架有现成机制吗？** | 有 → **必须用**，不许自己包一层 |
| 2 | **这个封装解决什么问题？** | 说不清 → **不写** |
| 3 | **当前代码有调用者吗？** | 只有"以后可能用" → **不写，等用到再加** |

**三条全过，才允许写。**

#### 19.6.2 七类能力必须优先复用框架

| 能力 | 必须用的框架机制 | 禁止 |
|---|---|---|
| **HTTP 状态码** | 标准码：`200/201/204/400/401/403/404/409/413/429/500/502/504` | 自造状态码、用 200 包错误 |
| **参数校验** | Pydantic `Field` / `Query` / `Path` 约束 | 手写 `if not x: raise` |
| **错误处理** | `@app.exception_handler` 全局注册 | 路由内 `try/except` |
| **路由分组** | `APIRouter` + `include_router` | 手动维护路由表 |
| **中间件** | `@app.middleware("http")` / `CORSMiddleware` | 手写 header 处理 |
| **日志** | 标准 `logging` + `extra={}` | `print()` |
| **依赖注入** | `Depends` | 全局变量 / 自定义装饰器 |

#### 19.6.3 自定义封装的四条准入门槛

同时满足才允许新增：

- [ ] 框架**没有**等价机制（或等价机制明显不够用）
- [ ] 能**一句话说清**它解决什么问题
- [ ] **当前代码有调用者**（不是"以后会用"）
- [ ] 已写进本文对应章节

**缺一条就不加。**

#### 19.6.4 "骨架机制"与"功能封装"的区别

这是唯一的例外条款，必须写清楚，否则会走两个极端：要么什么都预留，要么把地基也删了。

| | **骨架机制** | **功能封装** |
|---|---|---|
| 定义 | **每个业务路由都必须经过** | 某个功能专用 |
| 例子 | 认证入口、RLS 会话开关 | 分页、角色校验、raw session |
| 零调用时 | **保留**——删了架构不成立 | **删除**——等有调用者再加 |
| 当前保留 | `get_current_user`、`get_session` | 全部删除 |

#### 19.6.5 已经因此被删掉的（记录在案）

| 封装 | 删因 | 何时再加 |
|---|---|---|
| `get_raw_session` | 零调用；注释说给健康检查用，实际 health 走 `engine.connect()` | W2 写登录时 |
| `get_pagination` + `Pagination` | 零调用 | 第一个列表接口 |
| `paginated()` | 零调用 | 第一个列表接口 |
| `require_owner` | 零调用（还没有 admin 路由） | 第一个 admin 接口 |

**加回来都是 5-10 行的事**，但那一刻它会有真实调用者，能被验证。

#### 19.6.6 反例（这三种都算违规）

| 反例 | 为什么违规 |
|---|---|
| "以后肯定要用，先写上" | 违反第 3 条：没有当前调用者 |
| "封装一下更优雅" | 违反第 2 条：说不出具体解决什么问题 |
| "框架那个写法和我们习惯不一样" | 违反第 1 条：框架有机制就必须用 |

---

# 第三部分 · 文档地图

## 20. 八份文档各自管什么

| 文档 | 管什么 | 什么时候读 |
|---|---|---|
| **本文（ARCHITECTURE）** | 业务边界 + 工程规范 + 框架用法 | **接手项目先读** |
| [`01-立项说明`](./01-立项说明.md) | 为什么做、给谁做、爆点与边界 | 答辩、介绍项目 |
| [`02-产品设计`](./02-产品设计.md) | 界面、流程、功能清单、版本范围 | 写页面前 |
| [`03-数据与技术`](./03-数据与技术.md) | AI 链路、温度模型、可解释性 | 写 AI 相关逻辑前 |
| [`05-产品形态与技术栈`](./05-产品形态与技术栈.md) | 唯一技术方案、选型理由 | 开工前、换栈时 |
| [`06-数据库设计`](./06-数据库设计.md) | 15 张表的字段、约束、索引、范式校验 | **改表前必读** |
| [`07-后端设计`](./07-后端设计.md) | 45 个 API、认证、校验、异常、降级 | **写后端前必读** |
| [`04-计划与验收`](./04-计划与验收.md) | 里程碑、四层验收、止损信号 | 每周对照 |

**补充材料**：`docs/design/` 是五版界面样张（v4 定稿前的探索），`.impeccable.md` 是设计上下文。

## 21. 文档优先级（冲突时听谁的）

```
06 数据库设计  >  07 后端设计  >  05 技术栈  >  02 产品设计  >  01 立项说明
      ↑                                                          ↑
   表结构最硬                                              价值判断最软
```

**规则**：下层和上层冲突时，以下层为准，然后**回头把上层改对**。

## 22. 变更纪律

| 变更类型 | 必须做什么 |
|---|---|
| 新增依赖 | 先改本文 7.6 节，写明用途和替代方案 |
| 调整目录 | 先改本文第 11 节，写明原因 |
| 自定义错误码 | 先改 `core/errors.py` + 07 的 4.3 节 |
| 更换框架 | 先改本文第 7 节，写明原因 |

**允许换栈的理由**：安全漏洞无补丁 / 核心依赖停止维护 / 已确认的性能瓶颈。
**不允许的理由**："看起来更先进" / "网上说那个更快" / "想试试新技术"。

---

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-28 | v1.0 | 首版：业务边界 + 工程规则 |
| 2026-10-28 | v2.0 | 补齐四块：① 为什么不选 Django/Flask（逐项对比）；② Python 语言规范 10 节（类型/命名/异常/异步/禁止清单）；③ FastAPI 最佳实践 5 组 + **框架最大化利用原则 22 行对照表**；④ 数据库连接方式与权限三入口（含 `SET LOCAL` vs `SET` 的坑）。同时修正 v1.0 中一处误植文本 |
| 2026-10-28 | v2.1 | 11.3 从"职责边界"升级为**目录责任表**（15 行 × 7 列：职责/该放/禁放/框架机制/示例/来源）；新增 11.3.1 请求响应模型放哪的决策、11.4 六个关键位置与请求流转图 |
