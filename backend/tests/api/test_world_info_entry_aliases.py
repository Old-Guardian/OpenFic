# -*- coding: utf-8 -*-
"""世界书条目别名 API 测试。"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.storage.models.world_info_entry_alias import WorldInfoEntryAlias


@pytest.fixture
async def world_info_id(client: AsyncClient) -> str:
    """创建项目并返回项目唯一世界书 ID。"""
    project_resp = await client.post("/api/v1/projects", data={"title": "别名测试小说"})
    project_id = project_resp.json()["id"]
    world_info_resp = await client.get(f"/api/v1/projects/{project_id}/world-info")
    return world_info_resp.json()["id"]


async def create_entry(
    client: AsyncClient,
    world_info_id: str,
    name: str,
    aliases: list[str] | None = None,
) -> dict:
    payload: dict = {"name": name, "content": ""}
    if aliases is not None:
        payload["aliases"] = aliases
    response = await client.post(
        f"/api/v1/world-info/{world_info_id}/entries", json=payload
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_create_entry_with_aliases_returns_them(
    client: AsyncClient, world_info_id: str
) -> None:
    entry = await create_entry(client, world_info_id, "燃魂术", ["焚命术", "燃魂术"])

    # 与正式名称相同的别名被静默丢弃。
    assert entry["aliases"] == ["焚命术"]
    fetched = await client.get(f"/api/v1/world-info-entries/{entry['id']}")
    assert fetched.json()["aliases"] == ["焚命术"]


@pytest.mark.asyncio
async def test_entry_list_includes_aliases(client: AsyncClient, world_info_id: str) -> None:
    await create_entry(client, world_info_id, "燃魂术", ["焚命术"])
    await create_entry(client, world_info_id, "无别名条目")

    response = await client.get(f"/api/v1/world-info/{world_info_id}/entries")

    assert response.status_code == 200
    aliases_by_name = {item["name"]: item["aliases"] for item in response.json()["items"]}
    assert aliases_by_name == {"燃魂术": ["焚命术"], "无别名条目": []}


@pytest.mark.asyncio
async def test_entry_update_keeps_and_clears_aliases(
    client: AsyncClient, world_info_id: str
) -> None:
    entry = await create_entry(client, world_info_id, "燃魂术", ["焚命术"])

    kept = await client.patch(
        f"/api/v1/world-info-entries/{entry['id']}", json={"content": "新正文"}
    )
    assert kept.status_code == 200
    assert kept.json()["aliases"] == ["焚命术"]
    assert kept.json()["updated_at"] != entry["updated_at"]

    cleared = await client.patch(
        f"/api/v1/world-info-entries/{entry['id']}", json={"aliases": []}
    )
    assert cleared.status_code == 200
    assert cleared.json()["aliases"] == []


@pytest.mark.asyncio
async def test_entry_alias_only_update_is_accepted(
    client: AsyncClient, world_info_id: str
) -> None:
    entry = await create_entry(client, world_info_id, "燃魂术")

    response = await client.patch(
        f"/api/v1/world-info-entries/{entry['id']}", json={"aliases": ["焚命术"]}
    )

    assert response.status_code == 200
    assert response.json()["aliases"] == ["焚命术"]
    assert response.json()["name"] == "燃魂术"


@pytest.mark.asyncio
async def test_entry_update_rejects_invalid_aliases(
    client: AsyncClient, world_info_id: str
) -> None:
    entry = await create_entry(client, world_info_id, "燃魂术")

    empty = await client.patch(
        f"/api/v1/world-info-entries/{entry['id']}", json={"aliases": ["  "]}
    )
    assert empty.status_code == 400

    too_long = await client.patch(
        f"/api/v1/world-info-entries/{entry['id']}", json={"aliases": ["甲" * 101]}
    )
    assert too_long.status_code == 400

    fetched = await client.get(f"/api/v1/world-info-entries/{entry['id']}")
    assert fetched.json()["aliases"] == []


@pytest.mark.asyncio
async def test_delete_entry_removes_alias_rows(
    client: AsyncClient, world_info_id: str, session: AsyncSession
) -> None:
    entry = await create_entry(client, world_info_id, "燃魂术", ["焚命术"])

    response = await client.delete(f"/api/v1/world-info-entries/{entry['id']}")
    assert response.status_code == 204

    remaining = await session.execute(select(col(WorldInfoEntryAlias.id)))
    assert list(remaining.scalars().all()) == []


@pytest.mark.asyncio
async def test_batch_delete_entries_removes_alias_rows(
    client: AsyncClient, world_info_id: str, session: AsyncSession
) -> None:
    first = await create_entry(client, world_info_id, "甲条目", ["甲别名"])
    second = await create_entry(client, world_info_id, "乙条目", ["乙别名"])

    response = await client.post(
        f"/api/v1/world-info/{world_info_id}/entries/batch/delete",
        json={"entry_ids": [first["id"]]},
    )
    assert response.status_code == 200
    assert response.json()["deleted_count"] == 1

    remaining = await session.execute(select(col(WorldInfoEntryAlias.entry_id)))
    assert list(remaining.scalars().all()) == [second["id"]]
