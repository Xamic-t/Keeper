"""骨架冒烟测试：健康检查、统一错误格式、trace_id。

全部走同一个事件循环里的 ASGI 客户端 —— app.db 的引擎是模块级单例，
混用 TestClient（自带一个循环）会让连接跨循环复用而出错。
"""

import httpx


async def test_health_returns_unified_envelope(client: httpx.AsyncClient) -> None:
    """健康检查：即使数据库没起也要有响应，且结构必须是 {data: {...}}。"""
    response = await client.get("/api/health")

    assert response.status_code in (200, 503)
    body = response.json()
    assert "data" in body
    assert body["data"]["app"] == "Keeper"
    assert body["data"]["checks"]["database"]["status"] in ("ok", "down")


async def test_unknown_path_returns_unified_error(client: httpx.AsyncClient) -> None:
    """404 也必须走统一错误格式，不能是框架默认的 {"detail": "..."}。"""
    response = await client.get("/api/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert isinstance(body["error"]["message"], str)


async def test_trace_id_header_present(client: httpx.AsyncClient) -> None:
    """每个响应都带 X-Trace-Id，出问题时能串起日志。"""
    response = await client.get("/api/health")

    assert "x-trace-id" in {key.lower() for key in response.headers}
    assert len(response.headers["X-Trace-Id"]) == 12


async def test_method_not_allowed_uses_unified_error(client: httpx.AsyncClient) -> None:
    """框架层 405 也要有业务码，不能凭空造一个字符串。"""
    response = await client.delete("/api/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "HTTP_ERROR"
