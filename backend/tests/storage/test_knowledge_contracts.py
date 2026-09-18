"""Contract tests for phase-one knowledge search and batch reads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.storage.services.knowledge_contracts import (
    KnowledgeCursorPayload,
    KnowledgeErrorResponse,
    KnowledgeKind,
    KnowledgeReadItem,
    KnowledgeReadItemRequest,
    KnowledgeReadRequest,
    KnowledgeReadResponse,
    KnowledgeSearchItem,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    ReadStatus,
    SearchExcerpt,
    SearchReason,
    normalize_literal,
)

VERSION = "sha256:" + "a" * 64


def test_search_request_trims_and_deduplicates_ascii_terms() -> None:
    request = KnowledgeSearchRequest(query="  燃魂 ALPHA alpha %_  ")

    assert request.query == "燃魂 ALPHA alpha %_"
    assert request.terms == ("燃魂", "ALPHA", "%_")
    assert normalize_literal("ÄABC中文") == "Äabc中文"
    assert request.match.value == "all"
    assert request.limit == 10


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "   "},
        {"query": "x" * 201},
        {"query": "1 2 3 4 5 6 7 8 9"},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": 21},
        {"query": "x", "match": "none"},
        {"query": "x", "unknown": True},
    ],
)
def test_search_request_rejects_out_of_contract_input(payload: dict) -> None:
    with pytest.raises(ValidationError):
        KnowledgeSearchRequest.model_validate(payload)


def test_cursor_payload_freezes_version_scope_and_bounds() -> None:
    cursor = KnowledgeCursorPayload(
        kind=KnowledgeKind.CHARACTER,
        project_id="project-1",
        query_fingerprint=VERSION,
        dataset_fingerprint="sha256:" + "b" * 64,
        limit=20,
        offset=100,
    )

    assert cursor.v == 1
    assert cursor.offset == 100

    with pytest.raises(ValidationError):
        KnowledgeCursorPayload.model_validate(cursor.model_dump() | {"offset": -1})
    with pytest.raises(ValidationError):
        KnowledgeCursorPayload.model_validate(cursor.model_dump() | {"v": 2})


def test_search_response_requires_exact_page_metadata_and_literal_excerpt() -> None:
    item = KnowledgeSearchItem(
        id="entry-1",
        kind=KnowledgeKind.WORLD_ENTRY,
        name="燃魂术",
        aliases=["焚命术"],
        matched_fields=["name", "content"],
        matched_terms=["燃魂", "寿命"],
        excerpts=[
            SearchExcerpt(
                text="消耗十年寿命",
                start_offset=4,
                end_offset=10,
                line_start=2,
                line_end=2,
            )
        ],
        content_version=VERSION,
    )
    response = KnowledgeSearchResponse(
        items=[item],
        returned_count=1,
        total_matches=2,
        has_more=True,
        next_cursor="opaque",
    )

    assert response.match_scope == "literal_terms"
    with pytest.raises(ValidationError):
        KnowledgeSearchResponse(
            items=[item],
            returned_count=0,
            total_matches=2,
            has_more=False,
        )
    with pytest.raises(ValidationError):
        KnowledgeSearchItem(
            id="entry-1",
            kind=KnowledgeKind.WORLD_ENTRY,
            name="燃魂术",
            matched_fields=["name"],
            matched_terms=["燃魂"],
            excerpts=[
                SearchExcerpt(
                    text="燃魂",
                    start_offset=0,
                    end_offset=2,
                    line_start=1,
                    line_end=1,
                )
            ],
            content_version=VERSION,
        )


def test_no_world_book_is_an_explicit_empty_result() -> None:
    response = KnowledgeSearchResponse(
        items=[],
        returned_count=0,
        total_matches=0,
        has_more=False,
        reason=SearchReason.NO_WORLD_BOOK,
    )

    assert response.reason is SearchReason.NO_WORLD_BOOK


def test_read_request_requires_unique_ids_and_version_for_continuation() -> None:
    with pytest.raises(ValidationError):
        KnowledgeReadItemRequest(id="entry-1", start_offset=1)
    with pytest.raises(ValidationError):
        KnowledgeReadRequest(
            items=[
                KnowledgeReadItemRequest(id="entry-1"),
                KnowledgeReadItemRequest(id="entry-1"),
            ]
        )

    request = KnowledgeReadRequest(
        items=[
            KnowledgeReadItemRequest(
                id="entry-1", start_offset=4, expected_version=VERSION
            )
        ]
    )
    assert request.max_chars_per_item == 4000


def test_read_response_counts_partial_failure_and_preserves_unicode_offsets() -> None:
    ok = KnowledgeReadItem(
        id="entry-1",
        status=ReadStatus.OK,
        name="条目",
        content_version=VERSION,
        total_chars=5,
        start_offset=1,
        end_offset=3,
        start_line=1,
        start_line_offset=1,
        content="😀甲",
        truncated=True,
        next_start_offset=3,
    )
    missing = KnowledgeReadItem(id="missing", status=ReadStatus.NOT_FOUND)
    response = KnowledgeReadResponse(
        items=[ok, missing],
        returned_count=1,
        partial_failure=True,
        budget_exhausted=False,
    )

    assert response.returned_count == 1
    with pytest.raises(ValidationError):
        KnowledgeReadItem(
            id="disabled",
            status=ReadStatus.DISABLED,
            name="secret",
        )
    with pytest.raises(ValidationError):
        KnowledgeReadItem(
            id="stalled",
            status=ReadStatus.OK,
            name="条目",
            content_version=VERSION,
            total_chars=1,
            start_offset=0,
            end_offset=0,
            start_line=1,
            start_line_offset=0,
            content="",
            truncated=True,
            next_start_offset=0,
        )
    with pytest.raises(ValidationError):
        KnowledgeReadResponse(
            items=[ok, missing],
            returned_count=2,
            partial_failure=True,
            budget_exhausted=False,
        )


def test_checked_in_contract_samples_validate_against_frozen_dtos() -> None:
    fixture_path = (
        Path(__file__).parents[1] / "fixtures" / "knowledge_retrieval_contract_samples.json"
    )
    samples = json.loads(fixture_path.read_text(encoding="utf-8"))

    KnowledgeSearchRequest.model_validate(samples["search_request"])
    KnowledgeSearchResponse.model_validate(samples["search_page_1"])
    KnowledgeSearchResponse.model_validate(samples["search_page_2"])
    KnowledgeReadRequest.model_validate(samples["read_request"])
    KnowledgeReadResponse.model_validate(samples["read_partial_response"])
    for error in samples["errors"].values():
        KnowledgeErrorResponse.model_validate(error)
