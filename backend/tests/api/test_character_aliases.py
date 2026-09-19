# -*- coding: utf-8 -*-
"""角色别名 API 测试。"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.storage.models.character_alias import CharacterAlias


async def create_project(client: AsyncClient, title: str = "别名测试项目") -> str:
    response = await client.post("/api/v1/projects", data={"title": title})
    assert response.status_code == 201
    return response.json()["id"]


async def create_character(
    client: AsyncClient,
    project_id: str,
    name: str,
    aliases_json: str | None = None,
) -> dict:
    data: dict[str, str] = {"name": name, "description": ""}
    if aliases_json is not None:
        data["aliases_json"] = aliases_json
    response = await client.post(f"/api/v1/projects/{project_id}/characters", data=data)
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_create_character_with_aliases_returns_them(client: AsyncClient) -> None:
    project_id = await create_project(client)

    character = await create_character(
        client, project_id, "林舟", aliases_json='["林师弟", "ThinRain"]'
    )

    assert character["aliases"] == ["林师弟", "ThinRain"]
    fetched = await client.get(f"/api/v1/characters/{character['id']}")
    assert fetched.json()["aliases"] == ["林师弟", "ThinRain"]


@pytest.mark.asyncio
async def test_create_character_without_aliases_defaults_to_empty(client: AsyncClient) -> None:
    project_id = await create_project(client)

    character = await create_character(client, project_id, "林舟")

    assert character["aliases"] == []


@pytest.mark.asyncio
async def test_character_list_includes_aliases(client: AsyncClient) -> None:
    project_id = await create_project(client)
    await create_character(client, project_id, "林舟", aliases_json='["林师弟"]')
    await create_character(client, project_id, "苏晚")

    response = await client.get(f"/api/v1/projects/{project_id}/characters")

    assert response.status_code == 200
    aliases_by_name = {
        item["name"]: item["aliases"] for item in response.json()["items"]
    }
    assert aliases_by_name == {"林舟": ["林师弟"], "苏晚": []}


@pytest.mark.asyncio
async def test_character_update_keeps_aliases_when_field_absent(client: AsyncClient) -> None:
    project_id = await create_project(client)
    character = await create_character(client, project_id, "林舟", aliases_json='["林师弟"]')

    response = await client.patch(
        f"/api/v1/characters/{character['id']}", data={"description": "新描述"}
    )

    assert response.status_code == 200
    assert response.json()["aliases"] == ["林师弟"]


@pytest.mark.asyncio
async def test_character_update_clears_aliases_with_empty_array(client: AsyncClient) -> None:
    project_id = await create_project(client)
    character = await create_character(client, project_id, "林舟", aliases_json='["林师弟"]')

    response = await client.patch(
        f"/api/v1/characters/{character['id']}", data={"aliases_json": "[]"}
    )

    assert response.status_code == 200
    assert response.json()["aliases"] == []


@pytest.mark.asyncio
async def test_character_alias_only_update_changes_updated_at(client: AsyncClient) -> None:
    project_id = await create_project(client)
    character = await create_character(client, project_id, "林舟")

    response = await client.patch(
        f"/api/v1/characters/{character['id']}", data={"aliases_json": '["林师弟"]'}
    )

    assert response.status_code == 200
    assert response.json()["aliases"] == ["林师弟"]
    assert response.json()["updated_at"] != character["updated_at"]


@pytest.mark.asyncio
async def test_character_update_rejects_invalid_aliases_json(client: AsyncClient) -> None:
    project_id = await create_project(client)
    character = await create_character(client, project_id, "林舟")

    for invalid in ("不是 JSON", '{"a": 1}', '["ok", 2]'):
        response = await client.patch(
            f"/api/v1/characters/{character['id']}", data={"aliases_json": invalid}
        )
        assert response.status_code == 400

    # 失败请求不改变既有别名。
    fetched = await client.get(f"/api/v1/characters/{character['id']}")
    assert fetched.json()["aliases"] == []


@pytest.mark.asyncio
async def test_character_update_rejects_too_many_aliases(client: AsyncClient) -> None:
    project_id = await create_project(client)
    character = await create_character(client, project_id, "林舟")

    too_many = "[" + ",".join(f'"别名{i:02d}"' for i in range(21)) + "]"
    response = await client.patch(
        f"/api/v1/characters/{character['id']}", data={"aliases_json": too_many}
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_delete_character_removes_alias_rows(
    client: AsyncClient, session: AsyncSession
) -> None:
    project_id = await create_project(client)
    character = await create_character(client, project_id, "林舟", aliases_json='["林师弟"]')

    response = await client.delete(f"/api/v1/characters/{character['id']}")
    assert response.status_code == 204

    remaining = await session.execute(select(col(CharacterAlias.id)))
    assert list(remaining.scalars().all()) == []


@pytest.mark.asyncio
async def test_batch_delete_characters_removes_alias_rows(
    client: AsyncClient, session: AsyncSession
) -> None:
    project_id = await create_project(client)
    first = await create_character(client, project_id, "林舟", aliases_json='["林师弟"]')
    second = await create_character(client, project_id, "苏晚", aliases_json='["晚晚"]')

    response = await client.post(
        f"/api/v1/projects/{project_id}/characters/batch/delete",
        json={"character_ids": [first["id"]]},
    )
    assert response.status_code == 200
    assert response.json()["deleted_count"] == 1

    remaining = await session.execute(select(col(CharacterAlias.character_id)))
    assert list(remaining.scalars().all()) == [second["id"]]
