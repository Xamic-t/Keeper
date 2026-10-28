"""notification_channels：推送通道。

对应文档：docs/06-数据库设计.md 5.14（W9 新增这张表）

为什么单独一张表而不是只读 .env：
  一个用户可能同时想配企微群 + PushPlus 兜底，且要能改优先级、能停用。
  .env 是部署级配置，改一次要重启；这张表是用户级配置，改完立刻生效。

payload 里存敏感信息（webhook URL / token）。**日志里绝不打印它。**
"""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Column, DateTime, Index, SmallInteger, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class NotificationChannel(SQLModel, table=True):
    """一条推送通道。同一用户多条时按 priority 从小到大尝试。"""

    __tablename__ = "notification_channels"
    __table_args__ = (
        CheckConstraint("kind IN ('wecom', 'pushplus')", name="ck_channel_kind"),
        Index("ix_channel_user_priority", "user_id", "priority"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False)
    kind: str = Field(max_length=20, nullable=False)
    name: str = Field(max_length=50, nullable=False)
    # {"webhook": "..."} 或 {"token": "..."}
    config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    priority: int = Field(
        default=100, sa_column=Column(SmallInteger, nullable=False, server_default=text("100"))
    )
    is_active: bool = Field(
        default=True, nullable=False, sa_column_kwargs={"server_default": text("true")}
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
