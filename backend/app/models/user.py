"""users 表：账号体系。

对应文档：docs/06-数据库设计.md 5.2
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, func
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    """用户账号。V1 用统一登录名，不做短信/邮件验证。"""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'member')", name="ck_users_role"),
        CheckConstraint("failed_attempts >= 0", name="ck_users_failed_attempts"),
    )

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(max_length=100, unique=True, index=True)
    display_name: str | None = Field(default=None, max_length=100)
    password_hash: str = Field(max_length=255)
    role: str = Field(default="member", max_length=20)
    is_active: bool = Field(default=True)
    failed_attempts: int = Field(default=0)
    locked_until: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    last_login_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    keep_original_images: bool = Field(default=True)
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
