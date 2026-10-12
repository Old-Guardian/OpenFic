import importlib
from datetime import datetime

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text

from app.storage.database import ALEMBIC_INI_PATH
from app.storage.models.llm_audit_log import LLMAuditLog


def test_dashboard_metadata_indexes_migrate_existing_records(tmp_path, monkeypatch):
    migration = importlib.import_module(
        "app.storage.migrations.versions.1029_dashboard_metadata_indexes"
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    try:
        with engine.begin() as connection:
            columns = {name for _, names in migration.INDEXES for name in names}
            columns.update(migration.DETAIL_COLUMNS)
            connection.execute(
                text(
                    "CREATE TABLE agent_audit_logs ("
                    + ", ".join(f"{name} TEXT" for name in sorted(columns))
                    + ")"
                )
            )
            prompt = "stored prompt" * 1000
            connection.execute(
                text(
                    "INSERT INTO agent_audit_logs (id, request_messages) VALUES ('existing', :prompt)"
                ),
                {"prompt": prompt},
            )
            monkeypatch.setattr(
                migration, "op", Operations(MigrationContext.configure(connection))
            )
            migration.upgrade()
            expected_indexes = dict(migration.INDEXES)
            expected_indexes[migration.DETAIL_STORAGE_INDEX] = ["has_details", "detail_bytes"]
            assert {
                i["name"]: i["column_names"]
                for i in inspect(connection).get_indexes("agent_audit_logs")
            } == expected_indexes
            assert connection.execute(
                text("SELECT has_details, detail_bytes FROM agent_audit_logs WHERE id = 'existing'")
            ).one() == (1, len(prompt.encode("utf-8")))
            model_indexes = {
                i.name: [c.name for c in i.columns]
                for i in LLMAuditLog.__table__.indexes
            }
            for name, names in expected_indexes.items():
                assert model_indexes[name] == names
            migration.downgrade()
            assert not inspect(connection).get_indexes("agent_audit_logs")
            assert connection.execute(
                text("SELECT id, request_messages FROM agent_audit_logs")
            ).one() == ("existing", "stored prompt" * 1000)
    finally:
        engine.dispose()


def test_deployed_1028_dashboard_upgrade_round_trip_preserves_data(tmp_path, monkeypatch):
    """Exercise Alembic on a deployed schema, including SQLite's downgrade table rebuild."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr("app.settings.BACKEND_DATA_DIR", data_dir)
    config = Config(str(ALEMBIC_INI_PATH))
    command.upgrade(config, "1028")
    engine = create_engine(f"sqlite:///{data_dir / 'openfic.db'}")
    details = {
        "request_messages": '[{"role":"user","content":"历史中文提示词"}]',
        "tool_references": '[{"name":"write_chapter"}]',
        "response_content": "保留输出 🌟",
        "response_tool_calls": "[]",
        "tool_call_results": "",
        "extra_data": '{"source":"原有记录"}',
    }
    expected_bytes = sum(len(value.encode("utf-8")) for value in details.values())
    try:
        audit = Table("agent_audit_logs", MetaData(), autoload_with=engine)
        assert "has_details" not in audit.c and "detail_bytes" not in audit.c
        legacy_columns = list(audit.c)
        legacy_indexes = {index["name"] for index in inspect(engine).get_indexes(audit.name)}
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO projects (id, title, description, word_count, chapter_count, "
                "created_at, updated_at) VALUES ('project-1', 'Novel', '', 0, 0, "
                "'2026-10-12', '2026-10-12')"
            ))
            connection.execute(text(
                "INSERT INTO model_providers (id, name, url, api_key_encrypted, "
                "custom_headers_encrypted, provider_type, is_builtin, provider_config, "
                "credentials_encrypted, created_at, updated_at) VALUES ('provider-1', "
                "'Vertex', 'https://vertex.googleapis.com', 'key-cipher', '', 'google-vertex', "
                "0, '{\"project_id\":\"existing-project\"}', 'shared-cipher', '2026-10-12', "
                "'2026-10-12')"
            ))
            connection.execute(text(
                "INSERT INTO model_provider_oauth_registrations "
                "(provider_type, issuer, client_id, subject, email, provider_id) VALUES "
                "('openai-codex', 'https://auth.openai.com', 'client-1', 'subject-1', "
                "'author@example.com', NULL)"
            ))
            for record_id, payload in (("with-details", details), ("without-details", {})):
                connection.execute(audit.insert().values(
                    id=record_id, created_at=datetime(2026, 10, 12), project_id="project-1",
                    category="agent", operation="writer", model_id="existing-model",
                    tokens_input=10, tokens_output=5, tokens_total=15, token_cache=0,
                    status="success", tool_calls_count=0, tool_calls_success_count=0,
                    tool_calls_failed_count=0, **payload,
                ))

        def snapshot():
            with engine.connect() as connection:
                return (
                    connection.execute(select(*legacy_columns).order_by(audit.c.id)).all(),
                    connection.execute(text(
                        "SELECT provider_config, credentials_encrypted FROM model_providers"
                    )).all(),
                    connection.execute(text(
                        "SELECT * FROM model_provider_oauth_registrations"
                    )).all(),
                )

        before = snapshot()
        migration = importlib.import_module(
            "app.storage.migrations.versions.1029_dashboard_metadata_indexes"
        )
        new_indexes = {name for name, _columns in migration.INDEXES} | {
            migration.DETAIL_STORAGE_INDEX
        }

        def assert_upgraded():
            assert snapshot() == before
            assert legacy_indexes | new_indexes <= {
                index["name"] for index in inspect(engine).get_indexes(audit.name)
            }
            with engine.connect() as connection:
                assert connection.execute(text(
                    "SELECT version_num FROM alembic_version"
                )).scalar_one() == "1029"
                assert connection.execute(text(
                    "SELECT id, has_details, detail_bytes FROM agent_audit_logs ORDER BY id"
                )).all() == [("with-details", 1, expected_bytes), ("without-details", 0, 0)]

        command.upgrade(config, "head")
        assert_upgraded()
        command.downgrade(config, "1028")
        assert snapshot() == before
        assert {column["name"] for column in inspect(engine).get_columns(audit.name)} == {
            column.name for column in legacy_columns
        }
        assert {index["name"] for index in inspect(engine).get_indexes(audit.name)} == legacy_indexes
        command.upgrade(config, "head")
        assert_upgraded()
    finally:
        engine.dispose()
