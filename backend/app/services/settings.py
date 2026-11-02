"""设置与推送通道的业务规则。API 层只取参数、调这里。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.notification import NotificationChannel
from app.models.user import User
from app.services import notify as notify_service


async def list_channels(session: AsyncSession, user_id: int) -> list[NotificationChannel]:
    """列出账号的推送通道。"""
    return await notify_service.list_channels(session, user_id)


async def load(session: AsyncSession, user_id: int) -> tuple[bool, list[NotificationChannel]]:
    """返回（是否保留原图, 通道列表）。"""
    user = await session.get(User, user_id)
    if user is None:
        raise AppError(ErrorCode.UNAUTHENTICATED, "请先登录", status_code=401)
    channels = await notify_service.list_channels(session, user_id)
    return bool(user.keep_original_images), channels


async def update_keep_images(
    session: AsyncSession, user_id: int, value: bool | None
) -> tuple[bool, list[NotificationChannel]]:
    """改“保留原图”开关；None 表示不改。"""
    user = await session.get(User, user_id)
    if user is None:
        raise AppError(ErrorCode.UNAUTHENTICATED, "请先登录", status_code=401)
    if value is not None:
        user.keep_original_images = value
        session.add(user)
        await session.commit()
    channels = await notify_service.list_channels(session, user_id)
    return bool(user.keep_original_images), channels


async def create_channel(
    session: AsyncSession,
    user_id: int,
    *,
    kind: str,
    name: str,
    priority: int,
    webhook: str | None,
    token: str | None,
) -> NotificationChannel:
    """新增通道；密钥存 config，列表接口不回显。"""
    config: dict[str, Any] = {}
    if kind == "wecom":
        if not webhook:
            raise AppError(ErrorCode.VALIDATION_ERROR, "企微通道要填 webhook", status_code=400)
        config["webhook"] = webhook
    else:
        if not token:
            raise AppError(ErrorCode.VALIDATION_ERROR, "PushPlus 通道要填 token", status_code=400)
        config["token"] = token

    channel = NotificationChannel(
        user_id=user_id,
        kind=kind,
        name=name,
        config=config,
        priority=priority,
        is_active=True,
    )
    session.add(channel)
    await session.commit()
    return channel


async def delete_channel(session: AsyncSession, channel_id: int) -> None:
    """删掉一条通道。"""
    channel = await session.get(NotificationChannel, channel_id)
    if channel is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个通道", status_code=404)
    await session.delete(channel)
    await session.commit()


async def test_channel(session: AsyncSession, channel_id: int) -> tuple[bool, str]:
    """发一条测试消息，返回（是否成功, 通道名）。失败不抛异常。"""
    channel = await session.get(NotificationChannel, channel_id)
    if channel is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到这个通道", status_code=404)

    message = notify_service.Outgoing(
        title="Keeper 测试推送",
        markdown="这是一条测试消息，收到就说明通道通了。",
        text="这是一条测试消息，收到就说明通道通了。",
    )
    sent = await notify_service.send_via(channel, message)
    return sent, channel.name
