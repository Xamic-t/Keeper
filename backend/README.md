# backend · Keeper

Keeper 的后端。Python 3.13 + FastAPI + SQLModel + PostgreSQL 17。

> 规则都在 [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)。写代码前先读第 8-16 节。

---

## 目录

```
backend/
├── app/
│   ├── main.py            # 入口：lifespan、CORS、日志中间件、挂路由
│   ├── config.py          # 配置（pydantic-settings 读项目根 .env）
│   ├── db.py              # 引擎、SessionLocal、健康探测
│   ├── deps.py            # 依赖：当前用户 / session / 分页 / 管理员
│   ├── core/
│   │   ├── errors.py      # AppError + 14 个错误码 + 4 个异常处理器
│   │   ├── logging.py     # JSON lines 日志 + trace_id
│   │   └── response.py    # 统一响应包装 ok() / paginated()
│   ├── api/
│   │   ├── router.py      # 路由汇总（新模块只在这里挂一行）
│   │   └── health.py      # 健康检查（唯一示范路由）
│   ├── models/            # SQLModel 表定义（待填）
│   ├── services/          # 业务逻辑（待填，不 import fastapi）
│   └── jobs/              # 定时任务（待填）
├── alembic/               # 迁移
├── tests/
│   └── test_health.py
├── pyproject.toml
└── README.md
```

**分层纪律**（ARCHITECTURE 11.2）：

| 层 | 只做 | 违规判断 |
|---|---|---|
| `api/` | 取参数、调 service、返回 | 函数超 30 行 |
| `services/` | 业务逻辑、查库、调外部 API | 出现 `from fastapi import Request` |
| `models/` | 定义表 | 出现 `select()` |

---

## 启动

### 前置

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL 17（或先不启，健康检查会显示 `down`）

### 步骤

```bash
# 1. 装依赖
cd backend
uv sync

# 2. 配环境变量（项目根目录）
cp ../.env.example ../.env
# 至少改 DB_PASSWORD 和 SECRET_KEY

# 3. 起数据库（可选，不起也能跑，只是 /api/health 显示 degraded）
docker compose up -d db

# 4. 启动
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

访问：

| 地址 | 说明 |
|---|---|
| `http://localhost:8000/api/health` | 健康检查 |
| `http://localhost:8000/docs` | Swagger（仅 dev 环境开） |

### 手机真机调试

同一 WiFi 下访问 `http://你的电脑IP:8000/api/health`。

---

## 跑测试

```bash
cd backend
uv run pytest -v
```

骨架阶段只有三个用例：健康检查结构、404 统一格式、trace_id。

---

## 代码规范

```bash
uv run ruff format .   # 格式化
uv run ruff check .    # 检查
```

`ruff` 已配置强制类型注解（`ANN` 规则），所有函数签名必须标注参数和返回值。

---

## 新增模块怎么加

按 ARCHITECTURE 16.2 的十步走。简化版：

```
1. 改文档：docs/02 → docs/06 → docs/07
2. models/xxx.py          加表
3. alembic 迁移
4. services/xxx.py        业务逻辑（不 import fastapi）
5. api/xxx.py             路由（≤30 行）
6. api/router.py          挂一行
7. tests/services/        单测
8. 前端 pnpm gen:types
```

**路由模板**：

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.core.response import ok

router = APIRouter(prefix="/api/persons", tags=["persons"])


@router.get("")
async def list_persons(session: AsyncSession = Depends(get_session)) -> dict:
    # session 已经带好 RLS 变量，查询不用写 where user_id
    return ok({"items": []})
```

---

## 已实现 / 未实现

| 项 | 状态 |
|---|---|
| 应用入口 + lifespan | ✅ |
| 配置（pydantic-settings） | ✅ |
| 路由分组 | ✅ |
| 健康检查（含数据库探测） | ✅ |
| 数据库连接（连接池） | ✅ |
| 统一响应 `ok()` / `paginated()` | ✅ |
| 统一错误处理（4 个 handler） | ✅ |
| JSON 日志 + trace_id | ✅ |
| 基础参数校验 | ✅（Pydantic，随模型定义生效） |
| 权限依赖占位（认证/RLS/管理员） | ✅ 骨架，认证未实现 |
| 业务表 / 迁移 | ⬜ W1 |
| 登录认证 | ⬜ W2 |
| 45 个业务接口 | ⬜ W2 起 |
