# -*- coding: utf-8 -*-
"""Knowledge Search Repository - 世界书与角色的底层 SQL 检索层。

依据第一阶段实施方案 §5.3 与 §8：
- 使用 SQLite 参数化 `instr(lower(column), :term) > 0` 进行字面子串匹配；
- 别名使用 EXISTS 子查询，避免 JOIN 导致结果膨胀与重复；
- 5 档稳定排序：
    1. 正式名称与完整 query 精确匹配
    2. 任一别名与完整 query 精确匹配
    3. 名称包含任意查询词
    4. 别名包含任意查询词
    5. 仅正文命中
    6. 同档按命中词数量降序，再按实体 ID 升序
- 两阶段分页：先在数据库统计总数与分页切片，再批量加载当前页正文及别名。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, case, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.core.text_normalization import normalize_literal
from app.storage.models.character import Character
from app.storage.models.character_alias import CharacterAlias
from app.storage.models.project import Project
from app.storage.models.world_info import WorldInfo
from app.storage.models.world_info_entry import WorldInfoEntry
from app.storage.models.world_info_entry_alias import WorldInfoEntryAlias
from app.storage.services.knowledge_contracts import SearchMatchMode


def _build_search_predicates_and_ordering(
    *,
    entity_model: type[WorldInfoEntry] | type[Character],
    alias_model: type[WorldInfoEntryAlias] | type[CharacterAlias],
    entity_id_col: Any,
    fk_col: Any,
    name_col: Any,
    content_col: Any,
    query: str,
    terms: Sequence[str],
    match_mode: SearchMatchMode,
) -> tuple[Any, Any, Any]:
    """构建匹配谓词、5 档 Rank 表达式和排序子句。

    Returns:
        (match_predicate, rank_expression, matched_terms_count_expression)
    """
    query_norm = normalize_literal(query.strip())
    terms_norm = [normalize_literal(term) for term in terms if term]

    # 1. 正式名称精确匹配
    exact_name = func.lower(name_col) == query_norm

    # 2. 任一别名精确匹配
    exact_alias = exists(
        select(1).where(
            fk_col == entity_id_col,
            or_(
                func.lower(col(alias_model.alias)) == query_norm,
                col(alias_model.normalized_alias) == query_norm,
            ),
        )
    )

    # 3. 名称包含任意查询词
    name_contains_any = (
        or_(*[func.instr(func.lower(name_col), t) > 0 for t in terms_norm])
        if terms_norm
        else literal(False)
    )

    # 4. 别名包含任意查询词
    alias_contains_any = (
        exists(
            select(1).where(
                fk_col == entity_id_col,
                or_(*[func.instr(func.lower(col(alias_model.alias)), t) > 0 for t in terms_norm]),
            )
        )
        if terms_norm
        else literal(False)
    )

    # 5 档排序分值计算
    rank_expr = case(
        (exact_name, 1),
        (exact_alias, 2),
        (name_contains_any, 3),
        (alias_contains_any, 4),
        else_=5,
    )

    # 针对每个词的命中判断
    term_hit_conds = [
        or_(
            func.instr(func.lower(name_col), t) > 0,
            func.instr(func.lower(content_col), t) > 0,
            exists(
                select(1).where(
                    fk_col == entity_id_col,
                    func.instr(func.lower(col(alias_model.alias)), t) > 0,
                )
            ),
        )
        for t in terms_norm
    ]

    # 命中词数量表达式
    if term_hit_conds:
        matched_term_count_expr = sum(
            case((cond, 1), else_=0) for cond in term_hit_conds
        )
    else:
        matched_term_count_expr = literal(0)

    # 过滤条件 (all vs any)
    if match_mode is SearchMatchMode.ALL:
        match_pred = and_(*term_hit_conds) if term_hit_conds else literal(True)
    else:
        match_pred = or_(*term_hit_conds) if term_hit_conds else literal(False)

    return match_pred, rank_expr, matched_term_count_expr


async def check_project_exists(session: AsyncSession, project_id: str) -> bool:
    """检查项目是否存在。"""
    result = await session.execute(
        select(1).select_from(Project).where(col(Project.id) == project_id)
    )
    return result.scalar() is not None


async def get_world_info_id_by_project(session: AsyncSession, project_id: str) -> str | None:
    """获取项目绑定的世界书 ID。"""
    result = await session.execute(
        select(col(WorldInfo.id)).where(col(WorldInfo.project_id) == project_id)
    )
    return result.scalar_one_or_none()


async def get_world_entries_fingerprint_rows(
    session: AsyncSession, world_info_id: str
) -> list[tuple[str, datetime]]:
    """获取世界书内可见（启用）条目的 (id, updated_at) 集合，按 id 升序。"""
    result = await session.execute(
        select(col(WorldInfoEntry.id), col(WorldInfoEntry.updated_at))
        .where(
            col(WorldInfoEntry.world_info_id) == world_info_id,
            col(WorldInfoEntry.is_enabled) == True,  # noqa: E712
        )
        .order_by(col(WorldInfoEntry.id).asc())
    )
    return [(row[0], row[1]) for row in result.all()]


async def get_characters_fingerprint_rows(
    session: AsyncSession, project_id: str
) -> list[tuple[str, datetime]]:
    """获取项目内全部角色的 (id, updated_at) 集合，按 id 升序。"""
    result = await session.execute(
        select(col(Character.id), col(Character.updated_at))
        .where(col(Character.project_id) == project_id)
        .order_by(col(Character.id).asc())
    )
    return [(row[0], row[1]) for row in result.all()]


async def count_world_entries(
    session: AsyncSession,
    *,
    world_info_id: str,
    query: str,
    terms: Sequence[str],
    match_mode: SearchMatchMode,
) -> int:
    """统计满足检索条件的世界书条目总数。"""
    match_pred, _, _ = _build_search_predicates_and_ordering(
        entity_model=WorldInfoEntry,
        alias_model=WorldInfoEntryAlias,
        entity_id_col=col(WorldInfoEntry.id),
        fk_col=col(WorldInfoEntryAlias.entry_id),
        name_col=col(WorldInfoEntry.name),
        content_col=col(WorldInfoEntry.content),
        query=query,
        terms=terms,
        match_mode=match_mode,
    )
    stmt = (
        select(func.count())
        .select_from(WorldInfoEntry)
        .where(
            col(WorldInfoEntry.world_info_id) == world_info_id,
            col(WorldInfoEntry.is_enabled) == True,  # noqa: E712
            match_pred,
        )
    )
    result = await session.execute(stmt)
    return int(result.scalar() or 0)


async def search_world_entries_page(
    session: AsyncSession,
    *,
    world_info_id: str,
    query: str,
    terms: Sequence[str],
    match_mode: SearchMatchMode,
    limit: int,
    offset: int,
) -> list[WorldInfoEntry]:
    """在数据库完成匹配与 5 档稳定排序，仅获取当前页条目实体。"""
    match_pred, rank_expr, term_count_expr = _build_search_predicates_and_ordering(
        entity_model=WorldInfoEntry,
        alias_model=WorldInfoEntryAlias,
        entity_id_col=col(WorldInfoEntry.id),
        fk_col=col(WorldInfoEntryAlias.entry_id),
        name_col=col(WorldInfoEntry.name),
        content_col=col(WorldInfoEntry.content),
        query=query,
        terms=terms,
        match_mode=match_mode,
    )
    stmt = (
        select(WorldInfoEntry)
        .where(
            col(WorldInfoEntry.world_info_id) == world_info_id,
            col(WorldInfoEntry.is_enabled) == True,  # noqa: E712
            match_pred,
        )
        .order_by(
            rank_expr.asc(),
            term_count_expr.desc(),
            col(WorldInfoEntry.id).asc(),
        )
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_characters(
    session: AsyncSession,
    *,
    project_id: str,
    query: str,
    terms: Sequence[str],
    match_mode: SearchMatchMode,
) -> int:
    """统计满足检索条件的角色总数。"""
    match_pred, _, _ = _build_search_predicates_and_ordering(
        entity_model=Character,
        alias_model=CharacterAlias,
        entity_id_col=col(Character.id),
        fk_col=col(CharacterAlias.character_id),
        name_col=col(Character.name),
        content_col=col(Character.description),
        query=query,
        terms=terms,
        match_mode=match_mode,
    )
    stmt = (
        select(func.count())
        .select_from(Character)
        .where(
            col(Character.project_id) == project_id,
            match_pred,
        )
    )
    result = await session.execute(stmt)
    return int(result.scalar() or 0)


async def search_characters_page(
    session: AsyncSession,
    *,
    project_id: str,
    query: str,
    terms: Sequence[str],
    match_mode: SearchMatchMode,
    limit: int,
    offset: int,
) -> list[Character]:
    """在数据库完成匹配与 5 档稳定排序，仅获取当前页角色实体。"""
    match_pred, rank_expr, term_count_expr = _build_search_predicates_and_ordering(
        entity_model=Character,
        alias_model=CharacterAlias,
        entity_id_col=col(Character.id),
        fk_col=col(CharacterAlias.character_id),
        name_col=col(Character.name),
        content_col=col(Character.description),
        query=query,
        terms=terms,
        match_mode=match_mode,
    )
    stmt = (
        select(Character)
        .where(
            col(Character.project_id) == project_id,
            match_pred,
        )
        .order_by(
            rank_expr.asc(),
            term_count_expr.desc(),
            col(Character.id).asc(),
        )
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
