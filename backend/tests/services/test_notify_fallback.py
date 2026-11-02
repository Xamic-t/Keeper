"""推送降级链：主通道失败要换下一个，全失败要返回 None，且不许留假记录。

为什么值得单独测：推送是"坏了也不会报错"的那类东西——`push()` 吞掉所有异常，
没有断言的话，降级链断了你也只会发现"怎么一直没收到提醒"。

三个断点：
  ① 企微失败 → PushPlus 顶上（这是唯一让用户还能收到消息的路径）
  ② 两路都失败 → 返回 None，且不抛异常
  ③ 失败时数据库里不许出现"看起来发出去了"的记录
"""

import pytest
from sqlalchemy import func, select

from app.models.notification import NotificationChannel
from app.services import notify
from tests.conftest import Account, rls_session

MESSAGE = notify.Outgoing(title="王姐生日", markdown="还有 3 天", text="还有 3 天")


def _channel(kind: str, name: str, priority: int, config: dict[str, str]) -> NotificationChannel:
    """造一条未入库的通道对象，避免每个用例都去写库。"""
    return NotificationChannel(
        id=priority,
        user_id=0,
        kind=kind,
        name=name,
        priority=priority,
        config=config,
        is_active=True,
    )


@pytest.fixture
def two_channels(monkeypatch: pytest.MonkeyPatch) -> list[NotificationChannel]:
    """企微 priority 1、PushPlus priority 2，符合真实优先级配置。"""
    channels = [
        _channel("wecom", "企微", 1, {"webhook": "https://example.invalid/hook"}),
        _channel("pushplus", "PushPlus", 2, {"token": "fake-token"}),
    ]

    async def _fake_effective(session: object, user_id: int) -> list[NotificationChannel]:
        return channels

    monkeypatch.setattr(notify, "effective_channels", _fake_effective)
    return channels


async def test_falls_back_to_second_channel(
    monkeypatch: pytest.MonkeyPatch, two_channels: list[NotificationChannel], single_user: Account
) -> None:
    """主通道失败，第二个成功 → 返回第二个的名字（用户仍然收到了）。"""
    calls: list[str] = []

    async def _wecom_fail(channel: NotificationChannel, message: notify.Outgoing) -> bool:
        calls.append("wecom")
        return False

    async def _pushplus_ok(channel: NotificationChannel, message: notify.Outgoing) -> bool:
        calls.append("pushplus")
        return True

    async def _send_via(channel: NotificationChannel, message: notify.Outgoing) -> bool:
        return await (_wecom_fail if channel.kind == "wecom" else _pushplus_ok)(channel, message)

    monkeypatch.setattr(notify, "send_via", _send_via)

    async with rls_session(single_user.id) as session:
        result = await notify.push(session, single_user.id, MESSAGE)

    assert result == "PushPlus"
    assert calls == ["wecom", "pushplus"], "必须按 priority 从小到大试，且失败后继续"


async def test_all_channels_failing_returns_none(
    monkeypatch: pytest.MonkeyPatch, two_channels: list[NotificationChannel], single_user: Account
) -> None:
    """两路都失败 → None，且不抛（推送挂了不能带崩每日任务）。"""

    async def _always_false(channel: NotificationChannel, message: notify.Outgoing) -> bool:
        return False

    monkeypatch.setattr(notify, "send_via", _always_false)

    async with rls_session(single_user.id) as session:
        assert await notify.push(session, single_user.id, MESSAGE) is None


async def test_exception_in_send_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, single_user: Account
) -> None:
    """通道抛异常（超时/DNS 挂了）也算失败，不能把异常漏到调用方。"""
    channel = _channel("wecom", "企微", 1, {"webhook": "https://example.invalid/hook"})

    async def _boom(webhook: str, message: notify.Outgoing) -> bool:
        raise TimeoutError("network down")

    monkeypatch.setattr(notify, "_send_wecom", _boom)

    assert await notify.send_via(channel, MESSAGE) is False


async def test_channel_without_secret_is_not_sent(single_user: Account) -> None:
    """通道建了但没填 webhook/token → 直接算失败，不发请求。"""
    empty_wecom = _channel("wecom", "空企微", 1, {})
    empty_pushplus = _channel("pushplus", "空PushPlus", 2, {})

    assert await notify.send_via(empty_wecom, MESSAGE) is False
    assert await notify.send_via(empty_pushplus, MESSAGE) is False


async def test_unknown_kind_is_skipped(single_user: Account) -> None:
    """未知 kind 只记 WARNING，不抛——库里若有人手工塞了脏数据不能炸。"""
    assert await notify.send_via(_channel("sms", "短信", 1, {"token": "x"}), MESSAGE) is False


async def test_failed_push_leaves_no_fake_records(single_user: Account) -> None:
    """推送全失败之后，库里不能多出"已发送"的痕迹。"""
    async with rls_session(single_user.id) as session:
        before = await session.scalar(select(func.count()).select_from(NotificationChannel))

    async with rls_session(single_user.id) as session:
        assert await notify.push(session, single_user.id, MESSAGE) is None

    async with rls_session(single_user.id) as session:
        after = await session.scalar(select(func.count()).select_from(NotificationChannel))

    assert after == before


def test_render_degrades_when_model_gave_no_reasons() -> None:
    """payload 里没 reasons 时，消息体要显式说明"模型没给依据"，不能留空。"""
    message = notify.render({"headline": "王姐生日", "suggestion": "送茶"}, "兜底标题")

    assert "（模型没给依据）" in message.markdown
    assert message.title == "王姐生日"


def test_render_uses_fallback_title_when_payload_empty() -> None:
    """payload 连 headline 都没有时，用兜底标题，不能发出空标题消息。"""
    message = notify.render({}, "今天有件事")

    assert message.title == "今天有件事"
