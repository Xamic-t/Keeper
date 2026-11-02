"""助理对话：chat_messages 表 + RLS

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-03 02:10:00

对应文档：docs/06 5.10、ARCHITECTURE 19.7.10

为什么现在才建这张表：2026-11-03 用户否决了 iframe 方案，助理改成主界面自研对话，
会话记录必须落在 Postgres（19.7.4 第 1 条：业务真相只在 Postgres）。

三件事，缺一不可：
  1. 建表（含 role 的 CHECK、user_id 外键级联）
  2. **ENABLE + FORCE RLS + USING/WITH CHECK**——和 0002 里其它业务表一模一样。
     漏了这一步，chat_messages 就是全库唯一能被越权读的表，而且不报错
  3. 给 keeper_app 授权（新表建出来默认没有权限，应用会直接 500）

downgrade 直接删表：这张表只有助理自己的会话，删掉不影响任何业务数据。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("image_raw_input_id", sa.Integer(), nullable=True),
        sa.Column(
            "actions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_role"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["image_raw_input_id"], ["raw_inputs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_user_id", "chat_messages", ["user_id"])
    op.create_index("ix_chat_user_created", "chat_messages", ["user_id", "created_at"])

    op.execute("ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chat_messages FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON chat_messages
            USING (user_id = current_setting('app.user_id')::bigint)
            WITH CHECK (user_id = current_setting('app.user_id')::bigint)
        """
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON chat_messages TO keeper_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE chat_messages_id_seq TO keeper_app")


def downgrade() -> None:
    op.drop_index("ix_chat_user_created", table_name="chat_messages")
    op.drop_index("ix_chat_messages_user_id", table_name="chat_messages")
    op.drop_table("chat_messages")
