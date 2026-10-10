"""Add reusable OAuth registrations after fork 1026 head.

Revision ID: 1027
Revises: 1026
Create Date: 2026-10-02 00:00:00.000000

Upstream #429 added credentials_encrypted and model_provider_oauth_registrations.
In this fork, credentials_encrypted was already introduced in 1022 for Vertex.
This migration only creates the OAuth registration table and its index,
reusing the existing shared credentials_encrypted column.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1027"
down_revision: Union[str, Sequence[str], None] = "1026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_provider_oauth_registrations",
        sa.Column("provider_type", sa.String(255), nullable=False),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("provider_id", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("provider_type", "issuer", "client_id"),
    )
    op.create_index(
        "ix_model_provider_oauth_registrations_provider_id",
        "model_provider_oauth_registrations",
        ["provider_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_provider_oauth_registrations_provider_id",
        table_name="model_provider_oauth_registrations",
    )
    op.drop_table("model_provider_oauth_registrations")
