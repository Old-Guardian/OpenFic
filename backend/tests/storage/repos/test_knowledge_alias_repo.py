# -*- coding: utf-8 -*-
"""别名声仓储层测试。"""

from __future__ import annotations

import pytest

from app.core.text_normalization import normalize_literal
from app.storage.models.character import Character
from app.storage.models.project import Project
from app.storage.models.world_info import WorldInfo
from app.storage.models.world_info_entry import WorldInfoEntry
from app.storage.repos import character_alias_repo, world_info_entry_alias_repo


async def _create_project(session) -> Project:
    project = Project(title="P", description="")
    session.add(project)
    await session.flush()
    return project


async def _create_world_info(session, project: Project) -> WorldInfo:
    world_info = WorldInfo(project_id=project.id, name="世界书", description="")
    session.add(world_info)
    await session.flush()
    return world_info


async def _create_entry(
    session, world_info: WorldInfo, name: str, uid: int
) -> WorldInfoEntry:
    entry = WorldInfoEntry(
        world_info_id=world_info.id,
        uid=uid,
        name=name,
        order=uid,
        content="",
    )
    session.add(entry)
    await session.flush()
    return entry


async def _create_character(session, project: Project, name: str) -> Character:
    character = Character(project_id=project.id, name=name, description="")
    session.add(character)
    await session.flush()
    return character


@pytest.mark.asyncio
async def test_replace_for_entry_preserves_input_order_and_normalization(session):
    project = await _create_project(session)
    entry = await _create_entry(session, await _create_world_info(session, project), "燃魂术", 1)

    rows = await world_info_entry_alias_repo.replace_for_entry(
        session, entry.id, ["焚命术", "BurningSoul", "第三别名"]
    )

    assert [(row.alias, row.normalized_alias, row.position) for row in rows] == [
        ("焚命术", "焚命术", 0),
        ("BurningSoul", "burningsoul", 1),
        ("第三别名", "第三别名", 2),
    ]
    listed = await world_info_entry_alias_repo.list_by_entry(session, entry.id)
    assert [row.alias for row in listed] == ["焚命术", "BurningSoul", "第三别名"]
    assert normalize_literal("BurningSoul") == rows[1].normalized_alias


@pytest.mark.asyncio
async def test_replace_for_entry_drops_previous_aliases(session):
    project = await _create_project(session)
    entry = await _create_entry(session, await _create_world_info(session, project), "燃魂术", 1)

    await world_info_entry_alias_repo.replace_for_entry(session, entry.id, ["旧别名"])
    await world_info_entry_alias_repo.replace_for_entry(session, entry.id, ["新别名"])
    assert [
        row.alias
        for row in await world_info_entry_alias_repo.list_by_entry(session, entry.id)
    ] == ["新别名"]

    await world_info_entry_alias_repo.replace_for_entry(session, entry.id, [])
    assert await world_info_entry_alias_repo.list_by_entry(session, entry.id) == []


@pytest.mark.asyncio
async def test_list_by_entries_returns_aliases_grouped_by_entry(session):
    project = await _create_project(session)
    world_info = await _create_world_info(session, project)
    first = await _create_entry(session, world_info, "甲", 1)
    second = await _create_entry(session, world_info, "乙", 2)
    await world_info_entry_alias_repo.replace_for_entry(session, first.id, ["甲A", "甲B"])
    await world_info_entry_alias_repo.replace_for_entry(session, second.id, ["乙A"])

    rows = await world_info_entry_alias_repo.list_by_entries(session, [second.id, first.id])

    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(row.entry_id, []).append(row.alias)
    assert grouped == {first.id: ["甲A", "甲B"], second.id: ["乙A"]}
    assert await world_info_entry_alias_repo.list_by_entries(session, []) == []


@pytest.mark.asyncio
async def test_delete_helpers_only_remove_target_aliases(session):
    project = await _create_project(session)
    world_info = await _create_world_info(session, project)
    first = await _create_entry(session, world_info, "甲", 1)
    second = await _create_entry(session, world_info, "乙", 2)
    await world_info_entry_alias_repo.replace_for_entry(session, first.id, ["甲A"])
    await world_info_entry_alias_repo.replace_for_entry(session, second.id, ["乙A"])

    assert await world_info_entry_alias_repo.delete_by_entry(session, first.id) == 1
    assert await world_info_entry_alias_repo.list_by_entry(session, first.id) == []

    assert await world_info_entry_alias_repo.delete_by_entries(session, [second.id]) == 1
    assert await world_info_entry_alias_repo.list_by_entries(session, [first.id, second.id]) == []
    assert await world_info_entry_alias_repo.delete_by_entries(session, []) == 0


@pytest.mark.asyncio
async def test_character_aliases_are_scoped_to_their_character(session):
    project = await _create_project(session)
    first = await _create_character(session, project, "师父")
    second = await _create_character(session, project, "徒弟")

    await character_alias_repo.replace_for_character(session, first.id, ["Teacher"])
    # 不同角色可以共享同一别名。
    await character_alias_repo.replace_for_character(session, second.id, ["师父"])

    rows = await character_alias_repo.list_by_characters(session, [first.id, second.id])
    grouped: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row.character_id, []).append((row.alias, row.normalized_alias))
    assert grouped == {
        first.id: [("Teacher", "teacher")],
        second.id: [("师父", "师父")],
    }

    assert await character_alias_repo.delete_by_character(session, first.id) == 1
    assert await character_alias_repo.list_by_character(session, first.id) == []
    assert [
        row.alias for row in await character_alias_repo.list_by_character(session, second.id)
    ] == ["师父"]
