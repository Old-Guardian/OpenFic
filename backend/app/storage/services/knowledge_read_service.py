# -*- coding: utf-8 -*-
"""Knowledge Read Service - 世界书与角色的按 ID 批量分段读取业务逻辑层。

依据第一阶段实施方案 §6、§7.1 与 §8：
- 严格按当前运行时项目校验作用域：不存在、跨项目或未绑定世界书的条目一律
  ``not_found``，禁用条目 ``disabled``，两者都不泄露名称、版本或正文长度；
- 正文版本使用原始 UTF-8 字节的 SHA-256；续读（start_offset > 0）必须携带
  一致的 ``expected_version``，否则返回 ``version_conflict``；
- 区间按 Unicode code point 计数、左闭右开，多段续读拼接必须严格等于原文；
  长单行按字符截断，不整行丢弃，避免无限重试；
- 每次批量正文总量 ≤ 16,000 字符，按输入顺序分配；序列化 JSON ≤ 32,768
  字符，超限时先缩短最后一个区间，剩余项降级为最小化 ``budget_exhausted``；
- 一次请求只做有界次数的批量 SELECT，不做逐项查询。
"""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import OpenFicError
from app.storage.database import read_snapshot
from app.storage.repos import knowledge_search_repo
from app.storage.services.knowledge_contracts import (
    READ_CONTENT_BUDGET_CHARS,
    SERIALIZED_OUTPUT_BUDGET_CHARS,
    KnowledgeErrorCode,
    KnowledgeErrorDetail,
    KnowledgeErrorResponse,
    KnowledgeKind,
    KnowledgeReadItem,
    KnowledgeReadRequest,
    KnowledgeReadResponse,
    ReadStatus,
    compute_content_version,
    locate_line_and_offset,
)


class KnowledgeReadError(OpenFicError):
    """知识读取业务异常，携带稳定的错误码。"""

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


def _build_response(items: list[KnowledgeReadItem]) -> KnowledgeReadResponse:
    """由条目列表推导顶层计数，保证与契约校验一致。"""
    return KnowledgeReadResponse(
        items=items,
        returned_count=sum(1 for item in items if item.status is ReadStatus.OK),
        partial_failure=any(item.status is not ReadStatus.OK for item in items),
        budget_exhausted=any(
            item.status is ReadStatus.BUDGET_EXHAUSTED for item in items
        ),
    )


def _serialize_response(response: KnowledgeReadResponse) -> str:
    """按契约规范序列化为紧凑 JSON 字符串。"""
    return json.dumps(
        response.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _build_ok_item(
    *,
    entity_id: str,
    name: str,
    content: str,
    content_version: str,
    start_offset: int,
    length: int,
) -> KnowledgeReadItem:
    """构建一个 ``ok`` 区间；``length`` 为 0 时表示已读到正文末尾。"""
    end_offset = start_offset + length
    total_chars = len(content)
    start_line, start_line_offset = locate_line_and_offset(content, start_offset)
    truncated = end_offset < total_chars
    return KnowledgeReadItem(
        id=entity_id,
        status=ReadStatus.OK,
        name=name,
        content_version=content_version,
        total_chars=total_chars,
        start_offset=start_offset,
        end_offset=end_offset,
        start_line=start_line,
        start_line_offset=start_line_offset,
        content=content[start_offset:end_offset],
        truncated=truncated,
        next_start_offset=end_offset if truncated else None,
    )


def _with_content(item: KnowledgeReadItem, content: str) -> KnowledgeReadItem:
    """在保持起始位置不变的前提下，用更短的正文区间替换 ``ok`` 项。"""
    assert item.start_offset is not None
    assert item.total_chars is not None
    end_offset = item.start_offset + len(content)
    truncated = end_offset < item.total_chars
    return KnowledgeReadItem(
        id=item.id,
        status=ReadStatus.OK,
        name=item.name,
        content_version=item.content_version,
        total_chars=item.total_chars,
        start_offset=item.start_offset,
        end_offset=end_offset,
        start_line=item.start_line,
        start_line_offset=item.start_line_offset,
        content=content,
        truncated=truncated,
        next_start_offset=end_offset if truncated else None,
    )


def _minimal_failure_item(item: KnowledgeReadItem, status: ReadStatus) -> KnowledgeReadItem:
    """把已读取项降级为不含正文的最小化失败项，保留原起始 offset 供重试。"""
    return KnowledgeReadItem(id=item.id, status=status, start_offset=item.start_offset)


def _last_ok_index(items: list[KnowledgeReadItem]) -> int | None:
    for index in range(len(items) - 1, -1, -1):
        if items[index].status is ReadStatus.OK:
            return index
    return None


def _last_shrinkable_index(items: list[KnowledgeReadItem]) -> int | None:
    """找到最后一个可继续缩短的 ``ok`` 项。

    区间必须至少前进一个 Unicode 字符，因此正文长度已为 1 的项不能再缩短。
    """
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if item.status is ReadStatus.OK and item.content and len(item.content) > 1:
            return index
    return None


def _largest_fitting_item(
    items: list[KnowledgeReadItem], index: int
) -> KnowledgeReadItem | None:
    """二分查找能装入序列化预算的最长正文区间。

    更短的正文只会产生同样短或更短的序列化结果，因此可以二分；无法装入
    哪怕一个字符时返回 ``None``，由调用方降级为 ``budget_exhausted``。
    """
    item = items[index]
    content = item.content or ""

    def serialized_length(length: int) -> int:
        probe = list(items)
        probe[index] = _with_content(item, content[:length])
        return len(_serialize_response(_build_response(probe)))

    if serialized_length(1) > SERIALIZED_OUTPUT_BUDGET_CHARS:
        return None

    low, high = 1, len(content) - 1
    best = 1
    while low <= high:
        mid = (low + high) // 2
        if serialized_length(mid) <= SERIALIZED_OUTPUT_BUDGET_CHARS:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return _with_content(item, content[:best])


def _apply_serialized_output_budget(
    items: list[KnowledgeReadItem],
) -> list[KnowledgeReadItem]:
    """把序列化结果压到 32,768 字符以内。

    优先缩短最后一个可缩短的 ``ok`` 区间；装不下任何正文时把该项降级为
    ``budget_exhausted``，再向前处理，直到整体装入预算。
    """
    result = list(items)
    while len(_serialize_response(_build_response(result))) > SERIALIZED_OUTPUT_BUDGET_CHARS:
        index = _last_shrinkable_index(result)
        if index is not None:
            shrunk = _largest_fitting_item(result, index)
            if shrunk is not None:
                result[index] = shrunk
                continue
        fallback = index if index is not None else _last_ok_index(result)
        if fallback is None:
            raise KnowledgeReadError(
                code=KnowledgeErrorCode.OUTPUT_BUDGET_EXCEEDED,
                message="批量读取结果超出单次输出序列化预算上限 (32,768 字符)",
            )
        result[fallback] = _minimal_failure_item(
            result[fallback], ReadStatus.BUDGET_EXHAUSTED
        )
    return result


async def _do_read(
    session: AsyncSession,
    project_id: str,
    request: KnowledgeReadRequest,
    *,
    kind: KnowledgeKind,
) -> KnowledgeReadResponse:
    """在统一事务快照内完成作用域校验、批量取数与区间组装。"""
    project_exists = await knowledge_search_repo.check_project_exists(session, project_id)
    if not project_exists:
        raise KnowledgeReadError(
            code=KnowledgeErrorCode.CONTEXT_ERROR,
            message=f"项目不存在: {project_id}",
        )

    requested_ids = [item.id for item in request.items]
    if kind is KnowledgeKind.WORLD_ENTRY:
        records = {
            entry.id: (entry.name, entry.content or "", bool(entry.is_enabled))
            for entry in await knowledge_search_repo.get_world_entries_for_read(
                session, project_id=project_id, entry_ids=requested_ids
            )
        }
    else:
        records = {
            character.id: (character.name, character.description or "", True)
            for character in await knowledge_search_repo.get_characters_for_read(
                session, project_id=project_id, character_ids=requested_ids
            )
        }

    remaining_content_budget = READ_CONTENT_BUDGET_CHARS
    items: list[KnowledgeReadItem] = []
    for requested in request.items:
        record = records.get(requested.id)
        if record is None:
            items.append(KnowledgeReadItem(id=requested.id, status=ReadStatus.NOT_FOUND))
            continue

        name, content, is_enabled = record
        if not is_enabled:
            items.append(KnowledgeReadItem(id=requested.id, status=ReadStatus.DISABLED))
            continue

        content_version = compute_content_version(content)
        if (
            requested.expected_version is not None
            and requested.expected_version != content_version
        ):
            items.append(
                KnowledgeReadItem(
                    id=requested.id,
                    status=ReadStatus.VERSION_CONFLICT,
                    start_offset=requested.start_offset,
                )
            )
            continue

        total_chars = len(content)
        if requested.start_offset > total_chars:
            items.append(
                KnowledgeReadItem(
                    id=requested.id,
                    status=ReadStatus.INVALID_RANGE,
                    start_offset=requested.start_offset,
                    total_chars=total_chars,
                )
            )
            continue

        unread_chars = total_chars - requested.start_offset
        if unread_chars > 0 and remaining_content_budget <= 0:
            items.append(
                KnowledgeReadItem(
                    id=requested.id,
                    status=ReadStatus.BUDGET_EXHAUSTED,
                    start_offset=requested.start_offset,
                )
            )
            continue

        length = min(
            request.max_chars_per_item, unread_chars, remaining_content_budget
        )
        remaining_content_budget -= length
        items.append(
            _build_ok_item(
                entity_id=requested.id,
                name=name,
                content=content,
                content_version=content_version,
                start_offset=requested.start_offset,
                length=length,
            )
        )

    return _build_response(_apply_serialized_output_budget(items))


async def read_world_entries(
    session: AsyncSession,
    project_id: str,
    request: KnowledgeReadRequest,
) -> KnowledgeReadResponse:
    """按 ID 批量读取世界书条目，版本校验与正文读取共享同一读快照。"""
    async with read_snapshot(session):
        return await _do_read(
            session, project_id, request, kind=KnowledgeKind.WORLD_ENTRY
        )


async def read_characters(
    session: AsyncSession,
    project_id: str,
    request: KnowledgeReadRequest,
) -> KnowledgeReadResponse:
    """按 ID 批量读取角色，版本校验与正文读取共享同一读快照。"""
    async with read_snapshot(session):
        return await _do_read(session, project_id, request, kind=KnowledgeKind.CHARACTER)
