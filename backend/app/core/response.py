"""统一响应格式。成功统一 {data, meta?}；失败见 core/errors.py。

分页响应（meta.total / page / size）等第一个列表接口出现时再加，
现在不预留——见 ARCHITECTURE 19.6。
"""

from typing import Any


def ok(data: Any, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """成功响应体。"""
    payload: dict[str, Any] = {"data": data}
    if meta is not None:
        payload["meta"] = meta
    return payload
