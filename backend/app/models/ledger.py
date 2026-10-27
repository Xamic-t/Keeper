"""ledger：往来账。

对应文档：docs/06-数据库设计.md 5.9
金额永远正数，方向由 direction 表达。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlmodel import Field, SQLModel


class Ledger(SQLModel, table=True):
    """一笔钱的进出。"""

    __tablename__ = "ledger"
    __table_args__ = (
        CheckConstraint("direction IN ('in', 'out')", name="ck_ledger_direction"),
        CheckConstraint(
            "kind IN ('tip', 'order', 'gift', 'payment', 'refund')",
            name="ck_ledger_kind",
        ),
        CheckConstraint("amount > 0", name="ck_ledger_amount_positive"),
        CheckConstraint("due_date IS NULL OR due_date >= occurred_at", name="ck_ledger_due_date"),
        Index("ix_ledger_person_time", "person_id", "occurred_at"),
        Index("ix_ledger_user_time", "user_id", "occurred_at"),
        Index(
            "ix_ledger_due",
            "user_id",
            "due_date",
            postgresql_where=text("due_date IS NOT NULL AND settled_at IS NULL"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False)
    person_id: int = Field(foreign_key="persons.id", ondelete="CASCADE", nullable=False)
    direction: str = Field(max_length=10, nullable=False)
    kind: str = Field(max_length=20, nullable=False)
    amount: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    currency: str = Field(
        default="CNY", sa_column=Column(String(3), nullable=False, server_default="CNY")
    )
    occurred_at: date = Field(sa_column=Column(Date, nullable=False))
    due_date: date | None = Field(default=None, sa_column=Column(Date))
    settled_at: date | None = Field(default=None, sa_column=Column(Date))
    note: str | None = Field(default=None, sa_column=Column(Text))
    source_type: str | None = Field(default=None, max_length=20)
    source_id: int | None = Field(default=None)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
