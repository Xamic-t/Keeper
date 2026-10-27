"""安全原语：argon2 哈希、会话签名、CSRF。

只放"拿起来就能用"的原子操作，不放流程编排——登录流程在 services/auth.py。
对应文档：specs/01-auth.md 5.1

注意：这里绝不记录明文密码、cookie 原文、CSRF token（ARCHITECTURE 14.3）。
"""

import logging
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from itsdangerous import BadSignature, SignatureExpired, TimedSerializer

from app.config import settings

logger = logging.getLogger(__name__)

# argon2id 默认参数（内存 64MB、3 轮），单次约 30ms，登录场景够用
_hasher = PasswordHasher()

# itsdangerous 的 salt：同一个 secret_key 下区分用途，防止会话串和别的签名串互串
_SESSION_SALT = "keeper-session"


def hash_password(password: str) -> str:
    """把明文密码变成 argon2id 哈希。永不返回空串。"""
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """验证明文与哈希是否匹配。

    任何异常都返回 False——哈希串被污染不该让登录接口 500。
    """
    if not password or not hashed:
        return False
    try:
        return _hasher.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        logger.warning("password hash invalid, treat as mismatch")
        return False


def _serializer() -> TimedSerializer:
    """签名器。secret_key 为空属于配置错误，启动阶段就该暴露，这里不兜底。"""
    return TimedSerializer(settings.secret_key.get_secret_value(), salt=_SESSION_SALT)


def create_session_token(user_id: int, max_age: int | None = None) -> str:
    """签发一个含 user_id 的签名串，cookie 里只存这个。

    payload 里带绝对过期时间戳（秒），超过就作废——滑动续期由重新登录实现。
    """
    lifetime = settings.session_max_age if max_age is None else max_age
    payload = {"uid": user_id, "exp": int(time.time()) + lifetime}
    return _serializer().dumps(payload)


def decode_session_token(token: str | None) -> int | None:
    """验签并取出 user_id。

    过期、篡改、格式错、缺字段——全部返回 None，不区分原因（不给攻击者信息）。
    """
    if not token or not isinstance(token, str):
        return None
    try:
        payload = _serializer().loads(token, max_age=settings.session_max_age)
    except SignatureExpired:
        return None
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None

    user_id = payload.get("uid")
    expires_at = payload.get("exp")
    if not isinstance(user_id, int) or not isinstance(expires_at, int):
        return None
    if expires_at < int(time.time()):
        return None
    return user_id


def generate_csrf_token() -> str:
    """生成 CSRF token。写进可读 cookie，前端取出来放进制 X-CSRF-Token。"""
    return secrets.token_urlsafe(32)


def verify_csrf(cookie_token: str | None, header_token: str | None) -> bool:
    """校验请求头与 cookie 里的 CSRF token 是否一致（恒定时间比较）。"""
    if not cookie_token or not header_token:
        return False
    if len(cookie_token) != len(header_token):
        return False
    return secrets.compare_digest(cookie_token, header_token)
