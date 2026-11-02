"""chat_messages 表：助理对话记录。

对应文档：docs/06-数据库设计.md 5.10（2026-11-03 新增）

为什么存库而不是放前端内存：
  ① 刷新/换设备还能接着聊（用户预期"像微信那样"）
  ② 助手执行过的动作要留痕——"它什么时候给我建了这条待办"能查
  ③ 前端存就得整段回传给模型，token 白花；存库只取最近 N 轮

三条约定的字段：
  · `image_raw_input_id` 指向 raw_inputs：那张图本身走既有投喂流水线，
    消息只记一个引用。图被删（用户关了"保留原图"）时置 NULL，消息还在
  · `actions` 是**已执行**的动作列表（不是"模型想做什么"），
    每条 {type, ok, summary, ref}，ref 是生成的 todo/event/fact id
  · 不给 messages 加编辑/删除单条的能力：V1 只有"清空历史"
"""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class ChatMessage(SQLModel, table=True):
    """一条对话消息（用户或助理）。"""

    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_role"),
        # 取最近 N 条是唯一高频查询，按 (user_id, created_at) 建索引
        Index("ix_chat_user_created", "user_id", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False)
    # 'user' | 'assistant'
    role: str = Field(max_length=16, nullable=False)
    content: str = Field(
        default="", sa_column=Column(Text, nullable=False, server_default=text("''"))
    )
    # 用户这一轮附的图（raw_inputs.id）。图没了不影响消息本身
    image_raw_input_id: int | None = Field(
        default=None, foreign_key="raw_inputs.id", ondelete="SET NULL"
    )
    actions: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
