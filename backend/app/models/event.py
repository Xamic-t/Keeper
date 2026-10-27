"""events + reminders：日历事件与提醒。

对应文档：docs/06-数据库设计.md 5.10 / 5.11
reminders 有 UNIQUE(event_id, remind_on, level) —— 定时任务重跑不会重复发。
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Index,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Event(SQLModel, table=True):
    """一个日子。生日/节日/结款/自定义。"""

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "type IN ('birthday', 'festival', 'payment_due', 'custom')",
            name="ck_event_type",
        ),
        Index("ix_event_user_date", "user_id", "occurs_on"),
        Index("ix_event_user_type", "user_id", "type"),
        Index("ix_event_person", "person_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False)
    # 节日不挂人，所以可空
    person_id: int | None = Field(default=None, foreign_key="persons.id", ondelete="CASCADE")
    type: str = Field(max_length=20, nullable=False)
    title: str = Field(max_length=100, nullable=False)
    occurs_on: date = Field(sa_column=Column(Date, nullable=False))
    rrule: str | None = Field(default=None, max_length=200)
    lunar: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    source_type: str | None = Field(default=None, max_length=20)
    source_id: int | None = Field(default=None)
    is_done: bool = Field(
        default=False, nullable=False, sa_column_kwargs={"server_default": text("false")}
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )


class Reminder(SQLModel, table=True):
    """一次推送记录。payload 存生成好的完整内容，保证不可变。"""

    __tablename__ = "reminders"
    __table_args__ = (
        CheckConstraint("level IN ('d7', 'd3', 'd0')", name="ck_reminder_level"),
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'clicked')",
            name="ck_reminder_status",
        ),
        Index(
            "uq_reminder_event_level",
            "event_id",
            "remind_on",
            "level",
            unique=True,
        ),
        Index("ix_reminder_status_date", "status", "remind_on"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False)
    event_id: int = Field(foreign_key="events.id", ondelete="CASCADE", nullable=False)
    person_id: int | None = Field(default=None, foreign_key="persons.id", ondelete="CASCADE")
    remind_on: date = Field(sa_column=Column(Date, nullable=False))
    level: str = Field(max_length=5, nullable=False)
    channel: str | None = Field(default=None, max_length=30)
    payload: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    status: str = Field(
        default="pending",
        max_length=20,
        nullable=False,
        sa_column_kwargs={"server_default": "pending"},
    )
    sent_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
