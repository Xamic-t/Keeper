"""API 路由汇总。新增模块只在这里挂一行，main.py 不动。"""

from fastapi import APIRouter

from app.api import (
    admin,
    auth,
    confirmations,
    events,
    export,
    facts,
    health,
    ingest,
    ledger,
    persons,
    reminders,
    settings,
    today,
    todos,
)

api_router = APIRouter(prefix="/api")

# 系统
api_router.include_router(health.router)

# 业务模块（按 07-后端设计 逐个加）
api_router.include_router(auth.router)
api_router.include_router(persons.router)
api_router.include_router(ingest.router)
api_router.include_router(confirmations.router)
api_router.include_router(facts.router)
api_router.include_router(ledger.router)
api_router.include_router(events.router)
api_router.include_router(todos.router)
api_router.include_router(reminders.router)
api_router.include_router(today.router)
api_router.include_router(settings.router)
api_router.include_router(admin.router)
api_router.include_router(export.router)
