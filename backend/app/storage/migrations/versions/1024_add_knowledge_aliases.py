"""add world entry and character alias tables

Revision ID: 1024
Revises: 1023
Create Date: 2026-09-18 00:00:00.000000

新增世界书条目与角色的别名字表，并在两类 revision snapshot 上增加
``aliases_json`` 列。旧快照该列为 NULL，按当时的空别名列表解释；
本迁移不改写既有实体 ID、名称、正文或顺序。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1024"
down_revision: Union[str, Sequence[str], None] = "1023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "world_info_entry_aliases",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("entry_id", sa.String(), nullable=False),
        sa.Column("alias", sa.String(length=100), nullable=False),
        sa.Column("normalized_alias", sa.String(length=100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["entry_id"], ["world_info_entries.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entry_id", "normalized_alias", name="uq_world_info_entry_alias_target"
        ),
    )
    op.create_index(
        op.f("ix_world_info_entry_aliases_entry_id"),
        "world_info_entry_aliases",
        ["entry_id"],
        unique=False,
    )

    op.create_table(
        "character_aliases",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("character_id", sa.String(), nullable=False),
        sa.Column("alias", sa.String(length=100), nullable=False),
        sa.Column("normalized_alias", sa.String(length=100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "character_id", "normalized_alias", name="uq_character_alias_target"
        ),
    )
    op.create_index(
        op.f("ix_character_aliases_character_id"),
        "character_aliases",
        ["character_id"],
        unique=False,
    )

    op.add_column(
        "revision_world_entry_snapshots",
        sa.Column("aliases_json", sa.String(), nullable=True),
    )
    op.add_column(
        "revision_character_snapshots",
        sa.Column("aliases_json", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("revision_character_snapshots", "aliases_json")
    op.drop_column("revision_world_entry_snapshots", "aliases_json")

    op.drop_index(
        op.f("ix_character_aliases_character_id"), table_name="character_aliases"
    )
    op.drop_table("character_aliases")

    op.drop_index(
        op.f("ix_world_info_entry_aliases_entry_id"),
        table_name="world_info_entry_aliases",
    )
    op.drop_table("world_info_entry_aliases")
