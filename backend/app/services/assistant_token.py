"""给 Harness 侧车的**短期用户令牌**。

为什么需要它：Harness 调主服务的接口时，必须"以当前登录用户的身份"调，
否则它只能用一个全局 token 冒充某个人（以前是 ASSISTANT_USERNAME 指定的 owner），
多用户下就是越权——tester 让助理建待办，结果建到 Admin 名下。

所以每次跑一轮助理，主服务签一个 3 分钟有效、绑定 user_id 的令牌给子进程，
侧车拿它调接口，`assistant_session` 校验签名后按这个 user_id 开 RLS session。

格式：`v1.<user_id>.<exp>.<hmac16>`。签名用 SECRET_KEY，不落库、不用查表。
"""

import hashlib
import hmac
import time

from app.config import settings

# 一轮对话最长多久。给 3 分钟：模型慢 + 工具多跳时留余量，又不会长期有效
DEFAULT_TTL_S = 180
_PREFIX = "v1"


def _sign(payload: str) -> str:
    key = settings.secret_key.get_secret_value().encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:32]


def issue(user_id: int, ttl_s: int = DEFAULT_TTL_S) -> str:
    """签一个绑定 user_id 的短令牌。"""
    expires_at = int(time.time()) + ttl_s
    payload = f"{_PREFIX}.{user_id}.{expires_at}"
    return f"{payload}.{_sign(payload)}"


def verify(token: str) -> int | None:
    """校验并返回 user_id。任何一处不对都返回 None（不区分原因，防探测）。"""
    parts = (token or "").split(".")
    if len(parts) != 4 or parts[0] != _PREFIX:
        return None
    try:
        user_id = int(parts[1])
        expires_at = int(parts[2])
    except ValueError:
        return None
    if expires_at < int(time.time()):
        return None
    payload = f"{parts[0]}.{parts[1]}.{parts[2]}"
    if not hmac.compare_digest(parts[3], _sign(payload)):
        return None
    return user_id
