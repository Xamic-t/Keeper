"""add server defaults

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28 02:35:00

对应文档：docs/W1-完成报告.md 遗留问题 1（多数表 default 只在 Python 层）

为什么补：
  W1 建表时 default 只写在 SQLModel 层，库里没有 DEFAULT 约束。
  通过 ORM 插入没问题，但**直接用 SQL 插数据会被 NOT NULL 拦住**——
  W2 实跑时就撞到了：手插 facts 少了 weight，报 null value in column "weight"。

补哪些：只补"有 Python 默认值 + NOT NULL"的列，其余保持 NULL 由应用决定。
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None

# (表, 列, 库里的默认值) —— 必须与 models/ 里的 Python default 一致
SERVER_DEFAULTS: list[tuple[str, str, sa.sql.elements.TextClause]] = [
    ("users", "role", sa.text("'member'")),
    ("users", "is_active", sa.text("true")),
    ("users", "failed_attempts", sa.text("0")),
    ("users", "keep_original_images", sa.text("true")),
    ("relationships", "stage", sa.text("'new'")),
    ("relationships", "stage_source", sa.text("'auto'")),
    ("relationships", "value_tier", sa.text("'unknown'")),
    ("relationships", "value_tier_source", sa.text("'auto'")),
    ("relationships", "is_active", sa.text("true")),
    ("facts", "weight", sa.text("1.0")),
    ("facts", "status", sa.text("'pending'")),
    ("raw_inputs", "status", sa.text("'pending'")),
    ("chat_segments", "said_at_inferred", sa.text("false")),
    ("events", "is_done", sa.text("false")),
]


def upgrade() -> None:
    for table, column, default in SERVER_DEFAULTS:
        op.alter_column(table, column, server_default=default)


def downgrade() -> None:
    for table, column, _default in SERVER_DEFAULTS:
        op.alter_column(table, column, server_default=None)
