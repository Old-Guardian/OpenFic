# -*- coding: utf-8 -*-
"""
1023 迁移测试：agent_definitions 新增 reasoning_effort 策略列。

验证旧数据升级后保持原值、新列服务端默认值为 inherit，downgrade 可回退。
"""

import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection


migration = importlib.import_module(
    "app.storage.migrations.versions.1023_add_agent_definition_reasoning_effort"
)


def _create_legacy_schema(connection: Connection) -> None:
    """升级前的 agent_definitions 结构（1022 之后、1023 之前）。"""
    connection.execute(
        text(
            "CREATE TABLE agent_definitions ("
            "id TEXT PRIMARY KEY, key TEXT NOT NULL, display_name TEXT NOT NULL, "
            "description TEXT NOT NULL, kind TEXT NOT NULL, "
            "prompt_agent_name TEXT NOT NULL, model_id TEXT, "
            "enabled_tool_categories JSON NOT NULL, enabled_skills JSON NOT NULL, "
            "metadata_json JSON NOT NULL, enabled BOOLEAN NOT NULL, "
            "order_index INTEGER NOT NULL, source TEXT NOT NULL, "
            "color TEXT, icon TEXT, delegatable_agents JSON NOT NULL, "
            "created_at TIMESTAMP, updated_at TIMESTAMP)"
        )
    )


def _insert_legacy_rows(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO agent_definitions "
            "(id, key, display_name, description, kind, prompt_agent_name, model_id, "
            "enabled_tool_categories, enabled_skills, metadata_json, enabled, "
            "order_index, source, color, icon, delegatable_agents, created_at, updated_at) "
            "VALUES "
            "('row-build', 'build', 'Build', 'builtin override', 'primary', 'build', "
            "'model-build', '[\"plan\"]', '[]', '{}', 1, 0, 'builtin', 'blue', "
            "'pen-tool', '[\"explore\", \"writer\"]', '2026-01-01', '2026-01-01'), "
            "('row-custom', 'custom-bot', 'Custom Bot', 'custom agent', 'subagent', "
            "'custom-bot', NULL, '[\"chapter_read\"]', '[\"skill-a\"]', '{}', 1, 5, "
            "'custom', 'green', 'sparkles', '[]', '2026-02-02', '2026-02-02')"
        )
    )


def test_upgrade_adds_inherit_default_and_keeps_existing_rows() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_legacy_schema(connection)
        _insert_legacy_rows(connection)

    with engine.begin() as connection:
        migration_context = MigrationContext.configure(connection)
        with Operations.context(migration_context):
            migration.upgrade()

        rows = connection.execute(
            text(
                "SELECT id, key, model_id, source, delegatable_agents, reasoning_effort "
                "FROM agent_definitions ORDER BY id"
            )
        ).mappings().all()

    # 旧数据保持原值，新列统一为 inherit。
    assert rows == [
        {
            "id": "row-build",
            "key": "build",
            "model_id": "model-build",
            "source": "builtin",
            "delegatable_agents": '["explore", "writer"]',
            "reasoning_effort": "inherit",
        },
        {
            "id": "row-custom",
            "key": "custom-bot",
            "model_id": None,
            "source": "custom",
            "delegatable_agents": "[]",
            "reasoning_effort": "inherit",
        },
    ]


def test_upgrade_then_downgrade_round_trip() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _create_legacy_schema(connection)

    with engine.begin() as connection:
        migration_context = MigrationContext.configure(connection)
        with Operations.context(migration_context):
            migration.upgrade()
            migration.downgrade()

        columns = connection.execute(
            text("PRAGMA table_info(agent_definitions)")
        ).mappings().all()

    column_names = {column["name"] for column in columns}
    assert "reasoning_effort" not in column_names
    # 原有列不受影响。
    assert {"id", "key", "kind", "model_id", "source", "delegatable_agents"} <= column_names
