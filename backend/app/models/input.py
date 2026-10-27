"""raw_inputs + chat_segments：原始材料与对话片段。

对应文档：docs/06-数据库设计.md 5.5 / 5.6
注：chat_segments 加了 user_id（06 原表没有），否则 RLS 保护不到这张表。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Text, func, text
from sqlmodel import Field, SQLModel


class RawInput(SQLModel, table=True):
    """一次投喂：一张截图，或一句话。"""

    __tablename__ = "raw_inputs"
    __table_args__ = (
        CheckConstraint("kind IN ('screenshot', 'text')", name="ck_raw_kind"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'failed')",
            name="ck_raw_status",
        ),
        Index("ix_raw_user_status", "user_id", "status"),
        Index("ix_raw_user_person", "user_id", "person_id"),
        Index(
            "uq_raw_user_hash",
            "user_id",
            "file_hash",
            unique=True,
            postgresql_where=text("file_hash IS NOT NULL"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False)
    # 归属判定前为空，判定后回填；删人时级联
    person_id: int | None = Field(default=None, foreign_key="persons.id", ondelete="CASCADE")
    kind: str = Field(max_length=20, nullable=False)
    file_path: str | None = Field(default=None, max_length=500)
    file_hash: str | None = Field(default=None, max_length=64)
    ocr_text: str | None = Field(default=None, sa_column=Column(Text))
    raw_text: str | None = Field(default=None, sa_column=Column(Text))
    status: str = Field(
        default="pending",
        max_length=20,
        nullable=False,
        sa_column_kwargs={"server_default": "pending"},
    )
    error_msg: str | None = Field(default=None, max_length=500)
    file_deleted_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
    processed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class ChatSegment(SQLModel, table=True):
    """截图里拆出来的一句话。"""

    __tablename__ = "chat_segments"
    __table_args__ = (
        CheckConstraint("speaker IN ('me', 'other')", name="ck_seg_speaker"),
        Index("ix_seg_raw_seq", "raw_input_id", "seq"),
        Index("ix_seg_person_time", "person_id", "said_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    # 冗余 user_id，让 RLS 能直接作用在这张表上
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False)
    raw_input_id: int = Field(foreign_key="raw_inputs.id", ondelete="CASCADE", nullable=False)
    person_id: int | None = Field(default=None, foreign_key="persons.id", ondelete="CASCADE")
    speaker: str = Field(max_length=10, nullable=False)
    content: str = Field(sa_column=Column(Text, nullable=False))
    said_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    said_at_inferred: bool = Field(
        default=False, nullable=False, sa_column_kwargs={"server_default": text("false")}
    )
    seq: int = Field(nullable=False)
