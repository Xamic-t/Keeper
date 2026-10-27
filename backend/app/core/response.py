"""统一响应格式。成功统一 {data, meta?}；失败见 core/errors.py。

两个东西各管一件事：
  · ok() / page_meta() —— 运行时拼响应体（路由最后一步调）
  · DataResponse / PageResponse —— 声明式契约（路由 response_model 用）

为什么要 DataResponse：W1 遗留 P5「全项目 response_model 零使用」，
而统一信封是 {data: ...}，不能直接拿业务模型当 response_model，
所以需要一个泛型信封。它没有替代品，且现在每个业务路由都在用（19.6 三条全过）。
"""

from math import ceil
from typing import Any

from pydantic import BaseModel, Field


class DataResponse[T](BaseModel):
    """单条资源的成功响应。"""

    data: T


class PageMeta(BaseModel):
    """列表分页信息。字段名与 docs/10 第 3.1 节一致，前端直接可用。"""

    total: int = Field(description="满足条件的总条数")
    page: int = Field(description="当前页，从 1 开始")
    size: int = Field(description="每页条数")
    total_pages: int = Field(description="总页数，空结果为 0")
    has_next: bool = Field(description="是否还有下一页")


class PageResponse[T](BaseModel):
    """列表的成功响应。"""

    data: list[T]
    meta: PageMeta


def ok(data: Any, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """成功响应体。"""
    payload: dict[str, Any] = {"data": data}
    if meta is not None:
        payload["meta"] = meta
    return payload


def page_meta(total: int, page: int, size: int) -> dict[str, Any]:
    """算分页信息。除不尽向上取整——18 条 / 20 一页 → 1 页。"""
    total_pages = ceil(total / size) if size > 0 else 0
    return {
        "total": total,
        "page": page,
        "size": size,
        "total_pages": total_pages,
        "has_next": page < total_pages,
    }
