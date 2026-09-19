# -*- coding: utf-8 -*-
"""KnowledgeAlias Service - 别名归一化、校验与写入。

世界书条目与角色共用同一套别名校验规则（见第一阶段计划 §4.1）：

- 写入时去除首尾空白，空值报错；
- 按字面匹配规则归一化后去重，保留首次出现的展示文本；
- 与实体当前正式名称归一化后相同的别名静默丢弃，不报错；
- 单个别名 1～100 字符，每个实体最多 20 个，超限报错；
- 全量替换语义：``[]`` 表示清空，``None`` 由调用方解释为保留。

别名与实体在同一事务写入，服务层不提交；提交由调用方（请求级
``get_session`` 或 Agent 工具自己的 session）负责。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text_normalization import normalize_literal
from app.storage.repos import character_alias_repo, world_info_entry_alias_repo
from app.storage.services.knowledge_contracts import (
    MAX_ALIAS_CHARS,
    MAX_ALIASES_PER_ENTITY,
)


def normalize_aliases(raw_aliases: list[str], *, entity_name: str) -> list[str]:
    """把提交的别名列表归一化为可存储的别名列表。

    Args:
        raw_aliases: 调用方提交的别名，顺序即用户输入顺序。
        entity_name: 实体当前（或即将生效）的正式名称，用于丢弃重名别名。

    Returns:
        归一化后的别名列表，顺序与输入一致。

    Raises:
        ValueError: 存在空别名、超长别名或去重后数量超过上限。
    """
    normalized_entity_name = normalize_literal(entity_name.strip())
    normalized_aliases: list[str] = []
    seen: set[str] = set()

    for raw_alias in raw_aliases:
        alias = raw_alias.strip()
        if not alias:
            raise ValueError("别名不能为空")
        if len(alias) > MAX_ALIAS_CHARS:
            raise ValueError(f"单个别名不能超过 {MAX_ALIAS_CHARS} 个字符")

        normalized = normalize_literal(alias)
        if normalized == normalized_entity_name or normalized in seen:
            continue

        seen.add(normalized)
        normalized_aliases.append(alias)

    if len(normalized_aliases) > MAX_ALIASES_PER_ENTITY:
        raise ValueError(f"每个实体最多 {MAX_ALIASES_PER_ENTITY} 个别名")

    return normalized_aliases


# ============== 角色别名 ==============


async def replace_character_aliases(
    session: AsyncSession,
    character_id: str,
    aliases: list[str],
) -> list[str]:
    """全量替换角色别名，返回实际写入的别名。"""
    rows = await character_alias_repo.replace_for_character(session, character_id, aliases)
    return [row.alias for row in rows]


async def list_character_aliases(session: AsyncSession, character_id: str) -> list[str]:
    """获取单个角色的别名，按用户输入顺序返回。"""
    rows = await character_alias_repo.list_by_character(session, character_id)
    return [row.alias for row in rows]


async def list_character_aliases_by_ids(
    session: AsyncSession,
    character_ids: list[str],
) -> dict[str, list[str]]:
    """批量获取角色别名，缺失的角色映射为空列表。"""
    result: dict[str, list[str]] = {character_id: [] for character_id in character_ids}
    for row in await character_alias_repo.list_by_characters(session, character_ids):
        result.setdefault(row.character_id, []).append(row.alias)
    return result


async def delete_character_aliases(session: AsyncSession, character_id: str) -> int:
    """删除单个角色的全部别名。"""
    return await character_alias_repo.delete_by_character(session, character_id)


async def delete_character_aliases_by_ids(
    session: AsyncSession,
    character_ids: list[str],
) -> int:
    """批量删除角色别名。"""
    return await character_alias_repo.delete_by_characters(session, character_ids)


async def delete_character_aliases_by_project(session: AsyncSession, project_id: str) -> int:
    """删除项目内全部角色的别名。"""
    return await character_alias_repo.delete_by_project(session, project_id)


# ============== 世界书条目别名 ==============


async def replace_entry_aliases(
    session: AsyncSession,
    entry_id: str,
    aliases: list[str],
) -> list[str]:
    """全量替换世界书条目别名，返回实际写入的别名。"""
    rows = await world_info_entry_alias_repo.replace_for_entry(session, entry_id, aliases)
    return [row.alias for row in rows]


async def list_entry_aliases(session: AsyncSession, entry_id: str) -> list[str]:
    """获取单个条目的别名，按用户输入顺序返回。"""
    rows = await world_info_entry_alias_repo.list_by_entry(session, entry_id)
    return [row.alias for row in rows]


async def list_entry_aliases_by_ids(
    session: AsyncSession,
    entry_ids: list[str],
) -> dict[str, list[str]]:
    """批量获取条目别名，缺失的条目映射为空列表。"""
    result: dict[str, list[str]] = {entry_id: [] for entry_id in entry_ids}
    for row in await world_info_entry_alias_repo.list_by_entries(session, entry_ids):
        result.setdefault(row.entry_id, []).append(row.alias)
    return result


async def delete_entry_aliases(session: AsyncSession, entry_id: str) -> int:
    """删除单个条目的全部别名。"""
    return await world_info_entry_alias_repo.delete_by_entry(session, entry_id)


async def delete_entry_aliases_by_ids(session: AsyncSession, entry_ids: list[str]) -> int:
    """批量删除条目别名。"""
    return await world_info_entry_alias_repo.delete_by_entries(session, entry_ids)


async def delete_entry_aliases_by_world_info(
    session: AsyncSession,
    world_info_id: str,
) -> int:
    """删除某世界书全部条目的别名。"""
    return await world_info_entry_alias_repo.delete_by_world_info(session, world_info_id)
