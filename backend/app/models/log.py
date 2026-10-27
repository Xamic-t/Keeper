"""extraction_logs：抽取审计。

对应文档：docs/06-数据库设计.md 5.13
这张表不是工程需要，是答辩物证 —— 评审问"AI 到底做了什么"，现场调一条出来看。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Float, Index, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class ExtractionLog(SQLModel, table=True):
    """一次抽取的完整记录：模型、prompt 版本、原始输出、用户处置。"""

    __tablename__ = "extraction_logs"
    __table_args__ = (
        Index("ix_extlog_raw", "raw_input_id"),
        Index("ix_extlog_user_time", "user_id", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False)
    raw_input_id: int | None = Field(default=None, foreign_key="raw_inputs.id", ondelete="CASCADE")
    model_name: str | None = Field(default=None, max_length=50)
    prompt_version: str | None = Field(default=None, max_length=20)
    raw_output: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    parsed_output: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    confidence_avg: float | None = Field(default=None, sa_column=Column(Float))
    user_action: str | None = Field(default=None, max_length=20)
    user_edits: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    latency_ms: int | None = Field(default=None, sa_column=Column(Integer))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
