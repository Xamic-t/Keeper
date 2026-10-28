"""推送：企业微信 webhook → PushPlus 降级。

对应文档：specs/09-reminder.md 5.4

三条规则：
  ① 通道按 priority 从小到大试，前一个失败就换下一个
  ② 全失败只记 WARNING，**不抛异常**——推送挂了不能让整个每日任务挂掉
  ③ 日志里绝不出现 webhook URL / token（隐私红线）
"""

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.notification import NotificationChannel

logger = logging.getLogger(__name__)

WECOM_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class Outgoing:
    """一条待发的消息。markdown 给企微，纯文本给 PushPlus。"""

    title: str
    markdown: str
    text: str


async def list_channels(session: AsyncSession, user_id: int) -> list[NotificationChannel]:
    """用户的通道，按 priority 升序。没配过就返回空列表。"""
    stmt = (
        select(NotificationChannel)
        .where(NotificationChannel.is_active.is_(True))
        .order_by(NotificationChannel.priority, NotificationChannel.id)
    )
    return list((await session.execute(stmt)).scalars().all())


def _fallback_channel(user_id: int) -> NotificationChannel | None:
    """库表为空时退回 .env 配置，保证单人自用时不用先建通道。"""
    if settings.wecom_webhook:
        return NotificationChannel(
            id=0,
            user_id=user_id,
            kind="wecom",
            name="环境变量·企微",
            config={"webhook": settings.wecom_webhook},
            priority=1,
        )
    if settings.pushplus_token:
        return NotificationChannel(
            id=0,
            user_id=user_id,
            kind="pushplus",
            name="环境变量·PushPlus",
            config={"token": settings.pushplus_token.get_secret_value()},
            priority=2,
        )
    return None


async def effective_channels(session: AsyncSession, user_id: int) -> list[NotificationChannel]:
    """实际可用的通道列表：库里的优先，一条都没有才用环境变量。"""
    channels = await list_channels(session, user_id)
    if channels:
        return channels
    fallback = _fallback_channel(user_id)
    return [fallback] if fallback else []


async def _send_wecom(webhook: str, message: Outgoing) -> bool:
    payload: dict[str, Any] = {
        "msgtype": "markdown",
        "markdown": {"content": f"**{message.title}**\n{message.markdown}"},
    }
    async with httpx.AsyncClient(timeout=WECOM_TIMEOUT) as client:
        response = await client.post(webhook, json=payload)
    if response.status_code != 200:
        return False
    body = response.json()
    return bool(body.get("errcode") == 0)


async def _send_pushplus(token: str, message: Outgoing) -> bool:
    payload = {"token": token, "title": message.title, "content": message.text, "template": "txt"}
    async with httpx.AsyncClient(timeout=WECOM_TIMEOUT) as client:
        response = await client.post("https://www.pushplus.plus/send", json=payload)
    if response.status_code != 200:
        return False
    return bool(response.json().get("code") == 200)


async def send_via(channel: NotificationChannel, message: Outgoing) -> bool:
    """按通道类型发一条。任何异常都吞掉，返回 False。"""
    try:
        if channel.kind == "wecom":
            webhook = str(channel.config.get("webhook") or "")
            if not webhook:
                return False
            return await _send_wecom(webhook, message)
        if channel.kind == "pushplus":
            token = str(channel.config.get("token") or "")
            if not token:
                return False
            return await _send_pushplus(token, message)
        logger.warning("unknown channel kind=%s", channel.kind)
        return False
    except Exception as exc:  # noqa: BLE001 — 推送失败不能影响主流程
        logger.warning(
            "push failed channel=%s kind=%s error=%s",
            channel.name,
            channel.kind,
            type(exc).__name__,
        )
        return False


async def push(session: AsyncSession, user_id: int, message: Outgoing) -> str | None:
    """按优先级尝试所有通道，返回成功的通道名；全失败返回 None。"""
    for channel in await effective_channels(session, user_id):
        if await send_via(channel, message):
            logger.info("push ok channel=%s", channel.name)
            return channel.name
        logger.warning("push failed, trying next channel=%s", channel.name)
    return None


def render(message_payload: dict[str, Any], fallback_title: str) -> Outgoing:
    """把提醒 payload 拼成两条消息体（markdown 与纯文本）。"""
    headline = str(message_payload.get("headline") or fallback_title)
    suggestion = str(message_payload.get("suggestion") or "")
    reasons = message_payload.get("reasons") or []
    script = str(message_payload.get("script") or "")

    reason_lines = "\n".join(f"- {reason}" for reason in reasons if reason)
    markdown = f"{suggestion}\n\n**依据**\n{reason_lines or '- （模型没给依据）'}"
    if script:
        markdown += f"\n\n**可以这样发**\n{script}"

    text = f"{headline}\n{suggestion}\n"
    if reason_lines:
        text += f"\n依据：\n{reason_lines}\n"
    if script:
        text += f"\n话术：{script}\n"

    return Outgoing(title=headline, markdown=markdown, text=text)
