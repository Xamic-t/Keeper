"""用户级助理令牌：签、验、过期、篡改。

为什么单独测：这是 Harness 能不能"以当前用户身份"调接口的唯一凭据。
签错一个字节，就会出现"tester 让助理建待办，建到 Admin 名下"这种越权，
而且不会报错——只会悄悄写错人的数据。
"""

import time

import pytest

from app.services import assistant_token


def test_roundtrip_returns_user_id() -> None:
    token = assistant_token.issue(42)
    assert assistant_token.verify(token) == 42


def test_default_ttl_is_short() -> None:
    """默认 3 分钟：够一轮对话，不够被长期复用。"""
    assert assistant_token.DEFAULT_TTL_S == 180


def test_expired_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    token = assistant_token.issue(7, ttl_s=1)
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 10)
    assert assistant_token.verify(token) is None


def test_tampered_user_id_is_rejected() -> None:
    """改中间的 user_id，签名对不上 → 拒绝（这是最该防的一种）。"""
    token = assistant_token.issue(1)
    parts = token.split(".")
    parts[1] = "2"
    assert assistant_token.verify(".".join(parts)) is None


def test_tampered_signature_is_rejected() -> None:
    token = assistant_token.issue(1)
    assert assistant_token.verify(token[:-1] + ("0" if token[-1] != "0" else "1")) is None


def test_garbage_is_rejected_not_raised() -> None:
    for bad in ["", "abc", "v1.1", "v1.x.1.y", "v2.1.9999999999.sig", None]:
        assert assistant_token.verify(bad or "") is None
