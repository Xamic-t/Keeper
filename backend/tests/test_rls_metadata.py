"""RLS 全表审计：把"数据库层强制隔离"从信念变成断言。

ARCHITECTURE §11.2 / §15.1–15.5 / §17 的三条硬约束：
  ① 业务表必须 ENABLE + FORCE RLS（FORCE 才能约束表 owner）
  ② 策略必须同时有 USING 与 WITH CHECK（只有 USING 等于"看得见别人的，还能写进去"）
  ③ 应用连接角色不能是 superuser / BYPASSRLS（否则 RLS 静默失效，不报任何错）

这里直接查 `pg_class` / `pg_policies` / `pg_roles`，不经过应用代码——
应用层测试只能证明"我查的时候挡住了"，证明不了"换个人来也挡住"。

**新增表没归类 → 直接失败**。这是故意的：漏一张表比多写一行清单贵得多。
"""

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import app.models  # noqa: F401 — 导入即注册全部表到 SQLModel.metadata
from app.config import settings

# 不开 RLS 的表：登录前就要按 username 查，那时还没有 user_id（§15.5 唯一例外）。
# 白名单必须显式写出来，且下面有用例反向验证"它确实没开"——
# 不能靠"查询结果里没出现，所以算过"。
RLS_EXEMPT: dict[str, str] = {
    "users": "登录前查询，此时无 app.user_id（ARCHITECTURE §15.5）",
}

# 不是业务表：Alembic 自己的版本表，不参与隔离审计。
NON_BUSINESS: frozenset[str] = frozenset({"alembic_version"})


@dataclass(frozen=True)
class TableRls:
    """一张表在数据库里的真实 RLS 状态。"""

    name: str
    enabled: bool
    forced: bool
    policies: tuple[tuple[str, str | None, str | None], ...]  # (cmd, using, with_check)


@pytest.fixture(scope="module")
def rls_state() -> Iterator[dict[str, TableRls]]:
    """一次查询把全部表的 RLS 状态取回来。"""
    engine = create_engine(settings.database_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            relations = conn.execute(
                text(
                    """
                    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'r'
                    """
                )
            ).all()
            policies = conn.execute(
                text(
                    """
                    SELECT tablename, cmd, qual, with_check
                    FROM pg_policies
                    WHERE schemaname = 'public'
                    """
                )
            ).all()
    finally:
        engine.dispose()

    by_table: dict[str, list[tuple[str, str | None, str | None]]] = {}
    for tablename, cmd, qual, with_check in policies:
        by_table.setdefault(tablename, []).append((cmd, qual, with_check))

    return {
        name: TableRls(
            name=name,
            enabled=enabled,
            forced=forced,
            policies=tuple(by_table.get(name, ())),
        )
        for name, enabled, forced in relations
    }


def _business_tables() -> list[str]:
    """业务表 = 模型里定义了、且不在白名单里的表。"""
    return sorted(set(SQLModel.metadata.tables) - NON_BUSINESS - set(RLS_EXEMPT))


def test_every_model_table_is_classified() -> None:
    """模型里的每张表都必须被明确归类：业务表 / 白名单。

    新加一张表却忘了处理 RLS 时，这条会先炸——比在生产上发现数据串号便宜。
    """
    declared = set(SQLModel.metadata.tables)
    classified = set(_business_tables()) | set(RLS_EXEMPT)
    assert declared == classified, (
        f"有表没归类：{sorted(declared - classified)}；"
        f"归类了但模型里不存在：{sorted(classified - declared)}"
    )


def test_no_unclassified_table_in_database(rls_state: dict[str, TableRls]) -> None:
    """数据库里也不许有"来路不明"的表。

    模型是表的唯一来源；库里多出一张表说明有人手建表或漏了迁移入库。
    这类表默认没有 RLS，最危险。
    """
    known = set(SQLModel.metadata.tables) | NON_BUSINESS
    unknown = sorted(set(rls_state) - known)
    assert not unknown, f"数据库里存在模型未定义的表（默认无 RLS）：{unknown}"


def test_business_table_count_matches_architecture() -> None:
    """ARCHITECTURE / docs/06 的 14 张表 = 13 张业务表 + users（2026-11-03 增 chat_messages）。"""
    assert len(_business_tables()) == 13
    assert set(SQLModel.metadata.tables) == set(_business_tables()) | set(RLS_EXEMPT)


@pytest.mark.parametrize("table", _business_tables())
def test_business_table_has_enabled_and_forced_rls(
    table: str, rls_state: dict[str, TableRls]
) -> None:
    """业务表必须 ENABLE + FORCE RLS。

    FORCE 不能省：keeper_app 不是 owner 时确实不需要它，但只要有人用 owner
    跑一次迁移或脚本，缺 FORCE 就能读到全库数据，而且不报错。
    """
    state = rls_state[table]
    assert state.enabled, f"{table} 没开 RLS"
    assert state.forced, f"{table} 开了 RLS 但没 FORCE（表 owner 仍可绕过）"


@pytest.mark.parametrize("table", _business_tables())
def test_business_table_has_using_and_with_check(
    table: str, rls_state: dict[str, TableRls]
) -> None:
    """策略必须同时约束读和写，且都落在 app.user_id 上。"""
    state = rls_state[table]
    assert state.policies, f"{table} 没有 RLS 策略"

    for cmd, using, with_check in state.policies:
        # FOR ALL 的策略 USING 与 WITH CHECK 都要有；
        # 分段策略（如只 SELECT）允许缺 WITH CHECK，但本项目统一用 ALL。
        assert using is not None, f"{table} 的策略 {cmd} 缺 USING"
        if cmd == "ALL":
            assert with_check is not None, f"{table} 的 FOR ALL 策略缺 WITH CHECK"
        # 断言表达式引用的是会话变量，而不是写死的 user_id
        assert "app.user_id" in (using or ""), f"{table} 的 USING 没引用 app.user_id：{using}"


@pytest.mark.parametrize("table", sorted(RLS_EXEMPT))
def test_exempt_table_really_has_no_rls(table: str, rls_state: dict[str, TableRls]) -> None:
    """白名单里的表必须确实没开 RLS。

    反向校验：如果哪天有人给 users 开了 RLS 却没改设计文档，登录会直接挂，
    而这里会先失败并指出"白名单过期了"。
    """
    state = rls_state[table]
    assert not state.enabled, f"{table} 在白名单里，但已经开了 RLS——白名单过期，请更新 §15.5"
    assert not state.policies, f"{table} 在白名单里，却存在策略：{state.policies}"


def test_business_tables_all_have_user_id_column() -> None:
    """策略按 user_id 隔离，前提是每张业务表都有 user_id。

    少一列 → 策略引用不存在的列（建表就失败），但反过来说：
    新表若故意不带 user_id（全局共享表），必须显式进白名单，不能默默塞进业务表。
    """
    missing = [
        table
        for table, tbl in SQLModel.metadata.tables.items()
        if table not in NON_BUSINESS and table not in RLS_EXEMPT and "user_id" not in tbl.columns
    ]
    assert not missing, f"这些表没有 user_id，无法按租户隔离：{missing}"


def test_app_role_cannot_bypass_rls() -> None:
    """应用连接角色不能是 superuser，也不能有 BYPASSRLS。

    ARCHITECTURE §17 第 3 条 + 第 9 条。这条最容易在本地开发时被破坏：
    用 owner 账号连库跑一遍，所有隔离测试都会"通过"，因为压根没隔离。
    """
    engine = create_engine(settings.database_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).one()
    finally:
        engine.dispose()

    role, is_super, bypass = row
    assert not is_super, f"应用连库角色 {role} 是 superuser，RLS 会被完全绕过"
    assert not bypass, f"应用连库角色 {role} 带 BYPASSRLS，RLS 会被完全绕过"
