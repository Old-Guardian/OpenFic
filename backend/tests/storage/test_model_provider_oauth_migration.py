# -*- coding: utf-8 -*-
"""1027 迁移测试：model_provider_oauth_registrations 表与索引创建。

验证：
1. 1027 迁移在已包含 credentials_encrypted 的结构上可正常 upgrade。
2. 建立主键约束 (provider_type, issuer, client_id) 及 provider_id 索引。
3. downgrade 时只删除 OAuth 注册表，不删除 model_providers 的 credentials_encrypted 共享列。
"""

from __future__ import annotations

import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


migration = importlib.import_module("app.storage.migrations.versions.1027_add_model_provider_oauth")


def test_provider_oauth_migration_round_trip(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    try:
        with engine.begin() as connection:
            # 模拟 1026 状态：model_providers 已包含 1022 创建的 credentials_encrypted 列
            connection.execute(
                text(
                    "CREATE TABLE model_providers ("
                    "id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                    "api_key_encrypted TEXT NOT NULL, credentials_encrypted TEXT NOT NULL DEFAULT '')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO model_providers VALUES "
                    "('existing', 'Existing', 'encrypted-api-key', 'encrypted-vertex-credentials')"
                )
            )
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
            migration.upgrade()

            table = "model_provider_oauth_registrations"
            assert table in inspect(connection).get_table_names()
            assert set(inspect(connection).get_pk_constraint(table)["constrained_columns"]) == {
                "provider_type",
                "issuer",
                "client_id",
            }
            indexes = {idx["name"]: idx["column_names"] for idx in inspect(connection).get_indexes(table)}
            assert "ix_model_provider_oauth_registrations_provider_id" in indexes
            assert indexes["ix_model_provider_oauth_registrations_provider_id"] == ["provider_id"]

            for provider_type, issuer in [
                ("one", "https://first.example"),
                ("two", "https://first.example"),
                ("one", "https://second.example"),
            ]:
                connection.execute(
                    text(
                        f"INSERT INTO {table} (provider_type, issuer, client_id) "
                        "VALUES (:provider_type, :issuer, 'same-client')"
                    ),
                    {"provider_type": provider_type, "issuer": issuer},
                )

            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        f"INSERT INTO {table} (provider_type, issuer, client_id) "
                        "VALUES ('one', 'https://first.example', 'same-client')"
                    )
                )

            # 模拟更新 credentials_encrypted
            connection.execute(
                text("UPDATE model_providers SET credentials_encrypted = 'updated-credentials'")
            )

            migration.downgrade()

            # 降级后，model_provider_oauth_registrations 表被删除
            assert table not in inspect(connection).get_table_names()
            # 但共享的 credentials_encrypted 列依然保留
            columns = {column["name"] for column in inspect(connection).get_columns("model_providers")}
            assert "credentials_encrypted" in columns
            assert connection.execute(
                text("SELECT id, name, api_key_encrypted, credentials_encrypted FROM model_providers")
            ).one() == ("existing", "Existing", "encrypted-api-key", "updated-credentials")

            # 重新 upgrade
            migration.upgrade()
            assert connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 0
            assert connection.execute(
                text("SELECT credentials_encrypted FROM model_providers")
            ).scalar_one() == "updated-credentials"
    finally:
        engine.dispose()
