"""The character graph migration works on empty and deployed fork databases."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.storage.database import ALEMBIC_INI_PATH


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


def test_empty_database_has_single_head_and_graph_schema(database: Path) -> None:
    scripts = ScriptDirectory.from_config(_config())
    assert scripts.get_heads() == ["1026"]
    assert scripts.get_revision("1026").down_revision == "1025"

    command.upgrade(_config(), "head")

    engine = _engine(database)
    try:
        inspector = inspect(engine)
        assert {"character_relationships", "revision_character_relationship_snapshots"} <= set(
            inspector.get_table_names()
        )
        for table in ("characters", "revision_character_snapshots"):
            assert {"graph_x", "graph_y"} <= _columns(inspector, table)
        assert {"project_id", "source_character_id", "target_character_id"} <= _columns(
            inspector, "character_relationships"
        )
        assert {"revision_id", "relationship_id", "project_id"} <= _columns(
            inspector, "revision_character_relationship_snapshots"
        )
        assert sum(
            column["name"] == "reasoning_effort"
            for column in inspector.get_columns("agent_definitions")
        ) == 1
    finally:
        engine.dispose()


def test_fork_1025_upgrades_and_downgrades_without_changing_existing_data(
    database: Path,
) -> None:
    command.upgrade(_config(), "1025")
    engine = _engine(database)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id, title, description, word_count, chapter_count, "
                    "cover_path, created_at, updated_at) VALUES "
                    "('project-1', 'Project', '', 0, 0, NULL, '2026-01-01', '2026-01-01')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO characters (id, project_id, name, description, image_path, "
                    "is_favorited, created_at, updated_at) VALUES "
                    "('character-1', 'project-1', 'Alice', 'Existing text', NULL, 0, "
                    "'2026-01-01', '2026-01-01')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO character_aliases "
                    "(id, character_id, alias, normalized_alias, position) VALUES "
                    "('alias-1', 'character-1', 'A', 'a', 0)"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(_config(), "head")

    engine = _engine(database)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT name, description, graph_x, graph_y FROM characters")
            ).one() == ("Alice", "Existing text", None, None)
            assert connection.execute(
                text("SELECT alias FROM character_aliases WHERE character_id = 'character-1'")
            ).scalar_one() == "A"
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "1026"
    finally:
        engine.dispose()

    command.downgrade(_config(), "1025")

    engine = _engine(database)
    try:
        inspector = inspect(engine)
        assert "character_relationships" not in inspector.get_table_names()
        assert "revision_character_relationship_snapshots" not in inspector.get_table_names()
        assert "graph_x" not in _columns(inspector, "characters")
        assert "graph_y" not in _columns(inspector, "revision_character_snapshots")
        assert "reasoning_effort" in _columns(inspector, "agent_definitions")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT name, description FROM characters")
            ).one() == ("Alice", "Existing text")
            assert connection.execute(
                text("SELECT alias FROM character_aliases")
            ).scalar_one() == "A"
    finally:
        engine.dispose()
