"""错误处理回归测试：W1 遗留的 P2/P3/P4 三条。

这里测的是"坏路径"，正常路径在 test_auth.py 和 tests/services/ 里。
"""

import httpx
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError

from app.core.errors import register_exception_handlers
from app.main import app
from tests.conftest import Account, login


def _app_that_raises() -> FastAPI:
    """只挂一个必然失败的接口，用来验证 handler 本身。"""
    test_app = FastAPI()
    register_exception_handlers(test_app)

    @test_app.get("/conflict")
    async def _raise_integrity() -> None:
        raise IntegrityError("INSERT ...", {}, Exception("duplicate key value"))

    return test_app


async def test_integrity_error_becomes_409_not_500() -> None:
    """唯一约束冲突要能识别出来，否则前端只会看到 500。"""
    transport = httpx.ASGITransport(app=_app_that_raises())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.get("/conflict")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


async def test_unexpected_error_carries_trace_id(single_user: Account) -> None:
    """500 必须带 trace_id，且头和体是同一串——用户报错时能直接捞日志。

    触发方式：person_id 超出 bigint 范围，数据库直接报错（真 500，不是造的）。
    raise_app_exceptions=False：Starlette 兜底中间件发完 500 后会再抛一次异常
    （那是给服务器记账用的，真实服务器会吞掉），测试客户端要显式关掉重抛。
    """
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        await login(http, single_user)
        response = await http.get("/api/persons/99999999999999999999")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert error["trace_id"] == response.headers["X-Trace-Id"]
    # 堆栈只进日志，不进响应
    assert "Traceback" not in response.text
