"""骨架冒烟测试：健康检查、统一错误格式、trace_id。

这里不测业务——业务还没写。等 W2 有了 auth / persons 再加。
"""

from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_unified_envelope() -> None:
    """健康检查：即使数据库没起也要有响应，且结构必须是 {data: {...}}。"""
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code in (200, 503)
    body = response.json()
    assert "data" in body
    assert body["data"]["app"] == "Keeper"
    assert body["data"]["checks"]["database"]["status"] in ("ok", "down")


def test_unknown_path_returns_unified_error() -> None:
    """404 也必须走统一错误格式，不能是框架默认的 {"detail": "..."}。"""
    with TestClient(app) as client:
        response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert isinstance(body["error"]["message"], str)


def test_trace_id_header_present() -> None:
    """每个响应都带 X-Trace-Id，出问题时能串起日志。"""
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert "x-trace-id" in {k.lower() for k in response.headers}
    assert len(response.headers["X-Trace-Id"]) == 12
