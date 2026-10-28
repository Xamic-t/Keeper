"""认证路由：登录 / 登出 / 当前用户 / 改密码。

路由只做三件事：接参数、调 service、包响应。业务判断一律在 services/auth.py。
对应文档：specs/01-auth.md 5.3
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.ratelimit import login_limiter
from app.core.response import DataResponse, ok
from app.core.security import create_session_token, generate_csrf_token
from app.deps import CurrentUser, get_current_user, get_raw_session, get_session, require_csrf
from app.models.user import User
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------- 请求 / 响应模型（接口契约跟路由走，见 ARCHITECTURE 11.3.1） ----------


class LoginRequest(BaseModel):
    """登录入参。username 允许填用户名 / 手机号 / 邮箱，不校验格式。"""

    username: str = Field(min_length=1, max_length=100, description="用户名 / 手机号 / 邮箱")
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    """返回给前端的账号信息。不含任何密码痕迹。"""

    id: int
    username: str
    display_name: str | None = None
    role: str


class LoginData(BaseModel):
    """登录成功的数据体。前端拿 csrf_token 放进后续写请求的头里。"""

    user: UserOut
    csrf_token: str


class MeData(BaseModel):
    """当前用户。字段与登录返回的 user 一致，前端只维护一套。"""

    id: int
    username: str
    display_name: str | None = None
    role: str


class PlainOk(BaseModel):
    """无数据体的成功响应（登出、改密码）。"""

    ok: bool


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128, description="新密码 8-128 位")


def _user_out(user: User) -> UserOut:
    """把库里的 User 转成对外模型。id 不该为空——为空说明数据坏了。"""
    if user.id is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "登录状态异常", status_code=500)
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
    )


# ---------- 路由 ----------


@router.post(
    "/login",
    response_model=DataResponse[LoginData],
    summary="登录",
    status_code=status.HTTP_200_OK,
)
async def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_raw_session)],
) -> dict[str, Any]:
    """校验账号密码，签发 session（httpOnly）+ csrf（可读）两个 cookie。

    限流：每 IP 每分钟最多 N 次（配置项 LOGIN_RATE_LIMIT）。账户本身还有"5 次错锁 15 分钟"，
    两道防线分别挡"同账号爆破"和"批量扫号"。
    """
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = login_limiter.check(client_ip)
    if not allowed:
        raise AppError(
            ErrorCode.RATE_LIMITED,
            "尝试太频繁，请稍后再试",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"retry_after": retry_after},
        )

    user = await auth_service.authenticate(session, payload.username, payload.password)
    login_limiter.reset(client_ip)
    user_out = _user_out(user)
    csrf_token = generate_csrf_token()

    # session：前端读不到，XSS 偷不走
    response.set_cookie(
        key="session",
        value=create_session_token(user_out.id),
        httponly=True,
        secure=not settings.is_dev,
        samesite="lax",
        max_age=settings.session_max_age,
        path="/",
    )
    # csrf：故意可读，前端取出来放进 X-CSRF-Token
    response.set_cookie(
        key="csrf",
        value=csrf_token,
        httponly=False,
        secure=not settings.is_dev,
        samesite="lax",
        max_age=settings.session_max_age,
        path="/",
    )

    return ok(LoginData(user=user_out, csrf_token=csrf_token))


@router.post("/logout", response_model=DataResponse[PlainOk], summary="登出")
async def logout(response: Response) -> dict[str, Any]:
    """清掉两个 cookie。未登录调用也返回成功（幂等）。"""
    response.delete_cookie("session", path="/")
    response.delete_cookie("csrf", path="/")
    return ok(PlainOk(ok=True))


@router.get("/me", response_model=DataResponse[MeData], summary="当前登录用户")
async def me(user: Annotated[CurrentUser, Depends(get_current_user)]) -> dict[str, Any]:
    """前端启动时用它校验登录态。不查库——认证时已经查过了。"""
    return ok(
        MeData(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
        )
    )


@router.post(
    "/password",
    response_model=DataResponse[PlainOk],
    summary="改密码",
    dependencies=[Depends(require_csrf)],
)
async def change_password(
    payload: ChangePasswordRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """验旧密码 → 写新密码。V1 不踢其他设备（没有会话表）。"""
    await auth_service.change_password(
        session,
        user_id=user.id,
        old_password=payload.old_password,
        new_password=payload.new_password,
    )
    return ok(PlainOk(ok=True))
