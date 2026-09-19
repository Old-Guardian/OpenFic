# -*- coding: utf-8 -*-
"""WorldInfoEntryAlias Repository - 世界书条目别名数据访问层。"""

from typing import Any, cast

from sqlalchemy import delete as sql_delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.core.text_normalization import normalize_literal
from app.storage.models.world_info_entry import WorldInfoEntry
from app.storage.models.world_info_entry_alias import WorldInfoEntryAlias


async def list_by_entry(session: AsyncSession, entry_id: str) -> list[WorldInfoEntryAlias]:
    """获取某条目的别名，按用户输入顺序返回。"""
    result = await session.execute(
        select(WorldInfoEntryAlias)
        .where(col(WorldInfoEntryAlias.entry_id) == entry_id)
        .order_by(col(WorldInfoEntryAlias.position), col(WorldInfoEntryAlias.id))
    )
    return list(result.scalars().all())


async def list_by_entries(
    session: AsyncSession,
    entry_ids: list[str],
) -> list[WorldInfoEntryAlias]:
    """批量获取多条目的别名，避免逐条查询。"""
    if not entry_ids:
        return []
    result = await session.execute(
        select(WorldInfoEntryAlias)
        .where(col(WorldInfoEntryAlias.entry_id).in_(entry_ids))
        .order_by(
            col(WorldInfoEntryAlias.entry_id),
            col(WorldInfoEntryAlias.position),
            col(WorldInfoEntryAlias.id),
        )
    )
    return list(result.scalars().all())


async def replace_for_entry(
    session: AsyncSession,
    entry_id: str,
    aliases: list[str],
) -> list[WorldInfoEntryAlias]:
    """整体替换某条目的别名。

    调用方需保证 ``aliases`` 已去除首尾空白、非空且按字面匹配规则去重；
    ``normalized_alias`` 在此统一派生，避免与 ``alias`` 出现不一致。
    """
    await session.execute(
        sql_delete(WorldInfoEntryAlias).where(
            col(WorldInfoEntryAlias.entry_id) == entry_id
        )
    )
    rows = [
        WorldInfoEntryAlias(
            entry_id=entry_id,
            alias=alias,
            normalized_alias=normalize_literal(alias),
            position=position,
        )
        for position, alias in enumerate(aliases)
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def delete_by_entry(session: AsyncSession, entry_id: str) -> int:
    """删除某条目的全部别名。"""
    result = await session.execute(
        sql_delete(WorldInfoEntryAlias).where(
            col(WorldInfoEntryAlias.entry_id) == entry_id
        )
    )
    await session.flush()
    return cast("CursorResult[Any]", result).rowcount


async def delete_by_entries(session: AsyncSession, entry_ids: list[str]) -> int:
    """批量删除多条目的别名。"""
    if not entry_ids:
        return 0
    result = await session.execute(
        sql_delete(WorldInfoEntryAlias).where(
            col(WorldInfoEntryAlias.entry_id).in_(entry_ids)
        )
    )
    await session.flush()
    return cast("CursorResult[Any]", result).rowcount


async def delete_by_world_info(session: AsyncSession, world_info_id: str) -> int:
    """删除某世界书全部条目的别名。

    需在删除条目行之前调用：子查询依赖 ``world_info_entries`` 仍然存在。
    """
    entry_ids = select(col(WorldInfoEntry.id)).where(
        col(WorldInfoEntry.world_info_id) == world_info_id
    )
    result = await session.execute(
        sql_delete(WorldInfoEntryAlias).where(
            col(WorldInfoEntryAlias.entry_id).in_(entry_ids)
        )
    )
    await session.flush()
    return cast("CursorResult[Any]", result).rowcount
