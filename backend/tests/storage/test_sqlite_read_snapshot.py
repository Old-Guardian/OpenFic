"""Document the aiosqlite transaction behavior required by search cursors."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest


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
