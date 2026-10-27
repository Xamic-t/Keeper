"""依赖注入。只放**所有业务路由都必须经过**的骨架机制。

判断一个依赖该不该放这里，问一句：
    它是"每个业务路由都要挂的"，还是"某个功能专用的工具"？
前者留下，后者等到真有接口用了再加（见 ARCHITECTURE 19.6）。

当前四个：
  ① get_current_user —— 认证入口（W2 起是真实现）
  ② get_session      —— 注入 RLS 变量，数据隔离的开关
  ③ require_csrf     —— 写操作防盗链（POST/PATCH/DELETE 挂）
  ④ Pagination       —— 列表接口的查询参数模型（page/size）

get_raw_session 是 W1 按 19.6 删掉、W2 又加回来的：登录和认证时还没有 user_id，
不能走带 RLS 的 get_session。现在有真实调用者了。
"""

import logging
from collections.abc import AsyncGenerator
from typing import Annotated, Literal

from fastapi import Cookie, Depends, Header, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.security import decode_session_token, verify_csrf
from app.db import SessionLocal
from app.models.user import User

logger = logging.getLogger(__name__)


class CurrentUser(BaseModel):
    """当前登录用户。请求级只读快照，不持有 session。"""

    id: int
    username: str
    display_name: str | None = None
    role: Literal["owner", "member"] = "member"


class Pagination(BaseModel):
    """列表接口的 page/size。校验交给 Pydantic，业务层只管用。"""

    page: int = Field(default=1, ge=1, description="第几页，从 1 开始")
    size: int = Field(default=20, ge=1, le=100, description="每页条数，上限 100")

    @property
    def offset(self) -> int:
        """跳过的行数。"""
        return (self.page - 1) * self.size


async def get_raw_session() -> AsyncGenerator[AsyncSession]:
    """系统级 session：不设 RLS 变量。

    只给两处用：
      ① 登录（此时还不知道 user_id）
      ② get_current_user 查 users（users 表本身没开 RLS）
    """
    async with SessionLocal() as session:
        yield session


async def get_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_raw_session)],
) -> CurrentUser:
    """从 httpOnly cookie 解析当前用户。所有业务路由的认证入口。

    失败一律 401 UNAUTHENTICATED，不区分"没带 cookie"和"cookie 过期"。
    """
    user_id = decode_session_token(request.cookies.get("session"))
    if user_id is None:
        raise AppError(
            ErrorCode.UNAUTHENTICATED,
            "请先登录",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise AppError(
            ErrorCode.UNAUTHENTICATED,
            "请先登录",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return CurrentUser(
        id=user.id or 0,
        username=user.username,
        display_name=user.display_name,
        role="owner" if user.role == "owner" else "member",
    )


async def require_csrf(
    csrf_cookie: Annotated[str | None, Cookie(alias="csrf")] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    """写操作的 CSRF 校验。GET/HEAD 的路由不挂这个依赖。"""
    if not verify_csrf(csrf_cookie, csrf_header):
        raise AppError(
            ErrorCode.FORBIDDEN,
            "CSRF 校验失败，请刷新页面后重试",
            status_code=status.HTTP_403_FORBIDDEN,
        )


async def get_session(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> AsyncGenerator[AsyncSession]:
    """业务 session，注入 RLS 变量。

    这里只往 session.info 里放 user_id，真正下发 SQL 的是 db.py 的 after_begin 事件——
    因为 `set_config(..., is_local=true)` 只在当前事务有效，而 service 里会 commit，
    commit 之后变量就没了。挂在事件上可以保证**每次新事务都自动补一次**，
    调用方不用记得"提交后不能再查库"这种隐性规矩。

    为什么不用 `SET LOCAL app.user_id = :uid`：Postgres 的 SET 不接受绑定参数，
    会报 syntax error at or near "$1"。set_config(key, value, true) 是参数化等价物。
    """
    async with SessionLocal() as session:
        session.info["rls_user_id"] = user.id
        yield session
