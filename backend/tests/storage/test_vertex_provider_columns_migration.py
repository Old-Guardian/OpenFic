# -*- coding: utf-8 -*-
"""
1022 迁移测试：model_providers 新增 Vertex 配置与凭据列。

验证旧数据升级后保持原值，新列默认值正确（provider_config='{}'、
credentials_encrypted=''），downgrade 可回退。
"""

import importlib
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection


migration = importlib.import_module(
    "app.storage.migrations.versions.1022_add_vertex_provider_config_and_credentials"
)
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "app" / "storage" / "migrations"


def test_migration_graph_has_single_vertex_head() -> None:
    """确保新增迁移不会与现有 revision 冲突或产生分叉。"""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))

    assert ScriptDirectory.from_config(config).get_heads() == ["1022"]


def _create_legacy_schema(connection: Connection) -> None:
    """升级前的 model_providers 结构（1021 之后、1022 之前）。"""
    connection.execute(
        text(
            "CREATE TABLE model_providers ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, "
            "api_key_encrypted TEXT NOT NULL, "
            "custom_headers_encrypted TEXT NOT NULL, "
            "provider_type TEXT NOT NULL, is_builtin BOOLEAN NOT NULL, "
            "created_at TIMESTAMP, updated_at TIMESTAMP)"
        )
    )


def _insert_legacy_rows(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO model_providers "
            "(id, name, url, api_key_encrypted, custom_headers_encrypted, "
            "provider_type, is_builtin, created_at, updated_at) VALUES "
            "('legacy-openai', 'OpenAI', 'https://api.openai.com/v1', "
            "'legacy-cipher', '', 'openai', 0, '2026-01-01', '2026-01-01'), "
            "('legacy-vertex', 'Old Vertex', 'https://old-vertex.example', "
            "'legacy-key-cipher', '', 'google-vertex', 0, "
            "'2026-01-01', '2026-01-01')"
        )
    )


def test_upgrade_adds_columns_with_defaults_and_keeps_existing_rows() -> None:
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
                "SELECT id, name, url, api_key_encrypted, provider_config, "
                "credentials_encrypted FROM model_providers ORDER BY id"
            )
        ).mappings().all()

    # 旧数据保持原值，新列使用与实体一致的默认值。
    assert rows == [
        {
            "id": "legacy-openai",
            "name": "OpenAI",
            "url": "https://api.openai.com/v1",
            "api_key_encrypted": "legacy-cipher",
            "provider_config": "{}",
            "credentials_encrypted": "",
        },
        {
            "id": "legacy-vertex",
            "name": "Old Vertex",
            "url": "https://old-vertex.example",
            "api_key_encrypted": "legacy-key-cipher",
            "provider_config": "{}",
            "credentials_encrypted": "",
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
            text("PRAGMA table_info(model_providers)")
        ).mappings().all()

    column_names = {column["name"] for column in columns}
    assert "provider_config" not in column_names
    assert "credentials_encrypted" not in column_names
    # 原有列不受影响。
    assert {"id", "url", "api_key_encrypted", "provider_type"} <= column_names
