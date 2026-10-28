"""persons 表：人的客观身份。

对应文档：docs/06-数据库设计.md 5.3
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Person(SQLModel, table=True):
    """被维护的人。只放客观信息，关系状态放 relationships。"""

    __tablename__ = "persons"
    __table_args__ = (
        Index("ix_persons_user_nickname", "user_id", "nickname"),
        Index("ix_persons_aliases_gin", "aliases", postgresql_using="gin"),
        Index("ix_persons_platform_ids_gin", "platform_ids", postgresql_using="gin"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False)
    nickname: str = Field(max_length=50, nullable=False)
    aliases: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    platform_ids: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    avatar_path: str | None = Field(default=None, max_length=500)
    contact: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    note: str | None = Field(default=None, max_length=2000)
    # 画像综述（W7 起懒生成，≤150 字）。stale=True 表示"事实变了，该重写"
    summary: str | None = Field(default=None, sa_column=Column(Text))
    summary_stale: bool = Field(
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
