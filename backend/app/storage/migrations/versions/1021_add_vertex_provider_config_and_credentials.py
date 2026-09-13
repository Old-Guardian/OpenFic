"""add vertex provider config and credentials to model providers

Revision ID: 1021
Revises: 1020
Create Date: 2026-09-13 10:00:00.000000

为 google-vertex 原生接入增加非敏感配置（provider_config）与
加密凭据（credentials_encrypted）两列。默认值与实体定义保持一致：
provider_config 为空 JSON 对象，credentials_encrypted 为空字符串。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1021"
down_revision: Union[str, Sequence[str], None] = "1020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "model_providers",
        sa.Column(
            "provider_config",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "model_providers",
        sa.Column(
            "credentials_encrypted",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("model_providers", "credentials_encrypted")
    op.drop_column("model_providers", "provider_config")
