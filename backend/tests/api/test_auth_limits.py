"""登录的两道防线：账号锁定（15 分钟）与 IP 限流（429）。

`test_auth.py` 已经钉住了"5 次错就锁"，但没钉住另外三件同样要紧的事：
  ① 锁的是 **15 分钟**，不是 5 分钟也不是永久
  ② 锁定到期后能自己恢复（否则用户只能等你手工改库）
  ③ IP 那道闸门返回的是 429 + 重试秒数，而不是 401

两条防线挡的不是同一件事：账号锁定挡"盯着一个人爆破"，
IP 限流挡"一次扫一堆账号"。少一条，另一条都容易被绕。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, text

from app.config import settings
from app.core.ratelimit import login_limiter
from app.services.auth import LOCK_MINUTES, MAX_FAILED_ATTEMPTS
from tests.conftest import TEST_PASSWORD, Account, login

# users 表没开 RLS，可以直接用普通同步连接查看锁定状态。
# 与 conftest 里建账号用的是同一种连接方式（psycopg 同步驱动）。
_engine = create_engine(settings.database_url, pool_pre_ping=True)


def _user_row(user_id: int) -> tuple[datetime | None, int]:
    """读 (locked_until, failed_attempts)。"""
    with _engine.begin() as conn:
        row = conn.execute(
            text("SELECT locked_until, failed_attempts FROM users WHERE id = :uid"),
            {"uid": user_id},
        ).one()
    return row[0], row[1]


async def _fail_login(client, account: Account, times: int) -> None:
    for _ in range(times):
        response = await client.post(
            "/api/auth/login",
            json={"username": account.username, "password": "wrong-password"},
        )
        assert response.status_code == 401


async def test_lockout_lasts_fifteen_minutes(client, single_user: Account) -> None:
    """5 次错之后锁定到期时间必须落在 now+15min 附近，且失败计数清零重算。"""
    before = datetime.now(UTC)
    await _fail_login(client, single_user, MAX_FAILED_ATTEMPTS)

    locked_until, failed = _user_row(single_user.id)
    assert locked_until is not None, "5 次失败后应该写 locked_until"
    delta = locked_until - before
    # 允许 1 分钟误差（用例本身有耗时），但必须明显是 15 分钟量级
    assert timedelta(minutes=LOCK_MINUTES - 1) <= delta <= timedelta(minutes=LOCK_MINUTES + 1)
    # 计数清零：解锁后重新给 5 次机会，而不是一次就再锁
    assert failed == 0


async def test_lock_returns_retry_after_minutes(client, single_user: Account) -> None:
    """锁定期间的 401 要说清"还有几分钟"，前端才能给出可执行的提示。"""
    await _fail_login(client, single_user, MAX_FAILED_ATTEMPTS)

    locked = await client.post(
        "/api/auth/login",
        json={"username": single_user.username, "password": TEST_PASSWORD},
    )

    assert locked.status_code == 401
    body = locked.json()["error"]
    assert body["code"] == "ACCOUNT_LOCKED"
    assert 14 <= body["detail"]["retry_after_minutes"] <= 15


async def test_lock_expires_and_login_recovers(client, single_user: Account) -> None:
    """锁到期后不需要人工干预：把库里的时间改到过去，用正确密码就能进。"""
    await _fail_login(client, single_user, MAX_FAILED_ATTEMPTS)

    with _engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET locked_until = now() - interval '1 minute' WHERE id = :uid"),
            {"uid": single_user.id},
        )

    recovered = await client.post(
        "/api/auth/login",
        json={"username": single_user.username, "password": TEST_PASSWORD},
    )

    assert recovered.status_code == 200
    locked_until, failed = _user_row(single_user.id)
    assert locked_until is None, "登录成功后要清掉锁定时间"
    assert failed == 0


async def test_ip_rate_limit_returns_429(client, single_user: Account) -> None:
    """同一 IP 超过每分钟上限 → 429，带 retry_after；且不再走密码校验。"""
    # 把上限调小，避免一个用例打十几次请求
    original_limit = login_limiter.limit
    login_limiter.limit = 3
    try:
        await _fail_login(client, single_user, 3)

        blocked = await client.post(
            "/api/auth/login",
            json={"username": single_user.username, "password": TEST_PASSWORD},
        )
    finally:
        login_limiter.limit = original_limit

    assert blocked.status_code == 429
    body = blocked.json()["error"]
    assert body["code"] == "RATE_LIMITED"
    assert body["detail"]["retry_after"] >= 1


async def test_successful_login_resets_ip_counter(client, single_user: Account) -> None:
    """登录成功要清掉这个 IP 的计数，否则正常用户点几下就被自己限流。"""
    original_limit = login_limiter.limit
    login_limiter.limit = 3
    try:
        await _fail_login(client, single_user, 1)
        await login(client, single_user)  # 成功 → 计数清零

        # 清完之后还能再失败两次而不被限流
        await _fail_login(client, single_user, 2)
    finally:
        login_limiter.limit = original_limit


async def test_unknown_user_and_wrong_password_look_identical(client, single_user: Account) -> None:
    """防账号枚举：不存在的账号和密码错误返回同一句话、同一个码。"""
    await _fail_login(client, single_user, 1)  # 用掉这次 IP 配额里的 1 次，避免和上一条冲突
    with_password_error = await client.post(
        "/api/auth/login",
        json={"username": single_user.username, "password": "wrong-password"},
    )
    unknown_user = await client.post(
        "/api/auth/login",
        json={"username": "nobody-here-at-all", "password": "wrong-password"},
    )

    assert with_password_error.status_code == unknown_user.status_code == 401
    assert with_password_error.json()["error"]["message"] == unknown_user.json()["error"]["message"]
    assert with_password_error.json()["error"]["code"] == unknown_user.json()["error"]["code"]
