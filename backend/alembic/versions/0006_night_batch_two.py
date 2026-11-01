"""夜间三件事：材料可忽略 / 生日可跨年 / 提醒能说明白为什么没发

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02 03:50:00

对应计划：夜间开发 B2

三处改动，都是**只加不删、可空**，老数据不受影响：

1. raw_inputs.status 增加 'ignored'
   投喂进来一张"归属待定"的图，用户想直接扔掉。没有这个状态就只能留着，
   列表里永远挂着一条处理不了的。约束之前只存在于模型里（库里没建），
   这次一并补上，让模型和库说同一句话。

2. 已有 birthday 事件的 rrule 回填 FREQ=YEARLY
   生日以前抽出的是"单日事件"，今年过完，明年日历和提醒同时消失。
   新写入的由 services/extract.py 负责，这里只管历史行。
   只填 rrule 为空的行，不覆盖用户/代码已经设过的规则。

3. reminders.last_error
   没配推送通道时提醒会失败。以前只把 status 写成 failed，
   用户既看不到内容也不知道为什么没发。加一列存原因，B3 起写进来。

downgrade 的取舍：前两列/约束照常回滚；第 2 步的回填**不逆操作**（不把
rrule 清空）—— 因为分不清哪条是这次填的、哪条本来就有，误清会把用户
真正设过的重复规则一起删掉。回填本身是纯增量，留着无害。
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None

RAW_STATUSES = ("pending", "processing", "done", "failed", "ignored")


def upgrade() -> None:
    # 1. 材料可以是「已忽略」
    #    约束在库里本来就存在（0001 建的），只是没带 'ignored'。
    #    先删再加，写成幂等的样子：直接 ADD 会在有约束的库上报 DuplicateObject。
    op.execute("ALTER TABLE raw_inputs DROP CONSTRAINT IF EXISTS ck_raw_status")
    op.execute(
        "ALTER TABLE raw_inputs ADD CONSTRAINT ck_raw_status "
        f"CHECK (status IN {RAW_STATUSES})"
    )

    # 2. 生日是每年重复的，不是一次性
    op.execute(
        "UPDATE events SET rrule = 'FREQ=YEARLY' "
        "WHERE type = 'birthday' AND rrule IS NULL"
    )

    # 3. 提醒失败要有原因
    op.add_column("reminders", sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("reminders", "last_error")
    op.execute("ALTER TABLE raw_inputs DROP CONSTRAINT IF EXISTS ck_raw_status")
