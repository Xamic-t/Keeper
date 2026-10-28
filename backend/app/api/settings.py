"""设置与推送通道路由。

对应文档：docs/07-后端设计.md 5.1（设置 / 通知通道）

通道的 webhook / token 是敏感信息：**列表接口不返回原文**，只回 kind、name、priority。
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.response import DataResponse, ok
from app.deps import CurrentUser, get_current_user, get_session, require_csrf
from app.models.notification import NotificationChannel
from app.models.user import User
from app.services import notify as notify_service

router = APIRouter(tags=["settings"])


class SettingsOut(BaseModel):
    keep_original_images: bool
    channels: list["ChannelOut"]


class SettingsIn(BaseModel):
    keep_original_images: bool | None = None


class ChannelIn(BaseModel):
    kind: Literal["wecom", "pushplus"]
    name: str = Field(min_length=1, max_length=50)
    webhook: str | None = Field(default=None, max_length=500)
    token: str | None = Field(default=None, max_length=500)
    priority: int = Field(default=100, ge=1, le=999)


class ChannelOut(BaseModel):
    id: int
    kind: str
    name: str
    priority: int
    is_active: bool
    has_secret: bool = Field(description="是否已配置密钥（不回显原文）")


class TestResultOut(BaseModel):
    ok: bool
    channel: str


def _channel_out(channel: NotificationChannel) -> ChannelOut:
    return ChannelOut(
        id=channel.id or 0,
        kind=channel.kind,
        name=channel.name,
        priority=channel.priority,
        is_active=channel.is_active,
        has_secret=bool(channel.config),
    )


@router.get("/settings", response_model=DataResponse[SettingsOut], summary="读设置")
async def get_settings(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """返回用户设置与已配置的推送通道。"""
    row = await session.get(User, user.id)
    channels = await notify_service.list_channels(session, user.id)
    return ok(
        SettingsOut(
            keep_original_images=bool(row.keep_original_images) if row else True,
            channels=[_channel_out(item) for item in channels],
        )
    )


@router.patch("/settings", response_model=DataResponse[SettingsOut], summary="改设置")
async def update_settings(
    payload: SettingsIn,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[None, Depends(require_csrf)] = None,
) -> dict[str, Any]:
    """改设置。目前只有"是否保留原图"这一项。"""
    row = await session.get(User, user.id)
    if row is None:
        raise AppError(ErrorCode.UNAUTHENTICATED, "请先登录", status_code=401)

    if payload.keep_original_images is not None:
        row.keep_original_images = payload.keep_original_images
        session.add(row)
        await session.commit()

    channels = await notify_service.list_channels(session, user.id)
    return ok(
        SettingsOut(
            keep_original_images=row.keep_original_images,
            channels=[_channel_out(item) for item in channels],
        )
    )


@router.get(
    "/notification-channels",
    response_model=DataResponse[list[ChannelOut]],
    summary="通道列表",
)
async def list_channels(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> dict[str, Any]:
    """列出推送通道（不返回密钥原文）。"""
    channels = await notify_service.list_channels(session, user.id)
    return ok([_channel_out(item) for item in channels])


@router.post(
    "/notification-channels",
    response_model=DataResponse[ChannelOut],
    status_code=status.HTTP_201_CREATED,
    summary="新增通道",
    dependencies=[Depends(require_csrf)],
)
async def create_channel(
    payload: ChannelIn,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """新增一条推送通道。webhook / token 存进 config，列表接口不显示。"""
    config: dict[str, Any] = {}
    if payload.kind == "wecom":
        if not payload.webhook:
            raise AppError(ErrorCode.VALIDATION_ERROR, "企微通道要填 webhook", status_code=400)
        config["webhook"] = payload.webhook
    else:
        if not payload.token:
            raise AppError(ErrorCode.VALIDATION_ERROR, "PushPlus 通道要填 token", status_code=400)
        config["token"] = payload.token

    channel = NotificationChannel(
        user_id=user.id,
        kind=payload.kind,
        name=payload.name,
        config=config,
        priority=payload.priority,
        is_active=True,
    )
    session.add(channel)
    await session.commit()
    return ok(_channel_out(channel))


@router.delete(
    "/notification-channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删通道",
    dependencies=[Depends(require_csrf)],
)
async def delete_channel(
    channel_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """删掉一条通道。"""
    channel = await session.get(NotificationChannel, channel_id)
    if channel is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个通道", status_code=404)
    await session.delete(channel)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/notification-channels/{channel_id}/test",
    response_model=DataResponse[TestResultOut],
    summary="测试推送",
    dependencies=[Depends(require_csrf)],
)
async def test_channel(
    channel_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """往这条通道发一条测试消息。失败返回 ok=false，不抛异常。"""
    channel = await session.get(NotificationChannel, channel_id)
    if channel is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个通道", status_code=404)

    message = notify_service.Outgoing(
        title="Keeper 测试推送",
        markdown="这是一条测试消息，收到就说明通道通了。",
        text="这是一条测试消息，收到就说明通道通了。",
    )
    ok_sent = await notify_service.send_via(channel, message)
    return ok(TestResultOut(ok=ok_sent, channel=channel.name))


SettingsOut.model_rebuild()
