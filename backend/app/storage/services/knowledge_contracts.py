"""Frozen protocol DTOs for phase-one world-entry and character retrieval.

The search and read services are implemented in later tasks.  Keeping their
wire contracts here lets those implementations, Agent tools, and UI fixtures
share one validation boundary instead of gradually inventing incompatible
payloads.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.text_normalization import normalize_literal

MAX_QUERY_CHARS = 200
MAX_QUERY_TERMS = 8
SEARCH_DEFAULT_LIMIT = 10
SEARCH_MAX_LIMIT = 20
MAX_CURSOR_CHARS = 4096
MAX_CURSOR_OFFSET = 2_147_483_647
MAX_ALIASES_PER_ENTITY = 20
MAX_ALIAS_CHARS = 100
MAX_EXCERPTS_PER_ITEM = 3
MAX_EXCERPT_CHARS = 240

READ_MAX_ITEMS = 10
READ_DEFAULT_CHARS_PER_ITEM = 4000
READ_MAX_CHARS_PER_ITEM = 8000
READ_CONTENT_BUDGET_CHARS = 16_000
SERIALIZED_OUTPUT_BUDGET_CHARS = 32_768

LEGACY_LIST_DEFAULT_LIMIT = 20
LEGACY_LIST_MAX_LIMIT = 50
LEGACY_SINGLE_READ_MAX_CHARS = READ_DEFAULT_CHARS_PER_ITEM

_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class KnowledgeKind(StrEnum):
    WORLD_ENTRY = "world_entry"
    CHARACTER = "character"


class SearchMatchMode(StrEnum):
    ALL = "all"
    ANY = "any"


class SearchReason(StrEnum):
    NO_WORLD_BOOK = "no_world_book"


class ReadStatus(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    DISABLED = "disabled"
    VERSION_CONFLICT = "version_conflict"
    INVALID_RANGE = "invalid_range"
    BUDGET_EXHAUSTED = "budget_exhausted"


class KnowledgeErrorCode(StrEnum):
    """Stable request-level error codes returned by the four new tools."""

    INVALID_REQUEST = "invalid_request"
    INVALID_CURSOR = "invalid_cursor"
    CURSOR_STALE = "cursor_stale"
    CONTEXT_ERROR = "context_error"
    OUTPUT_BUDGET_EXCEEDED = "output_budget_exceeded"


def split_query_terms(query: str) -> tuple[str, ...]:
    """Split on Unicode whitespace and de-duplicate by matching semantics."""

    terms: list[str] = []
    seen: set[str] = set()
    for term in query.split():
        normalized = normalize_literal(term)
        if normalized in seen:
            continue
        seen.add(normalized)
        terms.append(term)
    return tuple(terms)


class KnowledgeSearchRequest(_ContractModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    match: SearchMatchMode = SearchMatchMode.ALL
    limit: int = Field(default=SEARCH_DEFAULT_LIMIT, ge=1, le=SEARCH_MAX_LIMIT)
    cursor: str | None = Field(default=None, min_length=1, max_length=MAX_CURSOR_CHARS)

    @field_validator("query", mode="before")
    @classmethod
    def strip_query(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_term_count(self) -> "KnowledgeSearchRequest":
        if len(split_query_terms(self.query)) > MAX_QUERY_TERMS:
            raise ValueError(f"query 最多包含 {MAX_QUERY_TERMS} 个去重后的关键词")
        return self

    @property
    def terms(self) -> tuple[str, ...]:
        return split_query_terms(self.query)


class KnowledgeCursorPayload(_ContractModel):
    """Decoded cursor shape; cursors are base64url-encoded canonical JSON."""

    v: Literal[1] = 1
    kind: KnowledgeKind
    project_id: str = Field(min_length=1, max_length=200)
    query_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    dataset_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    limit: int = Field(ge=1, le=SEARCH_MAX_LIMIT)
    offset: int = Field(ge=0, le=MAX_CURSOR_OFFSET)


class SearchExcerpt(_ContractModel):
    text: str = Field(min_length=1, max_length=MAX_EXCERPT_CHARS)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_interval(self) -> "SearchExcerpt":
        if self.end_offset < self.start_offset:
            raise ValueError("end_offset 不能小于 start_offset")
        if self.end_offset - self.start_offset != len(self.text):
            raise ValueError("片段 offset 必须精确对应 text 的 Unicode 字符长度")
        if self.line_end < self.line_start:
            raise ValueError("line_end 不能小于 line_start")
        return self


MatchedField = Literal["name", "alias", "content"]


class KnowledgeSearchItem(_ContractModel):
    id: str = Field(min_length=1, max_length=200)
    kind: KnowledgeKind
    name: str
    aliases: list[Annotated[str, Field(min_length=1, max_length=MAX_ALIAS_CHARS)]] = Field(
        default_factory=list, max_length=MAX_ALIASES_PER_ENTITY
    )
    matched_fields: list[MatchedField] = Field(min_length=1, max_length=3)
    matched_terms: list[str] = Field(min_length=1, max_length=MAX_QUERY_TERMS)
    excerpts: list[SearchExcerpt] = Field(default_factory=list, max_length=MAX_EXCERPTS_PER_ITEM)
    content_version: str = Field(pattern=_SHA256_PATTERN)
    excerpts_truncated: bool = False

    @field_validator("matched_fields", "matched_terms")
    @classmethod
    def require_unique_values(cls, values: list[str]) -> list[str]:
        if len(values) != len(dict.fromkeys(values)):
            raise ValueError("命中字段和命中词不得重复")
        return values

    @model_validator(mode="after")
    def validate_excerpt_source(self) -> "KnowledgeSearchItem":
        if self.excerpts and "content" not in self.matched_fields:
            raise ValueError("只有正文命中时才能返回 excerpts")
        if self.excerpts_truncated and not self.excerpts:
            raise ValueError("excerpts_truncated=true 时必须至少返回一个片段")
        return self


class KnowledgeSearchResponse(_ContractModel):
    items: list[KnowledgeSearchItem] = Field(default_factory=list)
    returned_count: int = Field(ge=0)
    total_matches: int = Field(ge=0)
    has_more: bool
    next_cursor: str | None = Field(default=None, min_length=1, max_length=MAX_CURSOR_CHARS)
    match_scope: Literal["literal_terms"] = "literal_terms"
    reason: SearchReason | None = None

    @model_validator(mode="after")
    def validate_page(self) -> "KnowledgeSearchResponse":
        if self.returned_count != len(self.items):
            raise ValueError("returned_count 必须等于 items 数量")
        if self.total_matches < self.returned_count:
            raise ValueError("total_matches 不能小于 returned_count")
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("has_more 与 next_cursor 必须一致")
        if self.reason is SearchReason.NO_WORLD_BOOK and (
            self.items or self.total_matches or self.has_more
        ):
            raise ValueError("no_world_book 只能用于空结果")
        return self


class KnowledgeReadItemRequest(_ContractModel):
    id: str = Field(min_length=1, max_length=200)
    start_offset: int = Field(default=0, ge=0)
    expected_version: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_version_for_continuation(self) -> "KnowledgeReadItemRequest":
        if self.start_offset > 0 and self.expected_version is None:
            raise ValueError("续读（start_offset > 0）必须提供 expected_version")
        return self


class KnowledgeReadRequest(_ContractModel):
    items: list[KnowledgeReadItemRequest] = Field(min_length=1, max_length=READ_MAX_ITEMS)
    max_chars_per_item: int = Field(
        default=READ_DEFAULT_CHARS_PER_ITEM,
        ge=1,
        le=READ_MAX_CHARS_PER_ITEM,
    )

    @field_validator("items")
    @classmethod
    def reject_duplicate_ids(
        cls, items: list[KnowledgeReadItemRequest]
    ) -> list[KnowledgeReadItemRequest]:
        ids = [item.id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("items 中不能包含重复 ID")
        return items


class KnowledgeReadItem(_ContractModel):
    id: str = Field(min_length=1, max_length=200)
    status: ReadStatus
    name: str | None = None
    content_version: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    total_chars: int | None = Field(default=None, ge=0)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)
    start_line: int | None = Field(default=None, ge=1)
    start_line_offset: int | None = Field(default=None, ge=0)
    content: str | None = None
    truncated: bool = False
    next_start_offset: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "KnowledgeReadItem":
        content_fields = (
            self.name,
            self.content_version,
            self.total_chars,
            self.start_offset,
            self.end_offset,
            self.start_line,
            self.start_line_offset,
            self.content,
        )
        if self.status is ReadStatus.OK:
            if any(value is None for value in content_fields):
                raise ValueError("ok 项必须包含完整的正文区间元数据")
            assert self.start_offset is not None
            assert self.end_offset is not None
            assert self.total_chars is not None
            assert self.content is not None
            if self.end_offset < self.start_offset or self.end_offset > self.total_chars:
                raise ValueError("读取区间超出正文范围")
            if self.end_offset - self.start_offset != len(self.content):
                raise ValueError("读取区间必须精确对应 content 的 Unicode 字符长度")
            if self.truncated:
                if self.end_offset <= self.start_offset:
                    raise ValueError("截断区间必须至少前进一个 Unicode 字符")
                if self.next_start_offset != self.end_offset or self.end_offset >= self.total_chars:
                    raise ValueError("截断项必须从 end_offset 继续且正文尚未读完")
            elif self.next_start_offset is not None:
                raise ValueError("未截断项不能返回 next_start_offset")
        else:
            if self.content is not None:
                raise ValueError("非 ok 项不得返回正文")
            if self.truncated or self.next_start_offset is not None:
                raise ValueError("非 ok 项不得标记正文截断或续读 offset")
            if self.status in {ReadStatus.NOT_FOUND, ReadStatus.DISABLED} and any(
                value is not None for value in (self.name, self.content_version, self.total_chars)
            ):
                raise ValueError("not_found/disabled 项不得泄露名称、版本或正文长度")
        return self


class KnowledgeReadResponse(_ContractModel):
    items: list[KnowledgeReadItem]
    returned_count: int = Field(ge=0)
    partial_failure: bool
    budget_exhausted: bool

    @model_validator(mode="after")
    def validate_counts(self) -> "KnowledgeReadResponse":
        ok_count = sum(item.status is ReadStatus.OK for item in self.items)
        has_failure = any(item.status is not ReadStatus.OK for item in self.items)
        exhausted = any(item.status is ReadStatus.BUDGET_EXHAUSTED for item in self.items)
        if self.returned_count != ok_count:
            raise ValueError("returned_count 必须等于 status=ok 的项目数")
        if self.partial_failure != has_failure:
            raise ValueError("partial_failure 必须反映是否存在非 ok 项")
        if self.budget_exhausted != exhausted:
            raise ValueError("budget_exhausted 必须反映是否存在预算耗尽项")
        return self


class KnowledgeErrorDetail(_ContractModel):
    code: KnowledgeErrorCode
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False


class KnowledgeErrorResponse(_ContractModel):
    error: KnowledgeErrorDetail
