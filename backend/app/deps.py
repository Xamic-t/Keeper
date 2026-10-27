"""依赖注入。只放**所有业务路由都必须经过**的骨架机制。

判断一个依赖该不该放这里，问一句：
    它是"每个业务路由都要挂的"，还是"某个功能专用的工具"？
前者留下，后者等到真有接口用了再加（见 ARCHITECTURE 19.6）。

当前只有两个骨架：
  ① get_current_user —— 认证入口
  ② get_session      —— 注入 RLS 变量，数据隔离的开关
"""

import logging
from collections.abc import AsyncGenerator
from typing import Annotated, Literal

from fastapi import Depends, status
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


async def get_session(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> AsyncGenerator[AsyncSession]:
    """业务 session，注入 RLS 变量。

    这一行 SET LOCAL 是 RLS 的开关，不能省，也不能换成 SET：
      - 忘了写 → RLS 拿不到变量 → 查询直接报错（比静默泄漏好）
      - 用 SET  → 变量活到连接归还，连接池复用时下一个人会用你的 user_id
    """
    async with SessionLocal() as session:
        await session.execute(text("SET LOCAL app.user_id = :uid"), {"uid": user.id})
        yield session
