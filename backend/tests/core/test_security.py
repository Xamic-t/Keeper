"""安全原语单测。纯函数，不碰数据库。"""

from app.core.security import (
    create_session_token,
    decode_session_token,
    generate_csrf_token,
    hash_password,
    verify_csrf,
    verify_password,
)


def test_hash_password_produces_argon2id():
    hashed = hash_password("Test-pass-123")

    assert hashed.startswith("$argon2id$")
    assert hashed != "Test-pass-123"


def test_verify_password_matches_only_the_same_password():
    hashed = hash_password("Test-pass-123")

    assert verify_password("Test-pass-123", hashed) is True
    assert verify_password("Test-pass-124", hashed) is False


def test_verify_password_returns_false_for_broken_hash():
    """哈希串被污染不能让登录接口 500。"""
    assert verify_password("whatever", "not-a-hash") is False
    assert verify_password("whatever", "") is False


def test_session_token_roundtrip():
    token = create_session_token(42)

    assert decode_session_token(token) == 42


def test_expired_token_returns_none():
    """max_age 为负 = 签发即过期。"""
    assert decode_session_token(create_session_token(42, max_age=-1)) is None


def test_tampered_and_empty_token_return_none():
    token = create_session_token(42)

    assert decode_session_token(token + "x") is None
    assert decode_session_token("") is None
    assert decode_session_token(None) is None


def test_csrf_token_compare():
    token = generate_csrf_token()

    assert verify_csrf(token, token) is True
    assert verify_csrf(token, token[:-1]) is False
    assert verify_csrf(token, generate_csrf_token()) is False
    assert verify_csrf(None, token) is False
    assert verify_csrf(token, None) is False
