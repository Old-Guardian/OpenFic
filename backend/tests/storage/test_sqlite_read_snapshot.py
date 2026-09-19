# -*- coding: utf-8 -*-
"""验证检索、目录与批读服务在真实落盘 SQLite 上的一致性读快照。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from app.core.ids import generate_id
from app.storage.database import enable_sqlite_transactions
from app.storage.models.character import Character
from app.storage.models.project import Project
from app.storage.services import knowledge_read_service, knowledge_search_service
from app.storage.services.knowledge_contracts import (
    KnowledgeReadItemRequest,
    KnowledgeReadRequest,
    KnowledgeSearchRequest,
    ReadStatus,
    SearchMatchMode,
)
from tests.model_registry import register_sqlmodel_models

_TIMESTAMP = "2026-01-01 00:00:00.000000"
_CHARACTER_INSERT = (
    "INSERT INTO characters "
    "(id, project_id, name, description, is_favorited, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, 0, ?, ?)"
)


async def _read_value(connection: aiosqlite.Connection) -> str:
    cursor = await connection.execute("SELECT value FROM snapshot_probe WHERE id = 1")
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    assert row is not None
    return str(row[0])


@pytest.mark.asyncio
async def test_explicit_read_transaction_keeps_one_wal_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "snapshot.db"
    setup = await aiosqlite.connect(database)
    await setup.execute("PRAGMA journal_mode=WAL")
    await setup.execute("CREATE TABLE snapshot_probe (id INTEGER PRIMARY KEY, value TEXT)")
    await setup.execute("INSERT INTO snapshot_probe VALUES (1, 'before')")
    await setup.commit()
    await setup.close()

    reader = await aiosqlite.connect(database)
    writer = await aiosqlite.connect(database)
    try:
        await reader.execute("BEGIN")
        assert await _read_value(reader) == "before"

        await writer.execute("UPDATE snapshot_probe SET value = 'after' WHERE id = 1")
        await writer.commit()

        assert await _read_value(reader) == "before"
        await reader.commit()
        assert await _read_value(reader) == "after"
    finally:
        await reader.close()
        await writer.close()


@pytest.mark.asyncio
async def test_separate_selects_without_begin_do_not_share_a_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "autocommit.db"
    setup = await aiosqlite.connect(database)
    await setup.execute("CREATE TABLE snapshot_probe (id INTEGER PRIMARY KEY, value TEXT)")
    await setup.execute("INSERT INTO snapshot_probe VALUES (1, 'before')")
    await setup.commit()
    await setup.close()

    reader = await aiosqlite.connect(database)
    writer = await aiosqlite.connect(database)
    try:
        assert await _read_value(reader) == "before"
        await writer.execute("UPDATE snapshot_probe SET value = 'after' WHERE id = 1")
        await writer.commit()
        assert await _read_value(reader) == "after"
    finally:
        await reader.close()
        await writer.close()


async def _create_service_database(
    tmp_path: Path, names: list[str]
) -> tuple[object, str, Path, list[str]]:
    """真实落盘 SQLite + 应用事务配置，返回 (engine, project_id, db_path, ids)。"""
    register_sqlmodel_models()
    db_path = tmp_path / "service.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}", future=True
    )
    enable_sqlite_transactions(engine)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    project_id = generate_id()
    characters = [
        Character(project_id=project_id, name=name, description=f"{name}的正文")
        for name in names
    ]
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(Project(id=project_id, title="快照测试"))
        session.add_all(characters)
        await session.commit()
    return engine, project_id, db_path, [character.id for character in characters]


def _write_after_statement(
    engine, db_path: Path, trigger: str, sql: str, parameters: tuple
) -> dict[str, bool]:
    """在指定 SQL 执行后由独立连接执行一次写入并提交，模拟并发写者。"""
    fired = {"value": False}

    def _hook(conn, cursor, statement, parameters_, context, executemany):
        if fired["value"] or trigger not in statement:
            return
        fired["value"] = True
        writer = sqlite3.connect(db_path)
        try:
            writer.execute(sql, parameters)
            writer.commit()
        finally:
            writer.close()

    event.listen(engine.sync_engine, "after_cursor_execute", _hook)
    return fired


@pytest.mark.asyncio
async def test_search_characters_keeps_one_snapshot_across_count_and_page(
    tmp_path: Path,
) -> None:
    engine, project_id, db_path, _ = await _create_service_database(tmp_path, ["角色甲"])
    try:
        fired = _write_after_statement(
            engine,
            db_path,
            "count(",
            _CHARACTER_INSERT,
            (generate_id(), project_id, "角色乙", "角色乙的正文", _TIMESTAMP, _TIMESTAMP),
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            response = await knowledge_search_service.search_characters(
                session,
                project_id,
                KnowledgeSearchRequest(query="角色", match=SearchMatchMode.ALL, limit=10),
            )
        assert fired["value"] is True
        assert response.total_matches == 1
        assert response.returned_count == 1
        assert [item.name for item in response.items] == ["角色甲"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_characters_keeps_one_snapshot_across_count_and_page(
    tmp_path: Path,
) -> None:
    engine, project_id, db_path, _ = await _create_service_database(tmp_path, ["角色甲"])
    try:
        fired = _write_after_statement(
            engine,
            db_path,
            "count(",
            _CHARACTER_INSERT,
            (generate_id(), project_id, "角色乙", "角色乙的正文", _TIMESTAMP, _TIMESTAMP),
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            page = await knowledge_search_service.list_characters(
                session, project_id, limit=10
            )
        assert fired["value"] is True
        assert page.total == 1
        assert len(page.items) == 1
        assert page.has_more is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_read_characters_keeps_one_snapshot_across_scope_check_and_fetch(
    tmp_path: Path,
) -> None:
    engine, project_id, db_path, ids = await _create_service_database(tmp_path, ["角色甲"])
    try:
        fired = _write_after_statement(
            engine,
            db_path,
            "FROM projects",
            "DELETE FROM characters WHERE id = ?",
            (ids[0],),
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            response = await knowledge_read_service.read_characters(
                session,
                project_id,
                KnowledgeReadRequest(items=[KnowledgeReadItemRequest(id=ids[0])]),
            )
        assert fired["value"] is True
        assert response.items[0].status is ReadStatus.OK
        assert response.items[0].content == "角色甲的正文"
    finally:
        await engine.dispose()
