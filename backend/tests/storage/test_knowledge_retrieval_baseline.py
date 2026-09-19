"""Tests for the reproducible T0 fixture and baseline utilities."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from benchmarks.knowledge_retrieval_baseline import (
    DEFAULT_SEED,
    backup_database,
    capture_legacy_baseline,
    capture_world_book_snapshot,
    fixture_digest,
    fixture_summary,
    generate_characters,
    prepare_fixture_database,
)


def _create_sample_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE world_info (
                id TEXT PRIMARY KEY,
                project_id TEXT
            );
            CREATE TABLE projects (
                id TEXT PRIMARY KEY
            );
            CREATE TABLE world_info_entries (
                id TEXT PRIMARY KEY,
                world_info_id TEXT,
                uid INTEGER,
                name TEXT,
                "order" INTEGER,
                content TEXT,
                is_enabled INTEGER
            );
            CREATE TABLE characters (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                name TEXT,
                description TEXT,
                image_path TEXT,
                is_favorited INTEGER,
                created_at TEXT,
                updated_at TEXT
            );
            INSERT INTO projects (id) VALUES ('project-1');
            INSERT INTO world_info (id, project_id) VALUES ('world-1', 'project-1');
            INSERT INTO world_info_entries
                (id, world_info_id, uid, name, "order", content, is_enabled)
            VALUES
                ('entry-b', 'world-1', 2, '禁用项', 2, '', 0),
                ('entry-a', 'world-1', 1, '燃魂术', 1, '第一行\n消耗寿命', 1);
            INSERT INTO characters
                (id, project_id, name, description, is_favorited, updated_at)
            VALUES ('character-1', 'project-1', '阿甲', '角色正文', 0, '2026-09-18');
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_character_generator_is_reproducible_and_regular_is_stress_prefix() -> None:
    first = list(generate_characters("project-1", count=290))
    second = list(generate_characters("project-1", count=4290))

    assert first == second[:290]
    assert fixture_digest(first) == fixture_digest(
        list(generate_characters("project-1", count=290, seed=DEFAULT_SEED))
    )
    assert len({character.id for character in second}) == 4290
    assert all(0 <= len(character.aliases) <= 3 for character in second)
    summary = fixture_summary(second)
    assert 1700 <= summary["description_median_chars"] <= 2300
    assert 8500 <= summary["description_p95_chars_nearest_rank"] <= 11_500


def test_snapshot_and_legacy_baseline_do_not_expose_source_text(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    _create_sample_database(database)
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        snapshot = capture_world_book_snapshot(connection)
        baseline = capture_legacy_baseline(
            connection,
            world_info_id="world-1",
            project_id="project-1",
            warmups=0,
            iterations=2,
        )
    finally:
        connection.close()

    assert snapshot["entry_count"] == 2
    assert snapshot["enabled_count"] == 1
    assert snapshot["empty_content_count"] == 1
    assert snapshot["project_character_count"] == 1
    assert snapshot["logical_fingerprint"].startswith("sha256:")
    serialized = json.dumps(baseline, ensure_ascii=False)
    assert "燃魂术" not in serialized
    assert "消耗寿命" not in serialized
    assert baseline["world_list"]["output_size"]["characters"] > 0
    assert baseline["world_single_read"]["timing"]["iterations"] == 2


def test_sqlite_backup_captures_data_without_mutating_source(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "copy.db"
    _create_sample_database(source)

    backup_database(source, destination)
    copy_connection = sqlite3.connect(destination)
    copy_connection.execute(
        "UPDATE world_info_entries SET content = 'changed' WHERE id = 'entry-a'"
    )
    copy_connection.commit()
    copy_connection.close()

    source_connection = sqlite3.connect(source)
    try:
        value = source_connection.execute(
            "SELECT content FROM world_info_entries WHERE id = 'entry-a'"
        ).fetchone()[0]
    finally:
        source_connection.close()
    assert value == "第一行\n消耗寿命"


def test_prepare_fixture_database_requires_empty_project_and_writes_only_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "fixture.db"
    _create_sample_database(source)
    source_connection = sqlite3.connect(source)
    source_connection.execute("DELETE FROM characters")
    source_connection.commit()
    source_connection.close()

    summary = prepare_fixture_database(
        source,
        destination,
        project_id="project-1",
        count=5,
    )

    assert summary["count"] == 5
    fixture_connection = sqlite3.connect(destination)
    try:
        rows = fixture_connection.execute(
            "SELECT id, name, description FROM characters ORDER BY name"
        ).fetchall()
    finally:
        fixture_connection.close()
    assert len(rows) == 5
    assert rows[0][1] == "基准角色0001"
    assert "角色编号0001" in rows[0][2]

    source_connection = sqlite3.connect(source)
    try:
        source_count = source_connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
    finally:
        source_connection.close()
    assert source_count == 0
