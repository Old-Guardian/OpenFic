# -*- coding: utf-8 -*-
"""1024 迁移测试：别名表与 revision snapshot 的 aliases_json 列。

按计划要求，迁移用 Alembic 升级真实临时文件数据库，不用
``SQLModel.metadata.create_all()`` 代替。``env.py`` 从应用设置解析数据库
URL，因此测试把 ``app.settings.BACKEND_DATA_DIR`` 指向临时目录。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.storage.database import ALEMBIC_INI_PATH

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "app" / "storage" / "migrations"
LEGACY_HEAD = "1023"
HEAD = "1024"

_ALIAS_TABLES = {"world_info_entry_aliases", "character_aliases"}
_ALIAS_COLUMNS = {"id", "alias", "normalized_alias", "position"}


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """临时文件数据库；alembic env.py 会从该目录解析 sqlite URL。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr("app.settings.BACKEND_DATA_DIR", data_dir)
    return data_dir / "openfic.db"


def _config() -> Config:
    return Config(str(ALEMBIC_INI_PATH))


def _engine(database: Path):
    return create_engine(f"sqlite:///{database.as_posix()}")


def test_alias_migration_is_the_single_alembic_head() -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))

    assert ScriptDirectory.from_config(config).get_heads() == [HEAD]


def test_empty_database_upgrades_to_head_with_alias_schema(database: Path) -> None:
    command.upgrade(_config(), "head")

    engine = _engine(database)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        assert _ALIAS_TABLES <= table_names
        assert _ALIAS_COLUMNS | {"entry_id"} <= {
            column["name"] for column in inspector.get_columns("world_info_entry_aliases")
        }
        assert _ALIAS_COLUMNS | {"character_id"} <= {
            column["name"] for column in inspector.get_columns("character_aliases")
        }
        for snapshot_table in (
            "revision_world_entry_snapshots",
            "revision_character_snapshots",
        ):
            columns = {column["name"]: column for column in inspector.get_columns(snapshot_table)}
            assert "aliases_json" in columns
            assert columns["aliases_json"]["nullable"] is True
        index_names = {
            index["name"]
            for index in inspector.get_indexes("world_info_entry_aliases")
        } | {
            index["name"] for index in inspector.get_indexes("character_aliases")
        }
        assert {
            "ix_world_info_entry_aliases_entry_id",
            "ix_character_aliases_character_id",
        } <= index_names
    finally:
        engine.dispose()


def _insert_legacy_entities(connection) -> None:
    connection.execute(
        text(
            "INSERT INTO projects (id, title, description, word_count, chapter_count, "
            "cover_path, created_at, updated_at) VALUES "
            "('project-1', '项目一', '', 0, 0, NULL, '2026-01-01', '2026-01-01')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO world_info (id, project_id, name, description, created_at, updated_at) "
            "VALUES ('world-1', 'project-1', '世界书', '', '2026-01-01', '2026-01-01')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO world_info_entries (id, world_info_id, uid, name, \"order\", content, "
            "token_count, is_enabled, created_at, updated_at) VALUES "
            "('entry-1', 'world-1', 1, '燃魂术', 1, '第一行\n消耗寿命', 3, 1, "
            "'2026-01-01', '2026-01-01')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO characters (id, project_id, name, description, image_path, "
            "is_favorited, created_at, updated_at) VALUES "
            "('character-1', 'project-1', '阿甲', '角色正文', NULL, 0, "
            "'2026-01-01', '2026-01-01')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO revisions (id, project_id, message, status, revision_type, "
            "is_checkpoint, project_snapshot_title, project_snapshot_description, "
            "project_snapshot_word_count, project_snapshot_chapter_count, created_at, updated_at) "
            "VALUES ('revision-1', 'project-1', 'msg', 'applied', 'agent', 0, '标题', NULL, "
            "0, 0, '2026-01-01', '2026-01-01')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO revision_world_entry_snapshots (id, revision_id, entry_id, project_id, "
            "\"exists\", name, content, created_at, updated_at) VALUES "
            "('snapshot-w1', 'revision-1', 'entry-1', 'project-1', 1, '燃魂术', "
            "'第一行\n消耗寿命', '2026-01-01', '2026-01-01')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO revision_character_snapshots (id, revision_id, character_id, project_id, "
            "\"exists\", name, description, created_at, updated_at) VALUES "
            "('snapshot-c1', 'revision-1', 'character-1', 'project-1', 1, '阿甲', "
            "'角色正文', '2026-01-01', '2026-01-01')"
        )
    )


def test_legacy_database_upgrade_keeps_entities_and_nulls_old_aliases(database: Path) -> None:
    command.upgrade(_config(), LEGACY_HEAD)
    engine = _engine(database)
    try:
        with engine.begin() as connection:
            _insert_legacy_entities(connection)
    finally:
        engine.dispose()

    command.upgrade(_config(), "head")

    engine = _engine(database)
    try:
        with engine.connect() as connection:
            entry = connection.execute(
                text("SELECT id, name, content, is_enabled FROM world_info_entries")
            ).one()
            character = connection.execute(
                text("SELECT id, name, description FROM characters")
            ).one()
            world_snapshot = connection.execute(
                text("SELECT id, name, content, aliases_json FROM revision_world_entry_snapshots")
            ).one()
            character_snapshot = connection.execute(
                text(
                    "SELECT id, name, description, aliases_json "
                    "FROM revision_character_snapshots"
                )
            ).one()
            world_alias_count = connection.execute(
                text("SELECT COUNT(*) FROM world_info_entry_aliases")
            ).scalar_one()
            character_alias_count = connection.execute(
                text("SELECT COUNT(*) FROM character_aliases")
            ).scalar_one()

        assert entry == ("entry-1", "燃魂术", "第一行\n消耗寿命", 1)
        assert character == ("character-1", "阿甲", "角色正文")
        # 旧快照缺少别名列，升级后为 NULL，按当时的空别名列表解释。
        assert world_snapshot == ("snapshot-w1", "燃魂术", "第一行\n消耗寿命", None)
        assert character_snapshot == ("snapshot-c1", "阿甲", "角色正文", None)
        assert world_alias_count == 0
        assert character_alias_count == 0
    finally:
        engine.dispose()


def test_duplicate_normalized_alias_is_rejected_within_one_entity(database: Path) -> None:
    command.upgrade(_config(), "head")
    engine = _engine(database)
    try:
        with engine.begin() as connection:
            _insert_legacy_entities(connection)
            connection.execute(
                text(
                    "INSERT INTO world_info_entries (id, world_info_id, uid, name, \"order\", "
                    "content, token_count, is_enabled, created_at, updated_at) VALUES "
                    "('entry-2', 'world-1', 2, '焚命术', 2, '', 0, 1, "
                    "'2026-01-01', '2026-01-01')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO world_info_entry_aliases "
                    "(id, entry_id, alias, normalized_alias, position) VALUES "
                    "('alias-1', 'entry-1', '焚命术', '焚命术', 0)"
                )
            )

        # 同一实体重复别名被唯一约束拒绝。
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO world_info_entry_aliases "
                        "(id, entry_id, alias, normalized_alias, position) VALUES "
                        "('alias-2', 'entry-1', '焚命术', '焚命术', 1)"
                    )
                )

        with engine.begin() as connection:
            # 不同实体可以共享同一别名。
            connection.execute(
                text(
                    "INSERT INTO world_info_entry_aliases "
                    "(id, entry_id, alias, normalized_alias, position) VALUES "
                    "('alias-3', 'entry-2', '焚命术', '焚命术', 0)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO character_aliases "
                    "(id, character_id, alias, normalized_alias, position) VALUES "
                    "('char-alias-1', 'character-1', 'Alpha', 'alpha', 0)"
                )
            )

        # 归一化后相同的 ASCII 大小写变体同样被拒绝。
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO character_aliases "
                        "(id, character_id, alias, normalized_alias, position) VALUES "
                        "('char-alias-2', 'character-1', 'ALPHA', 'alpha', 1)"
                    )
                )

        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM world_info_entry_aliases")
            ).scalar_one() == 2
            assert connection.execute(
                text("SELECT COUNT(*) FROM character_aliases")
            ).scalar_one() == 1
    finally:
        engine.dispose()


def test_downgrade_removes_alias_objects_and_keeps_legacy_data(database: Path) -> None:
    command.upgrade(_config(), "head")
    engine = _engine(database)
    try:
        with engine.begin() as connection:
            _insert_legacy_entities(connection)
            connection.execute(
                text(
                    "INSERT INTO character_aliases "
                    "(id, character_id, alias, normalized_alias, position) VALUES "
                    "('char-alias-1', 'character-1', '甲', '甲', 0)"
                )
            )
    finally:
        engine.dispose()

    command.downgrade(_config(), LEGACY_HEAD)

    engine = _engine(database)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        assert not (_ALIAS_TABLES & table_names)
        for snapshot_table in (
            "revision_world_entry_snapshots",
            "revision_character_snapshots",
        ):
            assert "aliases_json" not in {
                column["name"] for column in inspector.get_columns(snapshot_table)
            }

        with engine.connect() as connection:
            # 降级只丢弃新增别名数据，既有实体与快照保持原值。
            assert connection.execute(
                text("SELECT id, name, content FROM world_info_entries")
            ).one() == ("entry-1", "燃魂术", "第一行\n消耗寿命")
            assert connection.execute(
                text("SELECT id, name, description FROM characters")
            ).one() == ("character-1", "阿甲", "角色正文")
            assert connection.execute(
                text("SELECT id FROM revision_world_entry_snapshots")
            ).one() == ("snapshot-w1",)
    finally:
        engine.dispose()
