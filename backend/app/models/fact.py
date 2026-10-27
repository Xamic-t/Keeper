"""facts + tags：事实与标签。

对应文档：docs/06-数据库设计.md 5.7 / 5.8
tags 是派生表，可从 facts 重算，允许丢失。
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlmodel import Field, SQLModel


class Fact(SQLModel, table=True):
    """从材料里抽出来的一条事实，带半衰期。"""

    __tablename__ = "facts"
    __table_args__ = (
        CheckConstraint(
            "category IN ('permanent', 'taboo', 'structure', 'preference', 'situation', 'mood')",
            name="ck_fact_category",
        ),
        CheckConstraint("status IN ('pending', 'confirmed', 'rejected')", name="ck_fact_status"),
        CheckConstraint(
            "source_type IN ('screenshot', 'text', 'manual')",
            name="ck_fact_source_type",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_fact_conf"),
        CheckConstraint("weight >= 0 AND weight <= 1", name="ck_fact_weight"),
        Index("ix_fact_person_status", "person_id", "status"),
        Index("ix_fact_person_category", "person_id", "category"),
        Index("ix_fact_user_status", "user_id", "status"),
        Index("ix_fact_person_weight", "person_id", "weight"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False)
    person_id: int = Field(foreign_key="persons.id", ondelete="CASCADE", nullable=False)
    content: str = Field(sa_column=Column(Text, nullable=False))
    category: str = Field(max_length=20, nullable=False)
    half_life_days: int | None = Field(default=None, sa_column=Column(Integer))
    weight: float = Field(
        default=1.0,
        sa_column=Column(Float, nullable=False, server_default=text("1.0")),
    )
    confidence: float = Field(sa_column=Column(Float, nullable=False))
    source_type: str = Field(max_length=20, nullable=False)
    source_id: int | None = Field(default=None, description="多态引用，无外键")
    evidence: str | None = Field(default=None, sa_column=Column(Text))
    status: str = Field(
        default="pending",
        max_length=20,
        nullable=False,
        sa_column_kwargs={"server_default": "pending"},
    )
    last_confirmed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )


class Tag(SQLModel, table=True):
    """事实的结构化摘要，用于跨人筛选。派生表，可全量重建。"""

    __tablename__ = "tags"
    __table_args__ = (
        CheckConstraint(
            "key IN ('industry', 'family', 'preference', 'situation', 'value', 'stage')",
            name="ck_tag_key",
        ),
        Index("ix_tag_user_key_value", "user_id", "key", "value"),
        Index("ix_tag_person", "person_id"),
        Index("ix_tag_expires", "expires_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False)
    person_id: int = Field(foreign_key="persons.id", ondelete="CASCADE", nullable=False)
    key: str = Field(max_length=20, nullable=False)
    value: str = Field(max_length=100, nullable=False)
    source_fact_id: int | None = Field(default=None, foreign_key="facts.id", ondelete="SET NULL")
    expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    confidence: float | None = Field(default=None, sa_column=Column(Float))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
