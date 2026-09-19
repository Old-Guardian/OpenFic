# -*- coding: utf-8 -*-
"""CharacterAlias Repository - 角色别名数据访问层。"""

from typing import Any, cast

from sqlalchemy import delete as sql_delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.core.text_normalization import normalize_literal
from app.storage.models.character import Character
from app.storage.models.character_alias import CharacterAlias


async def list_by_character(
    session: AsyncSession,
    character_id: str,
) -> list[CharacterAlias]:
    """获取某角色的别名，按用户输入顺序返回。"""
    result = await session.execute(
        select(CharacterAlias)
        .where(col(CharacterAlias.character_id) == character_id)
        .order_by(col(CharacterAlias.position), col(CharacterAlias.id))
    )
    return list(result.scalars().all())


async def list_by_characters(
    session: AsyncSession,
    character_ids: list[str],
) -> list[CharacterAlias]:
    """批量获取多个角色的别名，避免逐条查询。"""
    if not character_ids:
        return []
    result = await session.execute(
        select(CharacterAlias)
        .where(col(CharacterAlias.character_id).in_(character_ids))
        .order_by(
            col(CharacterAlias.character_id),
            col(CharacterAlias.position),
            col(CharacterAlias.id),
        )
    )
    return list(result.scalars().all())


async def replace_for_character(
    session: AsyncSession,
    character_id: str,
    aliases: list[str],
) -> list[CharacterAlias]:
    """整体替换某角色的别名。

    调用方需保证 ``aliases`` 已去除首尾空白、非空且按字面匹配规则去重；
    ``normalized_alias`` 在此统一派生，避免与 ``alias`` 出现不一致。
    """
    await session.execute(
        sql_delete(CharacterAlias).where(
            col(CharacterAlias.character_id) == character_id
        )
    )
    rows = [
        CharacterAlias(
            character_id=character_id,
            alias=alias,
            normalized_alias=normalize_literal(alias),
            position=position,
        )
        for position, alias in enumerate(aliases)
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def delete_by_character(session: AsyncSession, character_id: str) -> int:
    """删除某角色的全部别名。"""
    result = await session.execute(
        sql_delete(CharacterAlias).where(
            col(CharacterAlias.character_id) == character_id
        )
    )
    await session.flush()
    return cast("CursorResult[Any]", result).rowcount


async def delete_by_characters(session: AsyncSession, character_ids: list[str]) -> int:
    """批量删除多个角色的别名。"""
    if not character_ids:
        return 0
    result = await session.execute(
        sql_delete(CharacterAlias).where(
            col(CharacterAlias.character_id).in_(character_ids)
        )
    )
    await session.flush()
    return cast("CursorResult[Any]", result).rowcount


async def delete_by_project(session: AsyncSession, project_id: str) -> int:
    """删除某项目全部角色的别名。

    需在删除角色行之前调用：子查询依赖 ``characters`` 仍然存在。
    """
    character_ids = select(col(Character.id)).where(col(Character.project_id) == project_id)
    result = await session.execute(
        sql_delete(CharacterAlias).where(col(CharacterAlias.character_id).in_(character_ids))
    )
    await session.flush()
    return cast("CursorResult[Any]", result).rowcount
