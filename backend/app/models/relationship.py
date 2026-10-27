"""relationships 表：人与我的关系状态。

对应文档：docs/06-数据库设计.md 5.4
关键约束：person_id 唯一 → 一个人只有一段关系（多角色用 roles 数组表达）
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Index,
    Numeric,
    SmallInteger,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Relationship(SQLModel, table=True):
    """关系状态。身份信息在 persons，这里只放"我跟他的关系"。"""

    __tablename__ = "relationships"
    __table_args__ = (
        CheckConstraint("stage IN ('new', 'familiar', 'core', 'cold')", name="ck_rel_stage"),
        CheckConstraint("stage_source IN ('auto', 'manual')", name="ck_rel_stage_source"),
        CheckConstraint(
            "value_tier IN ('high', 'mid', 'low', 'unknown')",
            name="ck_rel_value_tier",
        ),
        CheckConstraint("value_tier_source IN ('auto', 'manual')", name="ck_rel_value_tier_source"),
        CheckConstraint(
            "birthday_type IS NULL OR birthday_type IN ('solar', 'lunar')",
            name="ck_rel_birthday_type",
        ),
        CheckConstraint(
            "gift_budget_min IS NULL OR gift_budget_max IS NULL "
            "OR gift_budget_min <= gift_budget_max",
            name="ck_rel_gift_budget",
        ),
        CheckConstraint(
            "temp_score IS NULL OR (temp_score >= 0 AND temp_score <= 100)",
            name="ck_rel_temp_score",
        ),
        Index("ix_rel_user_active", "user_id", "is_active"),
        Index("ix_rel_user_stage", "user_id", "stage"),
        Index(
            "ix_rel_birthday",
            "birthday_solar",
            postgresql_where=text("birthday_solar IS NOT NULL"),
        ),
        Index("ix_rel_last_interaction", "last_interaction_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False)
    person_id: int = Field(
        foreign_key="persons.id",
        ondelete="CASCADE",
        unique=True,
        index=True,
        nullable=False,
    )

    # 角色与阶段
    roles: list[str] = Field(
        default_factory=lambda: ["ge_jie"],
        sa_column=Column(JSONB, nullable=False, server_default=text("'[\"ge_jie\"]'::jsonb")),
    )
    stage: str = Field(
        default="new", max_length=20, nullable=False, sa_column_kwargs={"server_default": "new"}
    )
    stage_source: str = Field(
        default="auto", max_length=10, nullable=False, sa_column_kwargs={"server_default": "auto"}
    )
    stage_updated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )

    # 价值
    value_tier: str = Field(
        default="unknown",
        max_length=20,
        nullable=False,
        sa_column_kwargs={"server_default": "unknown"},
    )
    value_tier_source: str = Field(
        default="auto", max_length=10, nullable=False, sa_column_kwargs={"server_default": "auto"}
    )
    total_in: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 2), nullable=False, server_default="0"),
    )
    total_out: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 2), nullable=False, server_default="0"),
    )

    # 生日与送礼
    birthday_solar: date | None = Field(default=None, sa_column=Column(Date))
    birthday_lunar: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    birthday_type: str | None = Field(default=None, max_length=10)
    gift_budget_min: Decimal | None = Field(default=None, sa_column=Column(Numeric(12, 2)))
    gift_budget_max: Decimal | None = Field(default=None, sa_column=Column(Numeric(12, 2)))
    taboo: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )

    # 温度（V2 写入，V1 只建列）
    temp_score: int | None = Field(default=None, sa_column=Column(SmallInteger))
    temp_trend: int | None = Field(default=None, sa_column=Column(SmallInteger))
    temp_updated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )

    # 活跃窗口
    last_interaction_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    is_active: bool = Field(
        default=True, nullable=False, sa_column_kwargs={"server_default": text("true")}
    )

    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )
    )
