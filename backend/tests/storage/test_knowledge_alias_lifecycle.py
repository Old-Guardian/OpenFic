# -*- coding: utf-8 -*-
"""别名归一化与生命周期测试。"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.storage.models.character import Character
from app.storage.models.character_alias import CharacterAlias
from app.storage.models.project import Project
from app.storage.models.world_info import WorldInfo
from app.storage.models.world_info_entry import WorldInfoEntry
from app.storage.models.world_info_entry_alias import WorldInfoEntryAlias
from app.storage.services import (
    character_service,
    knowledge_alias_service,
    project_service,
    world_info_entry_service,
    world_info_service,
)

# SQLite 返回无时区的 datetime，比较基准保持同样口径。
OLD_TIMESTAMP = datetime(2020, 1, 1)


def test_normalize_aliases_strips_deduplicates_and_drops_entity_name() -> None:
    assert knowledge_alias_service.normalize_aliases(
        ["  焚命术 ", "焚命术", "BurningSoul", "burningsoul", "燃魂术"],
        entity_name="燃魂术",
    ) == ["焚命术", "BurningSoul"]


def test_normalize_aliases_rejects_empty_alias() -> None:
    with pytest.raises(ValueError, match="别名不能为空"):
        knowledge_alias_service.normalize_aliases(["   "], entity_name="燃魂术")


def test_normalize_aliases_rejects_over_length_alias() -> None:
    with pytest.raises(ValueError, match="100"):
        knowledge_alias_service.normalize_aliases(["甲" * 101], entity_name="燃魂术")


def test_normalize_aliases_rejects_too_many_effective_aliases() -> None:
    too_many = [f"别名{i:02d}" for i in range(21)]
    with pytest.raises(ValueError, match="20"):
        knowledge_alias_service.normalize_aliases(too_many, entity_name="燃魂术")


def test_normalize_aliases_counts_only_surviving_aliases() -> None:
    # 21 个提交项中有 2 个与正式名称相同，去重后 19 个，不超限。
    submitted = [f"别名{i:02d}" for i in range(19)] + ["燃魂术", "燃魂术"]
    assert len(knowledge_alias_service.normalize_aliases(submitted, entity_name="燃魂术")) == 19


async def _create_project(session) -> Project:
    project = Project(title="别名测试项目", description="")
    session.add(project)
    await session.flush()
    return project


async def _create_world_info(session, project: Project) -> WorldInfo:
    world_info = WorldInfo(project_id=project.id, name="世界书", description="")
    session.add(world_info)
    await session.flush()
    return world_info


@pytest.mark.asyncio
async def test_character_aliases_are_written_and_cleared_with_entity(session) -> None:
    project = await _create_project(session)
    character = await character_service.create_character(
        session, project.id, name="林舟", aliases=["林师弟", "ThinRain"]
    )
    assert await character_service.list_aliases(session, character.id) == ["林师弟", "ThinRain"]

    # 未提供 aliases 表示保留。
    await character_service.update_character(session, character.id, description="新描述")
    assert await character_service.list_aliases(session, character.id) == ["林师弟", "ThinRain"]

    # [] 表示清空。
    await character_service.update_character(session, character.id, aliases=[])
    assert await character_service.list_aliases(session, character.id) == []

    await character_service.update_character(session, character.id, aliases=["新别名", "林舟"])
    # 与正式名称相同的别名被静默丢弃。
    assert await character_service.list_aliases(session, character.id) == ["新别名"]

    await character_service.delete_character(session, character.id)
    assert await _count_rows(session, CharacterAlias) == 0


@pytest.mark.asyncio
async def test_alias_only_character_update_bumps_updated_at(session) -> None:
    project = await _create_project(session)
    character = await character_service.create_character(session, project.id, name="林舟")
    character.updated_at = OLD_TIMESTAMP
    await session.flush()

    updated = await character_service.update_character(
        session, character.id, aliases=["林师弟"]
    )

    assert await _stored_updated_at(session, Character, updated.id) > OLD_TIMESTAMP
    assert await character_service.list_aliases(session, character.id) == ["林师弟"]


@pytest.mark.asyncio
async def test_batch_delete_characters_removes_aliases(session) -> None:
    project = await _create_project(session)
    first = await character_service.create_character(
        session, project.id, name="甲", aliases=["甲别名"]
    )
    second = await character_service.create_character(
        session, project.id, name="乙", aliases=["乙别名"]
    )

    assert await character_service.batch_delete_characters(session, project.id, [first.id]) == 1

    remaining = await session.execute(select(col(CharacterAlias.character_id)))
    assert list(remaining.scalars().all()) == [second.id]


@pytest.mark.asyncio
async def test_entry_aliases_are_written_cleared_and_removed_on_delete(session) -> None:
    project = await _create_project(session)
    world_info = await _create_world_info(session, project)
    entry = await world_info_entry_service.create_entry(
        session, world_info.id, name="燃魂术", aliases=["焚命术", "  燃魂术 "]
    )
    assert await world_info_entry_service.list_aliases(session, entry.id) == ["焚命术"]

    entry.updated_at = OLD_TIMESTAMP
    await session.flush()
    updated = await world_info_entry_service.update_entry(
        session, entry.id, aliases=["焚命术", "献祭"]
    )
    assert await _stored_updated_at(session, WorldInfoEntry, updated.id) > OLD_TIMESTAMP
    assert await world_info_entry_service.list_aliases(session, entry.id) == ["焚命术", "献祭"]

    await world_info_entry_service.delete_entry(session, entry.id)
    assert await _count_rows(session, WorldInfoEntryAlias) == 0


@pytest.mark.asyncio
async def test_delete_all_and_batch_delete_entries_remove_aliases_only_for_scope(session) -> None:
    project = await _create_project(session)
    world_info = await _create_world_info(session, project)
    await world_info_entry_service.create_entry(
        session, world_info.id, name="保留", aliases=["保留别名"]
    )
    removed = await world_info_entry_service.create_entry(
        session, world_info.id, name="删除", aliases=["删除别名"]
    )

    # 跨世界书的 ID 会命中世界书过滤，不允许清掉别的世界书的别名。
    other_world_info = await world_info_service.get_or_create_world_info_by_project(
        session, (await _create_project(session)).id
    )
    other_entry = await world_info_entry_service.create_entry(
        session, other_world_info.id, name="他书条目", aliases=["他书别名"]
    )

    assert await world_info_entry_service.batch_delete_entries(
        session, world_info.id, [removed.id, other_entry.id]
    ) == 1
    assert await world_info_entry_service.list_aliases(session, other_entry.id) == ["他书别名"]

    assert await world_info_entry_service.delete_all_entries(session, world_info.id) == 1
    assert await _alias_entry_ids(session) == {other_entry.id}
    assert await world_info_entry_service.list_aliases(session, other_entry.id) == ["他书别名"]


@pytest.mark.asyncio
async def test_import_overwrite_clears_aliases_and_append_keeps_them(session) -> None:
    project = await _create_project(session)
    world_info = await _create_world_info(session, project)
    entry = await world_info_entry_service.create_entry(
        session, world_info.id, name="燃魂术", aliases=["焚命术"]
    )

    imported = [
        world_info_entry_service.WorldInfoImportEntry(
            uid=1, name="燃魂术", content="新正文", is_enabled=True, order=1
        )
    ]
    # append 按名称匹配既有条目，只改内容，保留别名。
    await world_info_entry_service.import_entries(session, world_info.id, imported, mode="append")
    assert await world_info_entry_service.list_aliases(session, entry.id) == ["焚命术"]

    await world_info_entry_service.import_entries(
        session, world_info.id, imported, mode="overwrite"
    )
    assert await _count_rows(session, WorldInfoEntryAlias) == 0


@pytest.mark.asyncio
async def test_bulk_toggle_and_order_shift_bump_updated_at(session) -> None:
    project = await _create_project(session)
    world_info = await _create_world_info(session, project)
    first = await world_info_entry_service.create_entry(session, world_info.id, name="甲")
    second = await world_info_entry_service.create_entry(session, world_info.id, name="乙")
    first.updated_at = OLD_TIMESTAMP
    second.updated_at = OLD_TIMESTAMP
    await session.flush()

    await world_info_entry_service.batch_toggle_entries(
        session, world_info.id, [first.id], False
    )
    assert await _stored_updated_at(session, WorldInfoEntry, first.id) > OLD_TIMESTAMP

    # 移动乙会触发 shift_orders，顺移到的甲同样要更新时间。
    await world_info_entry_service.move_entry(session, second.id, 1)
    assert await _stored_updated_at(session, WorldInfoEntry, first.id) > OLD_TIMESTAMP


@pytest.mark.asyncio
async def test_deleting_world_info_removes_entry_aliases(session) -> None:
    project = await _create_project(session)
    world_info = await _create_world_info(session, project)
    await world_info_entry_service.create_entry(
        session, world_info.id, name="燃魂术", aliases=["焚命术"]
    )

    await world_info_service.delete_world_info(session, world_info.id)

    assert await _count_rows(session, WorldInfoEntryAlias) == 0


@pytest.mark.asyncio
async def test_deleting_project_removes_entities_and_aliases(session) -> None:
    project = await _create_project(session)
    await character_service.create_character(
        session, project.id, name="林舟", aliases=["林师弟"]
    )
    world_info = await _create_world_info(session, project)
    await world_info_entry_service.create_entry(
        session, world_info.id, name="燃魂术", aliases=["焚命术"]
    )

    await project_service.delete_project(session, project.id)

    assert await _count_rows(session, CharacterAlias) == 0
    assert await _count_rows(session, WorldInfoEntryAlias) == 0
    assert (await session.execute(select(col(WorldInfo.id)))).scalars().all() == []


async def _count_rows(session, model) -> int:
    result = await session.execute(select(col(model.id)))
    return len(list(result.scalars().all()))


async def _stored_updated_at(session, model, entity_id: str) -> datetime:
    """直接读取库中的 updated_at，绕开 session 身份映射里的内存值。"""
    result = await session.execute(
        select(col(model.updated_at)).where(col(model.id) == entity_id)
    )
    return result.scalar_one()


async def _alias_entry_ids(session) -> set[str]:
    result = await session.execute(select(col(WorldInfoEntryAlias.entry_id)))
    return set(result.scalars().all())
