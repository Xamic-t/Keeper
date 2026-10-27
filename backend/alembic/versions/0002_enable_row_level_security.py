"""enable row level security

Revision ID: 0002
Revises: d0a58eb7fdfe
Create Date: 2026-10-28 01:29:21

对应文档：docs/06-数据库设计.md 第 8 节

为什么单独一个迁移：
  表结构和安全策略是两件事，分开便于回滚和排查。

关键点：
  1. users 表不开 RLS —— 登录前还不知道 user_id
  2. 每张表同时写 USING 和 WITH CHECK ——
     只写 USING 的话，插入时可以伪造别人的 user_id
  3. FORCE ROW LEVEL SECURITY —— 让表 owner 也受约束，
     防止用 owner 角色绕过
  4. current_setting 不传第二参 —— 变量缺失时直接报错，
     比静默返回空结果更早暴露问题（fail loud 而非 fail silent）
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "d0a58eb7fdfe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 需要开 RLS 的业务表（users 不在其中）
RLS_TABLES = [
    "persons",
    "relationships",
    "raw_inputs",
    "chat_segments",
    "facts",
    "tags",
    "ledger",
    "events",
    "reminders",
    "todos",
    "extraction_logs",
]

POLICY = """
CREATE POLICY tenant_isolation ON {table}
    USING (user_id = current_setting('app.user_id')::bigint)
    WITH CHECK (user_id = current_setting('app.user_id')::bigint)
"""


def upgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(POLICY.format(table=table))

    # 新表建好后要补授权（0001 时 keeper_app 还没表可授）
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO keeper_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO keeper_app")


def downgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
