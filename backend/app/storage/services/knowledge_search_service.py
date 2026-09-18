# -*- coding: utf-8 -*-
"""Knowledge Search Service - 世界书与角色的关键词检索业务逻辑层。

依据第一阶段实施方案 §5、§6.2 与 §8：
- 严格校验 query（去首尾空白后 1～200 字符，最多 8 个词），match（all/any），limit（1～20）；
- 严格校验与生成 base64url JSON cursor，包含协议版本、查询指纹与数据集指纹；
- 项目或实体发生增删改、启停、别名变更时，数据集指纹变化导致 cursor_stale；
- 正文切片（Search Excerpt）计算：最多 3 段，每段最多 240 字符，优先覆盖不同词，合并相邻区间，精准对应 1-based 行号；
- 序列化输出限制不超过 32,768 字符，超限时逐项收缩当前页候选。
"""

from __future__ import annotations

import base64
import bisect
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
import hashlib
import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import OpenFicError
from app.core.text_normalization import normalize_literal
from app.storage.repos import (
    character_alias_repo,
    knowledge_search_repo,
    world_info_entry_alias_repo,
)
from app.storage.services.knowledge_contracts import (
    MAX_CURSOR_CHARS,
    MAX_EXCERPT_CHARS,
    MAX_EXCERPTS_PER_ITEM,
    SERIALIZED_OUTPUT_BUDGET_CHARS,
    KnowledgeCursorPayload,
    KnowledgeErrorDetail,
    KnowledgeErrorCode,
    KnowledgeErrorResponse,
    KnowledgeKind,
    KnowledgeSearchItem,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    MatchedField,
    SearchExcerpt,
    SearchMatchMode,
    SearchReason,
)


class KnowledgeSearchError(OpenFicError):
    """知识检索业务异常，携带稳定的错误码。"""

    def __init__(
        self,
        code: KnowledgeErrorCode,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def to_response(self) -> KnowledgeErrorResponse:
        return KnowledgeErrorResponse(
            error=KnowledgeErrorDetail(
                code=self.code,
                message=self.message,
                retryable=self.retryable,
            )
        )


def compute_query_fingerprint(
    *,
    kind: KnowledgeKind,
    project_id: str,
    query: str,
    terms: Sequence[str],
    match: SearchMatchMode,
    limit: int,
) -> str:
    """计算查询参数的规范 SHA-256 指纹。"""
    canonical = {
        "kind": kind.value,
        "limit": limit,
        "match": match.value,
        "project_id": project_id,
        "query": query,
        "terms": list(terms),
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def compute_dataset_fingerprint(rows: Sequence[tuple[str, datetime | str]]) -> str:
    """根据作用域内可见实体的有序 (id, updated_at) 集合计算数据集版本指纹。"""
    hasher = hashlib.sha256()
    for entity_id, updated_at in rows:
        ts = (
            updated_at.isoformat()
            if isinstance(updated_at, datetime)
            else str(updated_at)
        )
        hasher.update(f"{entity_id}:{ts}\n".encode("utf-8"))
    return f"sha256:{hasher.hexdigest()}"


def compute_content_version(content: str | None) -> str:
    """计算正文原始 UTF-8 字节的 SHA-256 版本标识。"""
    raw = (content or "").encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def encode_cursor(payload: KnowledgeCursorPayload) -> str:
    """把 Cursor 结构体编码为 base64url JSON 字符串。"""
    raw = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor_str: str) -> KnowledgeCursorPayload:
    """解码并校验 Cursor 字符串结构与长度。"""
    if len(cursor_str) > MAX_CURSOR_CHARS:
        raise KnowledgeSearchError(
            code=KnowledgeErrorCode.INVALID_CURSOR,
            message=f"Cursor 长度超过限制 ({MAX_CURSOR_CHARS} 字符)",
        )
    try:
        padded = cursor_str + "=" * ((4 - len(cursor_str) % 4) % 4)
        raw_bytes = base64.urlsafe_b64decode(padded)
        data = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Cursor payload 必须为 JSON 对象")
        return KnowledgeCursorPayload.model_validate(data)
    except Exception as exc:
        raise KnowledgeSearchError(
            code=KnowledgeErrorCode.INVALID_CURSOR,
            message=f"无效的 Cursor: {exc}",
        ) from exc


def validate_cursor(
    payload: KnowledgeCursorPayload,
    *,
    kind: KnowledgeKind,
    project_id: str,
    query_fingerprint: str,
    dataset_fingerprint: str,
    limit: int,
) -> int:
    """校验 Cursor 是否与当前查询条件及数据版本一致。

    Returns:
        下一页的 offset。

    Raises:
        KnowledgeSearchError: 格式/条件不符抛出 INVALID_CURSOR，数据版本过期抛出 CURSOR_STALE。
    """
    if payload.v != 1:
        raise KnowledgeSearchError(
            code=KnowledgeErrorCode.INVALID_CURSOR,
            message="不支持的 Cursor 版本",
        )
    if payload.kind != kind:
        raise KnowledgeSearchError(
            code=KnowledgeErrorCode.INVALID_CURSOR,
            message="Cursor 实体类别与当前查询不匹配",
        )
    if payload.project_id != project_id:
        raise KnowledgeSearchError(
            code=KnowledgeErrorCode.INVALID_CURSOR,
            message="Cursor 所属项目与当前查询不匹配",
        )
    if payload.limit != limit:
        raise KnowledgeSearchError(
            code=KnowledgeErrorCode.INVALID_CURSOR,
            message="Cursor 页大小与当前查询不匹配",
        )
    if payload.query_fingerprint != query_fingerprint:
        raise KnowledgeSearchError(
            code=KnowledgeErrorCode.INVALID_CURSOR,
            message="Cursor 查询条件指纹与当前查询不匹配",
        )
    if payload.dataset_fingerprint != dataset_fingerprint:
        raise KnowledgeSearchError(
            code=KnowledgeErrorCode.CURSOR_STALE,
            message="数据已更新，旧 Cursor 已失效，请从第一页重新发起搜索",
            retryable=True,
        )
    return payload.offset


def _build_line_offsets(content: str) -> list[tuple[int, int]]:
    """遵循 splitlines(keepends=True) 逻辑构建每一行的字符偏移范围 [start, end)。"""
    if not content:
        return []
    lines = content.splitlines(keepends=True)
    offsets: list[tuple[int, int]] = []
    current = 0
    for line in lines:
        line_len = len(line)
        offsets.append((current, current + line_len))
        current += line_len
    return offsets


def _get_line_number(line_offsets: list[tuple[int, int]], char_index: int) -> int:
    """根据字符偏移查找 1-indexed 行号。"""
    if not line_offsets:
        return 1
    idx = bisect.bisect_right(line_offsets, (char_index, float("inf"))) - 1
    if idx < 0:
        return 1
    if idx >= len(line_offsets):
        return len(line_offsets)
    return idx + 1


def extract_excerpts(
    content: str,
    terms: Sequence[str],
) -> tuple[list[SearchExcerpt], bool]:
    """提取正文中命中关键词的原文切片（最多 3 段，每段最多 240 字符）。

    Returns:
        (excerpts, excerpts_truncated)
    """
    if not content or not terms:
        return [], False

    norm_content = normalize_literal(content)
    content_len = len(content)

    all_occurrences: list[tuple[int, int, str]] = []
    term_first_occurrence: dict[str, tuple[int, int]] = {}

    for term in terms:
        norm_t = normalize_literal(term)
        if not norm_t:
            continue
        start = 0
        while True:
            pos = norm_content.find(norm_t, start)
            if pos == -1:
                break
            end = pos + len(norm_t)
            all_occurrences.append((pos, end, term))
            if term not in term_first_occurrence:
                term_first_occurrence[term] = (pos, end)
            start = pos + 1

    if not all_occurrences:
        return [], False

    all_occurrences.sort(key=lambda x: (x[0], x[1]))

    # 优先选取不同词的首次出现位置
    primary_targets = sorted(term_first_occurrence.values(), key=lambda x: x[0])
    targets: list[tuple[int, int]] = list(primary_targets)
    if len(targets) < MAX_EXCERPTS_PER_ITEM:
        covered_starts = {t[0] for t in targets}
        for pos, end, _ in all_occurrences:
            if pos not in covered_starts:
                targets.append((pos, end))
                covered_starts.add(pos)
                if len(targets) >= MAX_EXCERPTS_PER_ITEM:
                    break
    targets.sort(key=lambda x: x[0])

    def make_window(m_start: int, m_end: int) -> tuple[int, int]:
        m_len = m_end - m_start
        if m_len >= MAX_EXCERPT_CHARS:
            return (m_start, m_start + MAX_EXCERPT_CHARS)
        half_budget = (MAX_EXCERPT_CHARS - m_len) // 2
        w_start = max(0, m_start - half_budget)
        w_end = min(content_len, w_start + MAX_EXCERPT_CHARS)
        w_start = max(0, w_end - MAX_EXCERPT_CHARS)
        return (w_start, w_end)

    raw_windows = [make_window(s, e) for s, e in targets]

    # 合并重叠或相邻且合并后不超过 MAX_EXCERPT_CHARS 的区间
    merged_windows: list[tuple[int, int]] = []
    for w_start, w_end in raw_windows:
        if not merged_windows:
            merged_windows.append((w_start, w_end))
            continue
        prev_start, prev_end = merged_windows[-1]
        if w_start <= prev_end:
            potential_len = max(prev_end, w_end) - prev_start
            if potential_len <= MAX_EXCERPT_CHARS:
                merged_windows[-1] = (prev_start, max(prev_end, w_end))
            else:
                adjusted_start = max(prev_end, w_start)
                if adjusted_start < w_end:
                    merged_windows.append((adjusted_start, w_end))
        else:
            merged_windows.append((w_start, w_end))

    final_windows = merged_windows[:MAX_EXCERPTS_PER_ITEM]

    # 检查所有命中位置是否均被最终片段所包含
    def is_covered(pos: int, end: int) -> bool:
        return any(ws <= pos and end <= we for ws, we in final_windows)

    all_covered = all(is_covered(pos, end) for pos, end, _ in all_occurrences)
    excerpts_truncated = not all_covered

    line_offsets = _build_line_offsets(content)
    excerpts: list[SearchExcerpt] = []
    for ws, we in final_windows:
        excerpt_text = content[ws:we]
        line_start = _get_line_number(line_offsets, ws)
        line_end = _get_line_number(line_offsets, max(ws, we - 1))
        excerpts.append(
            SearchExcerpt(
                text=excerpt_text,
                start_offset=ws,
                end_offset=we,
                line_start=line_start,
                line_end=line_end,
            )
        )

    return excerpts, excerpts_truncated


def _serialize_response(response: KnowledgeSearchResponse) -> str:
    """按契约规范序列化为紧凑 JSON 字符串。"""
    return json.dumps(
        response.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _apply_serialized_output_budget(
    *,
    response: KnowledgeSearchResponse,
    kind: KnowledgeKind,
    project_id: str,
    query_fingerprint: str,
    dataset_fingerprint: str,
    limit: int,
    offset: int,
    total_matches: int,
) -> KnowledgeSearchResponse:
    """若序列化 JSON 超过 32,768 字符，从末尾缩减候选并调整游标。"""
    serialized = _serialize_response(response)
    if len(serialized) <= SERIALIZED_OUTPUT_BUDGET_CHARS:
        return response

    items = list(response.items)
    while len(items) > 1 and len(serialized) > SERIALIZED_OUTPUT_BUDGET_CHARS:
        items.pop()
        has_more = (offset + len(items)) < total_matches
        next_cursor = (
            encode_cursor(
                KnowledgeCursorPayload(
                    v=1,
                    kind=kind,
                    project_id=project_id,
                    query_fingerprint=query_fingerprint,
                    dataset_fingerprint=dataset_fingerprint,
                    limit=limit,
                    offset=offset + len(items),
                )
            )
            if has_more
            else None
        )
        response = KnowledgeSearchResponse(
            items=items,
            returned_count=len(items),
            total_matches=total_matches,
            has_more=has_more,
            next_cursor=next_cursor,
            match_scope="literal_terms",
            reason=response.reason,
        )
        serialized = _serialize_response(response)

    if len(serialized) > SERIALIZED_OUTPUT_BUDGET_CHARS:
        raise KnowledgeSearchError(
            code=KnowledgeErrorCode.OUTPUT_BUDGET_EXCEEDED,
            message="搜索结果超出单次输出序列化预算上限 (32,768 字符)",
        )

    return response


async def _do_search_world_entries(
    session: AsyncSession,
    project_id: str,
    request: KnowledgeSearchRequest,
) -> KnowledgeSearchResponse:
    """在统一事务快照内执行世界书检索。"""
    project_exists = await knowledge_search_repo.check_project_exists(session, project_id)
    if not project_exists:
        raise KnowledgeSearchError(
            code=KnowledgeErrorCode.CONTEXT_ERROR,
            message=f"项目不存在: {project_id}",
        )

    world_info_id = await knowledge_search_repo.get_world_info_id_by_project(session, project_id)
    if world_info_id is None:
        return KnowledgeSearchResponse(
            items=[],
            returned_count=0,
            total_matches=0,
            has_more=False,
            next_cursor=None,
            match_scope="literal_terms",
            reason=SearchReason.NO_WORLD_BOOK,
        )

    query_fingerprint = compute_query_fingerprint(
        kind=KnowledgeKind.WORLD_ENTRY,
        project_id=project_id,
        query=request.query,
        terms=request.terms,
        match=request.match,
        limit=request.limit,
    )

    fp_rows = await knowledge_search_repo.get_world_entries_fingerprint_rows(session, world_info_id)
    dataset_fingerprint = compute_dataset_fingerprint(fp_rows)

    if request.cursor is not None:
        cursor_payload = decode_cursor(request.cursor)
        offset = validate_cursor(
            cursor_payload,
            kind=KnowledgeKind.WORLD_ENTRY,
            project_id=project_id,
            query_fingerprint=query_fingerprint,
            dataset_fingerprint=dataset_fingerprint,
            limit=request.limit,
        )
    else:
        offset = 0

    total_matches = await knowledge_search_repo.count_world_entries(
        session,
        world_info_id=world_info_id,
        query=request.query,
        terms=request.terms,
        match_mode=request.match,
    )

    if total_matches == 0 or offset >= total_matches:
        return KnowledgeSearchResponse(
            items=[],
            returned_count=0,
            total_matches=total_matches,
            has_more=False,
            next_cursor=None,
            match_scope="literal_terms",
        )

    entries = await knowledge_search_repo.search_world_entries_page(
        session,
        world_info_id=world_info_id,
        query=request.query,
        terms=request.terms,
        match_mode=request.match,
        limit=request.limit,
        offset=offset,
    )

    entry_ids = [entry.id for entry in entries]
    alias_records = await world_info_entry_alias_repo.list_by_entries(session, entry_ids)
    aliases_by_entry: dict[str, list[str]] = defaultdict(list)
    for a in alias_records:
        aliases_by_entry[a.entry_id].append(a.alias)

    items: list[KnowledgeSearchItem] = []
    for entry in entries:
        aliases = aliases_by_entry.get(entry.id, [])
        item = _build_search_item(
            entity_id=entry.id,
            kind=KnowledgeKind.WORLD_ENTRY,
            name=entry.name,
            aliases=aliases,
            content=entry.content or "",
            query=request.query,
            terms=request.terms,
        )
        items.append(item)

    has_more = (offset + len(items)) < total_matches
    next_cursor = (
        encode_cursor(
            KnowledgeCursorPayload(
                v=1,
                kind=KnowledgeKind.WORLD_ENTRY,
                project_id=project_id,
                query_fingerprint=query_fingerprint,
                dataset_fingerprint=dataset_fingerprint,
                limit=request.limit,
                offset=offset + len(items),
            )
        )
        if has_more
        else None
    )

    response = KnowledgeSearchResponse(
        items=items,
        returned_count=len(items),
        total_matches=total_matches,
        has_more=has_more,
        next_cursor=next_cursor,
        match_scope="literal_terms",
    )

    return _apply_serialized_output_budget(
        response=response,
        kind=KnowledgeKind.WORLD_ENTRY,
        project_id=project_id,
        query_fingerprint=query_fingerprint,
        dataset_fingerprint=dataset_fingerprint,
        limit=request.limit,
        offset=offset,
        total_matches=total_matches,
    )


async def _do_search_characters(
    session: AsyncSession,
    project_id: str,
    request: KnowledgeSearchRequest,
) -> KnowledgeSearchResponse:
    """在统一事务快照内执行角色检索。"""
    project_exists = await knowledge_search_repo.check_project_exists(session, project_id)
    if not project_exists:
        raise KnowledgeSearchError(
            code=KnowledgeErrorCode.CONTEXT_ERROR,
            message=f"项目不存在: {project_id}",
        )

    query_fingerprint = compute_query_fingerprint(
        kind=KnowledgeKind.CHARACTER,
        project_id=project_id,
        query=request.query,
        terms=request.terms,
        match=request.match,
        limit=request.limit,
    )

    fp_rows = await knowledge_search_repo.get_characters_fingerprint_rows(session, project_id)
    dataset_fingerprint = compute_dataset_fingerprint(fp_rows)

    if request.cursor is not None:
        cursor_payload = decode_cursor(request.cursor)
        offset = validate_cursor(
            cursor_payload,
            kind=KnowledgeKind.CHARACTER,
            project_id=project_id,
            query_fingerprint=query_fingerprint,
            dataset_fingerprint=dataset_fingerprint,
            limit=request.limit,
        )
    else:
        offset = 0

    total_matches = await knowledge_search_repo.count_characters(
        session,
        project_id=project_id,
        query=request.query,
        terms=request.terms,
        match_mode=request.match,
    )

    if total_matches == 0 or offset >= total_matches:
        return KnowledgeSearchResponse(
            items=[],
            returned_count=0,
            total_matches=total_matches,
            has_more=False,
            next_cursor=None,
            match_scope="literal_terms",
        )

    characters = await knowledge_search_repo.search_characters_page(
        session,
        project_id=project_id,
        query=request.query,
        terms=request.terms,
        match_mode=request.match,
        limit=request.limit,
        offset=offset,
    )

    character_ids = [c.id for c in characters]
    alias_records = await character_alias_repo.list_by_characters(session, character_ids)
    aliases_by_character: dict[str, list[str]] = defaultdict(list)
    for a in alias_records:
        aliases_by_character[a.character_id].append(a.alias)

    items: list[KnowledgeSearchItem] = []
    for c in characters:
        aliases = aliases_by_character.get(c.id, [])
        item = _build_search_item(
            entity_id=c.id,
            kind=KnowledgeKind.CHARACTER,
            name=c.name,
            aliases=aliases,
            content=c.description or "",
            query=request.query,
            terms=request.terms,
        )
        items.append(item)

    has_more = (offset + len(items)) < total_matches
    next_cursor = (
        encode_cursor(
            KnowledgeCursorPayload(
                v=1,
                kind=KnowledgeKind.CHARACTER,
                project_id=project_id,
                query_fingerprint=query_fingerprint,
                dataset_fingerprint=dataset_fingerprint,
                limit=request.limit,
                offset=offset + len(items),
            )
        )
        if has_more
        else None
    )

    response = KnowledgeSearchResponse(
        items=items,
        returned_count=len(items),
        total_matches=total_matches,
        has_more=has_more,
        next_cursor=next_cursor,
        match_scope="literal_terms",
    )

    return _apply_serialized_output_budget(
        response=response,
        kind=KnowledgeKind.CHARACTER,
        project_id=project_id,
        query_fingerprint=query_fingerprint,
        dataset_fingerprint=dataset_fingerprint,
        limit=request.limit,
        offset=offset,
        total_matches=total_matches,
    )


def _build_search_item(
    *,
    entity_id: str,
    kind: KnowledgeKind,
    name: str,
    aliases: list[str],
    content: str,
    query: str,
    terms: Sequence[str],
) -> KnowledgeSearchItem:
    """构建单个实体的检索结果项，计算命中字段、命中词、正文切片与内容版本。"""
    norm_name = normalize_literal(name)
    norm_query = normalize_literal(query.strip())
    norm_aliases = [normalize_literal(a) for a in aliases]
    norm_content = normalize_literal(content)

    name_matched = any(normalize_literal(t) in norm_name for t in terms) or (norm_name == norm_query)
    alias_matched = any(
        any(normalize_literal(t) in na for t in terms) or (na == norm_query)
        for na in norm_aliases
    )
    content_matched = any(normalize_literal(t) in norm_content for t in terms)

    matched_fields: list[MatchedField] = []
    if name_matched:
        matched_fields.append("name")
    if alias_matched:
        matched_fields.append("alias")
    if content_matched:
        matched_fields.append("content")

    # 计算命中词，保持查询词顺序
    matched_terms: list[str] = [
        t
        for t in terms
        if (
            normalize_literal(t) in norm_name
            or any(normalize_literal(t) in na for na in norm_aliases)
            or normalize_literal(t) in norm_content
        )
    ]
    if not matched_terms and (norm_name == norm_query or any(na == norm_query for na in norm_aliases)):
        matched_terms = list(terms)

    # 仅当正文命中时提取片段
    if "content" in matched_fields:
        excerpts, excerpts_truncated = extract_excerpts(content, terms)
    else:
        excerpts, excerpts_truncated = [], False

    content_version = compute_content_version(content)

    return KnowledgeSearchItem(
        id=entity_id,
        kind=kind,
        name=name,
        aliases=aliases,
        matched_fields=matched_fields,
        matched_terms=matched_terms,
        excerpts=excerpts,
        content_version=content_version,
        excerpts_truncated=excerpts_truncated,
    )


async def search_world_entries(
    session: AsyncSession,
    project_id: str,
    request: KnowledgeSearchRequest,
) -> KnowledgeSearchResponse:
    """检索世界书条目。如果当前 session 未处于事务中，显式开启只读事务快照。"""
    if not session.in_transaction():
        async with session.begin():
            return await _do_search_world_entries(session, project_id, request)
    return await _do_search_world_entries(session, project_id, request)


async def search_characters(
    session: AsyncSession,
    project_id: str,
    request: KnowledgeSearchRequest,
) -> KnowledgeSearchResponse:
    """检索角色。如果当前 session 未处于事务中，显式开启只读事务快照。"""
    if not session.in_transaction():
        async with session.begin():
            return await _do_search_characters(session, project_id, request)
    return await _do_search_characters(session, project_id, request)
