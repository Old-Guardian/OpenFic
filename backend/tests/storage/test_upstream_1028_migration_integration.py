# -*- coding: utf-8 -*-
"""集成测试：验证 1027 与 1028 迁移在隔离数据库上的完整升级与降级生命周期。

验证目标（实施计划 A）：
1. Alembic heads 唯一且为 1028，迁移链 1025 -> 1026 -> 1027 -> 1028 连续。
2. 空库可直接升级到 head (1028)，包含所有既有特性表/列、OAuth 注册表与 1028 索引。
3. 针对已部署的 fork 1026 数据库：
   - 保留既有 Vertex 配置与加密凭据 (model_providers.provider_config, credentials_encrypted)
   - 保留 Agent 定义推理强度 (agent_definitions.reasoning_effort)
   - 保留知识与角色别名 (world_info_entry_aliases, character_aliases, aliases_json)
   - 保留附件提取内容 (agent_attachments.content, content_length)
   - 保留角色图谱坐标与关系 (characters.graph_x, graph_y, character_relationships)
4. 1028 -> 1026 降级只删除 OAuth 注册表及 1028 索引，绝对不删除共享凭据列 credentials_encrypted，所有既有数据完好。
5. 降级后再升级回 1028 依然正常且数据完好。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.storage.database import ALEMBIC_INI_PATH

EXPECTED_HEAD = "1028"
FORK_HEAD = "1026"

EXPECTED_1028_INDEXES = {
    ("agent_run_messages", "ix_agent_run_messages_session_id_seq"),
    ("agent_child_runs", "ix_agent_child_runs_parent_session_id_created_at"),
    ("agent_child_runs", "ix_agent_child_runs_parent_session_id_parent_revision_id"),
    ("agent_child_run_requests", "ix_agent_child_run_requests_child_run_id_seq"),
    ("revisions", "ix_revisions_agent_session_id_type_seq_created_at"),
}


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr("app.settings.BACKEND_DATA_DIR", data_dir)
    return data_dir / "openfic.db"


def _config() -> Config:
    return Config(str(ALEMBIC_INI_PATH))


def _engine(database: Path):
    return create_engine(f"sqlite:///{database.as_posix()}")


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table)}


def test_migration_chain_is_unique_and_linear() -> None:
    scripts = ScriptDirectory.from_config(_config())
    assert scripts.get_heads() == [EXPECTED_HEAD]

    rev_1028 = scripts.get_revision("1028")
    assert rev_1028.down_revision == "1027"

    rev_1027 = scripts.get_revision("1027")
    assert rev_1027.down_revision == "1026"

    rev_1026 = scripts.get_revision("1026")
    assert rev_1026.down_revision == "1025"

    rev_1025 = scripts.get_revision("1025")
    assert rev_1025.down_revision == "1024"


def test_empty_database_upgrades_to_head_1028(database: Path) -> None:
    command.upgrade(_config(), "head")

    engine = _engine(database)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())

        # 检查 1027 表和索引
        assert "model_provider_oauth_registrations" in table_names
        assert "ix_model_provider_oauth_registrations_provider_id" in _indexes(
            inspector, "model_provider_oauth_registrations"
        )
        assert {"provider_type", "issuer", "client_id", "subject", "email", "provider_id"} <= _columns(
            inspector, "model_provider_oauth_registrations"
        )

        # 检查 1028 索引
        for table, index_name in EXPECTED_1028_INDEXES:
            assert index_name in _indexes(inspector, table), f"Missing index {index_name} on {table}"

        # 检查 fork 既有特性结构依然完整
        assert {"provider_config", "credentials_encrypted"} <= _columns(inspector, "model_providers")
        assert "reasoning_effort" in _columns(inspector, "agent_definitions")
        assert {"world_info_entry_aliases", "character_aliases", "character_relationships"} <= table_names
        assert {"content", "content_length"} <= _columns(inspector, "agent_attachments")
        assert {"graph_x", "graph_y"} <= _columns(inspector, "characters")

        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == EXPECTED_HEAD
    finally:
        engine.dispose()


def test_deployed_fork_1026_database_upgrades_downgrades_and_reupgrades_cleanly(database: Path) -> None:
    # 1. 模拟已有 fork 1026 数据库
    command.upgrade(_config(), FORK_HEAD)

    engine = _engine(database)
    try:
        with engine.begin() as conn:
            # 基础数据
            conn.execute(
                text(
                    "INSERT INTO projects (id, title, description, word_count, chapter_count, created_at, updated_at) "
                    "VALUES ('proj-1', 'Fork Novel', 'Fork description', 100, 1, '2026-10-01', '2026-10-01')"
                )
            )
            # Vertex 提供商与加密凭据 (1022)
            conn.execute(
                text(
                    "INSERT INTO model_providers "
                    "(id, name, url, api_key_encrypted, custom_headers_encrypted, provider_type, is_builtin, "
                    "provider_config, credentials_encrypted, created_at, updated_at) VALUES "
                    "('prov-vertex', 'Vertex AI', 'https://vertex.googleapis.com', 'key-cipher', '', 'google-vertex', 0, "
                    "'{\"project_id\": \"test-proj\", \"location\": \"us-central1\"}', 'vertex-cipher-secret', '2026-10-01', '2026-10-01')"
                )
            )
            # 智能体定义与推理策略 (1023)
            conn.execute(
                text(
                    "INSERT INTO agent_definitions "
                    "(id, key, display_name, description, kind, prompt_agent_name, model_id, enabled_tool_categories, "
                    "enabled_skills, metadata_json, enabled, order_index, source, delegatable_agents, reasoning_effort, "
                    "created_at, updated_at) VALUES "
                    "('def-custom', 'custom-agent', 'Custom', '', 'primary', 'custom', NULL, '[]', '[]', '{}', 1, 0, 'custom', '[]', 'high', "
                    "'2026-10-01', '2026-10-01')"
                )
            )
            # 知识实体与别名 (1024)
            conn.execute(
                text(
                    "INSERT INTO world_info (id, project_id, name, description, created_at, updated_at) "
                    "VALUES ('wi-1', 'proj-1', 'Lore', '', '2026-10-01', '2026-10-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO world_info_entries (id, world_info_id, uid, name, \"order\", content, token_count, is_enabled, created_at, updated_at) "
                    "VALUES ('wie-1', 'wi-1', 1, 'Excalibur', 0, 'Holy sword', 10, 1, '2026-10-01', '2026-10-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO world_info_entry_aliases (id, entry_id, alias, normalized_alias, position) "
                    "VALUES ('wiea-1', 'wie-1', 'Caliburn', 'caliburn', 0)"
                )
            )
            # 角色与图谱坐标、别名 (1024, 1026)
            conn.execute(
                text(
                    "INSERT INTO characters (id, project_id, name, description, image_path, is_favorited, graph_x, graph_y, created_at, updated_at) "
                    "VALUES ('char-1', 'proj-1', 'Arthur', 'King', NULL, 1, 120.5, 340.5, '2026-10-01', '2026-10-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO characters (id, project_id, name, description, image_path, is_favorited, graph_x, graph_y, created_at, updated_at) "
                    "VALUES ('char-2', 'proj-1', 'Merlin', 'Wizard', NULL, 0, 220.0, 440.0, '2026-10-01', '2026-10-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO character_aliases (id, character_id, alias, normalized_alias, position) "
                    "VALUES ('ca-1', 'char-1', 'Artoria', 'artoria', 0)"
                )
            )
            # 角色关系 (1026)
            conn.execute(
                text(
                    "INSERT INTO character_relationships (id, project_id, source_character_id, target_character_id, name, description) "
                    "VALUES ('rel-1', 'proj-1', 'char-1', 'char-2', 'mentor', 'Merlin guided Arthur')"
                )
            )
            # 附件内容与长度 (1025)
            conn.execute(
                text(
                    "INSERT INTO tasks (id, project_id, title, mode, token_input, token_output, token_cache, context_input_tokens, is_running, is_favorited, created_at, updated_at) "
                    "VALUES ('task-1', 'proj-1', 'Task', 'chat', 0, 0, 0, 0, 0, 0, '2026-10-01', '2026-10-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO agent_attachments (id, session_id, task_id, project_id, storage_name, file_name, mime_type, size_bytes, width, height, content, content_length, created_at) "
                    "VALUES ('att-1', 'sess-1', 'task-1', 'proj-1', 'store-1', 'doc.pdf', 'application/pdf', 1024, 0, 0, 'Extracted pdf text', 18, '2026-10-01')"
                )
            )
    finally:
        engine.dispose()

    # 2. 从已部署的 1026 升级到 head (1028)
    command.upgrade(_config(), "head")

    engine = _engine(database)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == EXPECTED_HEAD

            # 核对 Vertex 配置与加密凭据保持完整
            provider_row = conn.execute(
                text("SELECT provider_config, credentials_encrypted FROM model_providers WHERE id = 'prov-vertex'")
            ).one()
            assert provider_row == ('{"project_id": "test-proj", "location": "us-central1"}', "vertex-cipher-secret")

            # 核对推理策略
            agent_row = conn.execute(
                text("SELECT reasoning_effort FROM agent_definitions WHERE id = 'def-custom'")
            ).scalar_one()
            assert agent_row == "high"

            # 核对知识别名
            alias_row = conn.execute(
                text("SELECT alias, normalized_alias FROM world_info_entry_aliases WHERE id = 'wiea-1'")
            ).one()
            assert alias_row == ("Caliburn", "caliburn")

            # 核对角色图谱坐标与角色别名
            char_row = conn.execute(
                text("SELECT name, graph_x, graph_y FROM characters WHERE id = 'char-1'")
            ).one()
            assert char_row == ("Arthur", 120.5, 340.5)

            char_alias_row = conn.execute(
                text("SELECT alias FROM character_aliases WHERE id = 'ca-1'")
            ).scalar_one()
            assert char_alias_row == "Artoria"

            # 核对角色关系
            rel_row = conn.execute(
                text("SELECT name, description FROM character_relationships WHERE id = 'rel-1'")
            ).one()
            assert rel_row == ("mentor", "Merlin guided Arthur")

            # 核对附件提取文本与长度
            att_row = conn.execute(
                text("SELECT content, content_length FROM agent_attachments WHERE id = 'att-1'")
            ).one()
            assert att_row == ("Extracted pdf text", 18)

        # 验证新 OAuth 表可正常插入数据
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO model_provider_oauth_registrations (provider_type, issuer, client_id, subject, email, provider_id) "
                    "VALUES ('openai-codex', 'https://auth.openai.com', 'client-1', 'sub-1', 'test@example.com', 'prov-oauth')"
                )
            )

        with engine.connect() as conn:
            oauth_count = conn.execute(
                text("SELECT COUNT(*) FROM model_provider_oauth_registrations")
            ).scalar_one()
            assert oauth_count == 1
    finally:
        engine.dispose()

    # 3. 降级 1028 -> 1026
    command.downgrade(_config(), FORK_HEAD)

    engine = _engine(database)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())

        # model_provider_oauth_registrations 表应被删除
        assert "model_provider_oauth_registrations" not in table_names

        # 1028 的 5 个索引应被删除
        for table, index_name in EXPECTED_1028_INDEXES:
            assert index_name not in _indexes(inspector, table), f"Index {index_name} should have been dropped on {table}"

        # 关键验证：共享凭据列 credentials_encrypted 绝对不能被误删！
        mp_columns = _columns(inspector, "model_providers")
        assert "credentials_encrypted" in mp_columns
        assert "provider_config" in mp_columns

        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == FORK_HEAD

            # Vertex 凭据与配置依然完好
            provider_row = conn.execute(
                text("SELECT provider_config, credentials_encrypted FROM model_providers WHERE id = 'prov-vertex'")
            ).one()
            assert provider_row == ('{"project_id": "test-proj", "location": "us-central1"}', "vertex-cipher-secret")

            # 附件内容依然完好
            att_row = conn.execute(
                text("SELECT content, content_length FROM agent_attachments WHERE id = 'att-1'")
            ).one()
            assert att_row == ("Extracted pdf text", 18)

            # 角色图谱与关系依然完好
            char_row = conn.execute(
                text("SELECT name, graph_x, graph_y FROM characters WHERE id = 'char-1'")
            ).one()
            assert char_row == ("Arthur", 120.5, 340.5)

            rel_row = conn.execute(
                text("SELECT name, description FROM character_relationships WHERE id = 'rel-1'")
            ).one()
            assert rel_row == ("mentor", "Merlin guided Arthur")

            # 别名依然完好
            assert conn.execute(
                text("SELECT alias FROM character_aliases WHERE id = 'ca-1'")
            ).scalar_one() == "Artoria"
    finally:
        engine.dispose()

    # 4. 从 1026 重新升级回 head (1028)
    command.upgrade(_config(), "head")

    engine = _engine(database)
    try:
        inspector = inspect(engine)
        assert "model_provider_oauth_registrations" in set(inspector.get_table_names())
        for table, index_name in EXPECTED_1028_INDEXES:
            assert index_name in _indexes(inspector, table)

        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == EXPECTED_HEAD

            # 数据在二次升级后依然完好
            provider_row = conn.execute(
                text("SELECT provider_config, credentials_encrypted FROM model_providers WHERE id = 'prov-vertex'")
            ).one()
            assert provider_row == ('{"project_id": "test-proj", "location": "us-central1"}', "vertex-cipher-secret")

            att_row = conn.execute(
                text("SELECT content, content_length FROM agent_attachments WHERE id = 'att-1'")
            ).one()
            assert att_row == ("Extracted pdf text", 18)

            char_row = conn.execute(
                text("SELECT name, graph_x, graph_y FROM characters WHERE id = 'char-1'")
            ).one()
            assert char_row == ("Arthur", 120.5, 340.5)
    finally:
        engine.dispose()
