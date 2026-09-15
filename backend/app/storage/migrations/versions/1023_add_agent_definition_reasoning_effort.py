"""add reasoning effort policy to agent definitions

Revision ID: 1023
Revises: 1022
Create Date: 2026-09-15 10:00:00.000000

为 agent_definitions 增加智能体级思考强度策略列。服务端默认 inherit，
使旧数据升级后保持既有行为（主智能体沿用会话基础默认，子智能体继承父级）。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1023"
down_revision: Union[str, Sequence[str], None] = "1022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_definitions",
        sa.Column(
            "reasoning_effort",
            sa.String(length=10),
            nullable=False,
            server_default="inherit",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_definitions", "reasoning_effort")
