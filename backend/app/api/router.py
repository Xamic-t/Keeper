"""API 路由汇总。新增模块只在这里挂一行，main.py 不动。"""

from fastapi import APIRouter

from app.api import health

api_router = APIRouter(prefix="/api")

# 系统
api_router.include_router(health.router)

# 业务模块（按 07-后端设计 逐个加，现在都还没写）
# from app.api import auth, persons, facts, ledger, events, todos, ingest
# api_router.include_router(auth.router)
# api_router.include_router(persons.router)
