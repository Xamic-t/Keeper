"""w3 schema additions

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-28 03:05:00

三件事：
  1. `persons` 补画像综述两列（W7 要用，docs/06 5.3 已同步）
  2. `tags` 加唯一索引 —— 派生标签靠 ON CONFLICT DO NOTHING 去重
  3. 新建 `notification_channels` —— 推送通道从 .env 挪到库表（docs/06 5.14）
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("persons", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "persons",
        sa.Column(
            "summary_stale", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )

    op.create_index(
        "uq_tag_person_key_value", "tags", ["person_id", "key", "value"], unique=True
    )

    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column(
            "config",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default=sa.text("100")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('wecom', 'pushplus')", name="ck_channel_kind"),
    )
    op.create_index("ix_notification_channels_user_id", "notification_channels", ["user_id"])
    op.create_index(
        "ix_channel_user_priority", "notification_channels", ["user_id", "priority"]
    )

    # 新表同样要开 RLS：用户的推送通道只能自己看
    op.execute("ALTER TABLE notification_channels ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notification_channels FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON notification_channels "
        "USING (user_id = current_setting('app.user_id')::bigint) "
        "WITH CHECK (user_id = current_setting('app.user_id')::bigint)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON notification_channels TO keeper_app"
    )
    op.execute("GRANT USAGE, SELECT ON SEQUENCE notification_channels_id_seq TO keeper_app")


def downgrade() -> None:
    op.drop_table("notification_channels")
    op.drop_index("uq_tag_person_key_value", table_name="tags")
    op.drop_column("persons", "summary_stale")
    op.drop_column("persons", "summary")
