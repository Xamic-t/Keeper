"""RAG 切片表 `vector_chunks`（V2，2026-11-04 建）。

对应文档：docs/06 §5.16、ARCHITECTURE 19.3 / 19.7.11

为什么 V1 没建、现在建：V1 的问答靠"把人名单塞进 prompt 让模型自己认"，
人一多就撑不住，也不支持"我上次是不是说过她喜欢白茶"这类跨材料的问题。
V2 要 RAG，就得有地方放向量。

三件事：
  1. `CREATE EXTENSION vector`——pgvector 已经装在这个镜像里（0.8.7），
     但扩展默认没启用，不建就 `type "vector" does not exist`
  2. 建表 + HNSW 索引（余弦）。**HNSW 而不是 ivfflat**：ivfflat 要先有数据
     才能训练聚类中心，空表建了是假的；HNSW 可以空表建、边写边用
  3. RLS 与其它业务表完全一致（ENABLE + FORCE + USING/WITH CHECK）
     —— 向量检索最容易"顺手把别人的切片召回"，这条必须在库里挡

无 key 的降级不在这层：没配向量模型时 `services/rag.py` 走关键词检索，
表就是空的，不影响任何功能。
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None

DIM = 1024


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "vector_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("scope IN ('memory', 'knowledge')", name="ck_vector_scope"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["persons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # orm 那边用自定义类型，迁移这里直接写 SQL 的 vector(1024)：
    # alembic 不该为了一个类型去 import 应用代码
    op.execute(f"ALTER TABLE vector_chunks ALTER COLUMN embedding TYPE vector({DIM}) USING NULL")

    op.create_index("ix_vector_person_scope", "vector_chunks", ["person_id", "scope"])
    op.create_index("ix_vector_source", "vector_chunks", ["user_id", "source_type", "source_id"])
    op.execute(
        "CREATE INDEX ix_vector_embedding ON vector_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.execute("ALTER TABLE vector_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE vector_chunks FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON vector_chunks
            USING (user_id = current_setting('app.user_id')::bigint)
            WITH CHECK (user_id = current_setting('app.user_id')::bigint)
        """
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON vector_chunks TO keeper_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE vector_chunks_id_seq TO keeper_app")


def downgrade() -> None:
    op.drop_index("ix_vector_embedding", table_name="vector_chunks")
    op.drop_index("ix_vector_source", table_name="vector_chunks")
    op.drop_index("ix_vector_person_scope", table_name="vector_chunks")
    op.drop_table("vector_chunks")
