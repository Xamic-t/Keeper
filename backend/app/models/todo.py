"""todos：待办。

对应文档：docs/06-数据库设计.md 5.12
注意：person_id 用 SET NULL —— 删人时任务保留，只是失去归属。
这是唯一不跟着人一起删的业务表。
"""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Index,
    Text,
    func,
)
from sqlmodel import Field, SQLModel


class Todo(SQLModel, table=True):
    """一件要做的事。"""

    __tablename__ = "todos"
    __table_args__ = (
        CheckConstraint(
            "source IN ('template', 'ai', 'reminder', 'manual')", name="ck_todo_source"
        ),
        CheckConstraint("status IN ('open', 'done', 'dropped')", name="ck_todo_status"),
        Index("ix_todo_user_status_due", "user_id", "status", "due_date"),
        Index("ix_todo_person", "person_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False)
    # SET NULL：删人不删任务
    person_id: int | None = Field(default=None, foreign_key="persons.id", ondelete="SET NULL")
    title: str = Field(max_length=200, nullable=False)
    detail: str | None = Field(default=None, sa_column=Column(Text))
    source: str = Field(max_length=20, nullable=False)
    source_id: int | None = Field(default=None)
    ai_reason: str | None = Field(default=None, sa_column=Column(Text))
    due_date: date | None = Field(default=None, sa_column=Column(Date))
    status: str = Field(
        default="open",
        max_length=20,
        nullable=False,
        sa_column_kwargs={"server_default": "open"},
    )
    done_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
