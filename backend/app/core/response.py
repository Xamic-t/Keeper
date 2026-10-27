"""统一响应格式。成功统一 {data, meta}；失败见 core/errors.py。"""

from typing import Any


def ok(data: Any, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """成功响应体。"""
    payload: dict[str, Any] = {"data": data}
    if meta is not None:
        payload["meta"] = meta
    return payload


def paginated(items: list[Any], total: int, page: int, size: int) -> dict[str, Any]:
    """分页响应体。"""
    return {
        "data": items,
        "meta": {"total": total, "page": page, "size": size},
    }
