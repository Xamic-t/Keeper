"""raw_inputs 记录原文件名

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-01 18:45:00

投喂页原先只能显示「截图 / 一句话」，因为库里根本没存名字。
用户要求列表里能看到原始文件名（或退化成时间），所以补一列。

只存文件名不存路径：路径是按 user_id/raw_input_id 生成的，
名字只用于展示，不需要能反查回磁盘。
老数据为 NULL，前端退化显示投喂时间。
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "raw_inputs",
        sa.Column("original_filename", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("raw_inputs", "original_filename")
