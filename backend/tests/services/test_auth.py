"""认证 service 单测。不起 HTTP，直接调函数。

覆盖 specs/01-auth.md 第 6 节的 1/2/3/5 条验收。
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.core.errors import AppError, ErrorCode
from app.services.auth import authenticate, change_password
from tests.conftest import TEST_PASSWORD, Account, create_user, delete_users, raw_session


async def test_authenticate_success(single_user: Account) -> None:
    async with raw_session() as session:
        user = await authenticate(session, single_user.username, TEST_PASSWORD)

    assert user.id == single_user.id
    assert user.last_login_at is not None


async def test_authenticate_accepts_email_like_username() -> None:
    """username 允许填邮箱/手机号——不校验格式，也不做任何分隔处理。"""
    email_user = create_user("someone@example.com")
    try:
        async with raw_session() as session:
            user = await authenticate(session, email_user.username, TEST_PASSWORD)
        assert user.id == email_user.id
    finally:
        delete_users(email_user.id)


async def test_wrong_password_and_unknown_user_share_one_error_code(
    single_user: Account,
) -> None:
    """账号不存在和密码错必须同码，否则等于送人一个账号枚举接口。"""
    async with raw_session() as session:
        with pytest.raises(AppError) as wrong_password:
            await authenticate(session, single_user.username, "wrong-password")

    async with raw_session() as session:
        with pytest.raises(AppError) as unknown_user:
            await authenticate(session, "no-such-user", TEST_PASSWORD)

    assert wrong_password.value.code is ErrorCode.BAD_CREDENTIALS
    assert unknown_user.value.code is ErrorCode.BAD_CREDENTIALS
    assert wrong_password.value.status_code == 401


async def test_five_failures_lock_the_account(single_user: Account) -> None:
    """连错 5 次锁定；第 6 次不管密码对不对都返回 ACCOUNT_LOCKED。"""
    for _ in range(5):
        async with raw_session() as session:
            with pytest.raises(AppError):
                await authenticate(session, single_user.username, "wrong-password")

    async with raw_session() as session:
        with pytest.raises(AppError) as locked:
            await authenticate(session, single_user.username, TEST_PASSWORD)

    assert locked.value.code is ErrorCode.ACCOUNT_LOCKED
    assert locked.value.status_code == 401

    # 锁定期内的正确密码也进不去
    async with raw_session() as session:
        remaining = (
            await session.execute(
                text("SELECT locked_until FROM users WHERE id = :id"), {"id": single_user.id}
            )
        ).scalar_one()
    assert remaining > datetime.now(UTC)


async def test_inactive_user_is_rejected_as_bad_credentials(single_user: Account) -> None:
    async with raw_session() as session:
        await session.execute(
            text("UPDATE users SET is_active = false WHERE id = :id"), {"id": single_user.id}
        )
        await session.commit()

    async with raw_session() as session:
        with pytest.raises(AppError) as error:
            await authenticate(session, single_user.username, TEST_PASSWORD)

    assert error.value.code is ErrorCode.BAD_CREDENTIALS


async def test_change_password_rejects_wrong_old_password(single_user: Account) -> None:
    async with raw_session() as session:
        with pytest.raises(AppError) as error:
            await change_password(session, single_user.id, "wrong-old", "New-pass-456")

    assert error.value.code is ErrorCode.BAD_CREDENTIALS
    assert error.value.status_code == 400


async def test_change_password_makes_old_password_invalid(single_user: Account) -> None:
    async with raw_session() as session:
        await change_password(session, single_user.id, TEST_PASSWORD, "New-pass-456")

    async with raw_session() as session:
        with pytest.raises(AppError):
            await authenticate(session, single_user.username, TEST_PASSWORD)

    async with raw_session() as session:
        user = await authenticate(session, single_user.username, "New-pass-456")
    assert user.id == single_user.id
