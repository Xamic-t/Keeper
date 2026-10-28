"""测试公用夹具。

**需要真实 Postgres**——RLS 只能在真库上验证，mock 掉了等于没测。
跑之前：`docker compose up -d db`。

三条约定：
  ① 每个用例用独立账号（随机用户名），teardown 删账号，级联清干净别人的数据
  ② 建账号走同步 engine —— 密码哈希是同步的，也避免把连接绑到错误的循环上
  ③ 业务查询走 rls_session()，它替路由做同一件事：SET LOCAL app.user_id
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.ratelimit import login_limiter
from app.core.security import hash_password
from app.db import SessionLocal
from app.main import app

TEST_PASSWORD = "Test-pass-123"

# 建账号/清账号用的同步引擎。psycopg 同时支持同步与异步驱动，
# 这里用同步是为了不跟 pytest 的事件循环纠缠。
_sync_engine = create_engine(settings.database_url, pool_pre_ping=True)


@dataclass(frozen=True)
class Account:
    """测试账号。密码统一，方便接口测试直接登录。"""

    id: int
    username: str
    password: str = TEST_PASSWORD


def create_user(prefix: str = "t") -> Account:
    """建一个普通角色账号。

    直接写 SQL 而不用 ORM：users 表没有开 RLS，也不该为测试引入额外封装。
    """
    username = f"{prefix}-{uuid.uuid4().hex[:10]}"
    with _sync_engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO users
                    (username, password_hash, display_name, role, is_active,
                     failed_attempts, keep_original_images)
                VALUES (:username, :password_hash, :display_name, 'member', true, 0, true)
                RETURNING id
                """
            ),
            {
                "username": username,
                "password_hash": hash_password(TEST_PASSWORD),
                "display_name": username,
            },
        ).one()
    return Account(id=row[0], username=username)


def delete_users(*user_ids: int) -> None:
    """删账号。persons 等表靠外键 ON DELETE CASCADE 跟着清。"""
    if not user_ids:
        return
    with _sync_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM users WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)),
            {"ids": list(user_ids)},
        )


@asynccontextmanager
async def rls_session(user_id: int) -> AsyncIterator[AsyncSession]:
    """带 RLS 变量的 session，等价于路由里的 get_session。

    变量由 db.py 的 after_begin 事件下发，这里只往 session.info 放 user_id。
    """
    async with SessionLocal() as session:
        session.info["rls_user_id"] = user_id
        yield session


@asynccontextmanager
async def raw_session() -> AsyncIterator[AsyncSession]:
    """系统级 session（不设 RLS 变量），等价于 deps.get_raw_session。"""
    async with SessionLocal() as session:
        yield session


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """登录限流是进程级状态，不清会让用例之间互相影响。"""
    login_limiter.clear()


@pytest.fixture
def two_users() -> Iterator[tuple[Account, Account]]:
    """两个互不相关的账号，用来验证 RLS 隔离。"""
    user_a = create_user("a")
    user_b = create_user("b")
    yield user_a, user_b
    delete_users(user_a.id, user_b.id)


@pytest.fixture
def single_user() -> Iterator[Account]:
    """单个账号，给不需要隔离验证的用例省点开销。"""
    user = create_user("s")
    yield user
    delete_users(user.id)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """在同一个事件循环里跑 ASGI 应用，不走真实网络端口。"""
    async with new_client() as http:
        yield http


@asynccontextmanager
async def new_client() -> AsyncIterator[httpx.AsyncClient]:
    """另起一个客户端（独立 cookie 罐）。验证双账号时用它。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def login(client: httpx.AsyncClient, user: Account) -> str:
    """登录并返回 CSRF token。cookie 由 client 自己记住。"""
    response = await client.post(
        "/api/auth/login",
        json={"username": user.username, "password": user.password},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["csrf_token"]


def csrf_headers(csrf_token: str) -> dict[str, str]:
    """写操作要带的头。"""
    return {"X-CSRF-Token": csrf_token}
