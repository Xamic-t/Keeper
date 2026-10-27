"""依赖注入。所有可复用的请求级对象都在这里定义，路由不重复写。

三个权限入口：
  ① get_current_user  —— 认证（未实现，W2 补）
  ② get_session       —— 注入 RLS 变量，做数据隔离
  ③ require_owner     —— 功能权限（管理员）
"""

import logging
from collections.abc import AsyncGenerator
from typing import Annotated, Literal

from fastapi import Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db import SessionLocal

logger = logging.getLogger(__name__)


class CurrentUser(BaseModel):
    """当前登录用户。认证实现后从 cookie 解析出来。"""

    id: int
    username: str
    role: Literal["owner", "member"] = "member"


class Pagination(BaseModel):
    """分页参数。"""

    page: int = 1
    size: int = 20

    @property
    def offset(self) -> int:
        """SQL OFFSET 值。"""
        return (self.page - 1) * self.size


async def get_current_user() -> CurrentUser:
    """【占位】解析当前用户。

    正式实现（W2）：
      1. 从 httpOnly cookie 取签名串，用 itsdangerous 验签
      2. 查 users 表确认 is_active
      3. 返回 CurrentUser

    现在直接抛 401——认证没做完，不让任何业务接口假装可用。
    """
    raise AppError(
        ErrorCode.UNAUTHENTICATED,
        "请先登录",
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


async def require_owner(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    """管理员专用。用在 /api/admin/* 的路由上。"""
    if user.role != "owner":
        raise AppError(
            ErrorCode.FORBIDDEN,
            "没有权限",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return user


async def get_raw_session() -> AsyncGenerator[AsyncSession]:
    """系统级 session，**不带 RLS 变量**。

    只允许三处使用：登录、健康检查、定时任务。
    业务路由一律用 get_session。
    """
    async with SessionLocal() as session:
        yield session


async def get_session(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> AsyncGenerator[AsyncSession]:
    """业务 session，注入 RLS 变量。

    这一行 SET LOCAL 是 RLS 的开关，不能省，也不能换成 SET。
      - 忘了写 → RLS 拿不到变量 → 查询直接报错（比静默泄漏好）
      - 用 SET  → 变量活到连接归还，连接池复用时下一个人会用你的 user_id
    """
    async with SessionLocal() as session:
        await session.execute(text("SET LOCAL app.user_id = :uid"), {"uid": user.id})
        yield session


def get_pagination(
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Pagination:
    """分页依赖。"""
    return Pagination(page=page, size=size)
