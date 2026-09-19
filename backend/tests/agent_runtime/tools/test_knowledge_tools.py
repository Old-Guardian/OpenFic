# -*- coding: utf-8 -*-
"""T5 新增检索/读取工具与写工具别名的工具层测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.storage.services.knowledge_contracts import SearchReason

VERSION = "sha256:" + "a" * 64


def _make_state() -> dict:
    return {
        "session_id": "sess-1",
        "project_id": "proj-1",
        "model_config": {},
        "active_agent": "writer",
        "is_completed": False,
        "error": None,
        "retry_count": 0,
        "message_checkpoints": [],
        "user_request": "",
    }


def _search_item() -> object:
    from app.storage.services.knowledge_contracts import (
        KnowledgeKind,
        KnowledgeSearchItem,
        SearchExcerpt,
    )

    return KnowledgeSearchItem(
        id="e1",
        kind=KnowledgeKind.WORLD_ENTRY,
        name="燃魂术",
        aliases=["焚命术"],
        matched_fields=["name", "content"],
        matched_terms=["燃魂"],
        excerpts=[
            SearchExcerpt(
                text="消耗寿命",
                start_offset=0,
                end_offset=4,
                line_start=1,
                line_end=1,
            )
        ],
        content_version=VERSION,
        excerpts_truncated=False,
    )


def _search_response(
    *,
    items: list | None = None,
    total_matches: int = 3,
    has_more: bool = True,
    next_cursor: str | None = "cursor-1",
    reason: SearchReason | None = None,
) -> object:
    from app.storage.services.knowledge_contracts import KnowledgeSearchResponse

    resolved_items = [_search_item()] if items is None else items
    return KnowledgeSearchResponse(
        items=resolved_items,
        returned_count=len(resolved_items),
        total_matches=total_matches,
        has_more=has_more,
        next_cursor=next_cursor,
        match_scope="literal_terms",
        reason=reason,
    )


# ============== 搜索工具 ==============


@pytest.mark.asyncio
async def test_search_world_entries_delegates_with_bound_project() -> None:
    from app.agent_runtime.tools.impls.context.knowledge_search import (
        SearchWorldEntriesTool,
    )

    tool = SearchWorldEntriesTool(_state=_make_state())

    with patch(
        "app.agent_runtime.tools.impls.context.knowledge_search.create_session"
    ) as mock_cs, patch(
        "app.agent_runtime.tools.impls.context.knowledge_search.knowledge_search_service"
    ) as mock_service:
        mock_cs.return_value = AsyncMock()
        mock_service.search_world_entries = AsyncMock(return_value=_search_response())

        result = await tool.ainvoke({"query": "燃魂", "limit": 5})

    search_call = mock_service.search_world_entries.await_args
    assert search_call is not None
    args = search_call.args
    assert args[1] == "proj-1"
    request = args[2]
    assert request.query == "燃魂"
    assert request.limit == 5
    assert request.match.value == "all"
    assert request.cursor is None

    data = json.loads(result)
    assert data["total_matches"] == 3
    assert data["has_more"] is True
    assert data["next_cursor"] == "cursor-1"
    assert data["items"][0]["id"] == "e1"
    assert data["items"][0]["excerpts"][0]["text"] == "消耗寿命"


@pytest.mark.asyncio
async def test_search_characters_delegates_and_surfaces_reason() -> None:
    from app.agent_runtime.tools.impls.context.knowledge_search import (
        SearchCharactersTool,
    )

    tool = SearchCharactersTool(_state=_make_state())
    empty = _search_response(
        items=[],
        total_matches=0,
        has_more=False,
        next_cursor=None,
        reason=SearchReason.NO_WORLD_BOOK,
    )

    with patch(
        "app.agent_runtime.tools.impls.context.knowledge_search.create_session"
    ) as mock_cs, patch(
        "app.agent_runtime.tools.impls.context.knowledge_search.knowledge_search_service"
    ) as mock_service:
        mock_cs.return_value = AsyncMock()
        mock_service.search_characters = AsyncMock(return_value=empty)

        result = await tool.ainvoke({"query": "师父", "match": "any"})

    characters_call = mock_service.search_characters.await_args
    assert characters_call is not None
    assert characters_call.args[1] == "proj-1"
    assert characters_call.args[2].match.value == "any"
    assert json.loads(result)["reason"] == "no_world_book"


@pytest.mark.asyncio
async def test_search_tool_rejects_out_of_contract_parameters() -> None:
    from app.agent_runtime.tools.impls.context.knowledge_search import (
        SearchWorldEntriesTool,
    )

    tool = SearchWorldEntriesTool(_state=_make_state())

    with patch(
        "app.agent_runtime.tools.impls.context.knowledge_search.create_session"
    ) as mock_cs, patch(
        "app.agent_runtime.tools.impls.context.knowledge_search.knowledge_search_service"
    ) as mock_service:
        mock_cs.return_value = AsyncMock()
        mock_service.search_world_entries = AsyncMock()

        result = await tool.ainvoke({"query": "燃魂", "limit": 0})

    data = json.loads(result)
    assert data["type"] == "fail"
    assert "检索参数不合法" in data["message"]
    mock_service.search_world_entries.assert_not_awaited()


# ============== 批量读取工具 ==============


def _read_response() -> object:
    from app.storage.services.knowledge_contracts import (
        KnowledgeReadItem,
        KnowledgeReadResponse,
        ReadStatus,
    )

    return KnowledgeReadResponse(
        items=[
            KnowledgeReadItem(
                id="e1",
                status=ReadStatus.OK,
                name="燃魂术",
                content_version=VERSION,
                total_chars=3,
                start_offset=0,
                end_offset=3,
                start_line=1,
                start_line_offset=0,
                content="甲😀乙",
                truncated=False,
            ),
            KnowledgeReadItem(id="e2", status=ReadStatus.DISABLED),
        ],
        returned_count=1,
        partial_failure=True,
        budget_exhausted=False,
    )


@pytest.mark.asyncio
async def test_read_world_entries_delegates_items_and_budget() -> None:
    from app.agent_runtime.tools.impls.context.knowledge_read import (
        ReadWorldEntriesTool,
    )

    tool = ReadWorldEntriesTool(_state=_make_state())

    with patch(
        "app.agent_runtime.tools.impls.context.knowledge_read.create_session"
    ) as mock_cs, patch(
        "app.agent_runtime.tools.impls.context.knowledge_read.knowledge_read_service"
    ) as mock_service:
        mock_cs.return_value = AsyncMock()
        mock_service.read_world_entries = AsyncMock(return_value=_read_response())

        result = await tool.ainvoke(
            {
                "items": [
                    {"id": "e1"},
                    {
                        "id": "e2",
                        "start_offset": 4000,
                        "expected_version": VERSION,
                    },
                ],
                "max_chars_per_item": 2000,
            }
        )

    read_call = mock_service.read_world_entries.await_args
    assert read_call is not None
    request = read_call.args[2]
    assert read_call.args[1] == "proj-1"
    assert [item.id for item in request.items] == ["e1", "e2"]
    assert request.items[1].start_offset == 4000
    assert request.items[1].expected_version == VERSION
    assert request.max_chars_per_item == 2000

    data = json.loads(result)
    assert data["returned_count"] == 1
    assert data["partial_failure"] is True
    assert data["items"][0]["content"] == "甲😀乙"
    assert data["items"][1]["status"] == "disabled"
    assert data["items"][1]["content"] is None


@pytest.mark.asyncio
async def test_read_characters_delegates() -> None:
    from app.agent_runtime.tools.impls.context.knowledge_read import ReadCharactersTool

    tool = ReadCharactersTool(_state=_make_state())

    with patch(
        "app.agent_runtime.tools.impls.context.knowledge_read.create_session"
    ) as mock_cs, patch(
        "app.agent_runtime.tools.impls.context.knowledge_read.knowledge_read_service"
    ) as mock_service:
        mock_cs.return_value = AsyncMock()
        mock_service.read_characters = AsyncMock(return_value=_read_response())

        result = await tool.ainvoke({"items": [{"id": "e1"}]})

    read_call = mock_service.read_characters.await_args
    assert read_call is not None
    assert read_call.args[1] == "proj-1"
    assert json.loads(result)["returned_count"] == 1


@pytest.mark.asyncio
async def test_read_tool_rejects_duplicate_ids_and_oversized_batches() -> None:
    from app.agent_runtime.tools.impls.context.knowledge_read import (
        ReadWorldEntriesTool,
    )

    tool = ReadWorldEntriesTool(_state=_make_state())

    with patch(
        "app.agent_runtime.tools.impls.context.knowledge_read.create_session"
    ) as mock_cs, patch(
        "app.agent_runtime.tools.impls.context.knowledge_read.knowledge_read_service"
    ) as mock_service:
        mock_cs.return_value = AsyncMock()
        mock_service.read_world_entries = AsyncMock()

        duplicate = await tool.ainvoke({"items": [{"id": "e1"}, {"id": "e1"}]})
        oversized = await tool.ainvoke(
            {"items": [{"id": f"e{index}"} for index in range(11)]}
        )
        missing_version = await tool.ainvoke(
            {"items": [{"id": "e1", "start_offset": 10}]}
        )

    for result in (duplicate, oversized, missing_version):
        assert json.loads(result)["type"] == "fail"
    assert "读取参数不合法" in json.loads(duplicate)["message"]
    mock_service.read_world_entries.assert_not_awaited()


def test_tool_descriptions_carry_retrieval_guidance() -> None:
    from app.agent_runtime.tools.impls.context.knowledge_read import (
        ReadCharactersTool,
        ReadWorldEntriesTool,
    )
    from app.agent_runtime.tools.impls.context.knowledge_search import (
        SearchCharactersTool,
        SearchWorldEntriesTool,
    )

    for tool_class in (SearchWorldEntriesTool, SearchCharactersTool):
        description = tool_class.model_fields["description"].default
        assert "has_more" in description
        assert "不存在" in description
        assert "match=any" in description

    for tool_class in (ReadWorldEntriesTool, ReadCharactersTool):
        description = tool_class.model_fields["description"].default
        assert "truncated" in description
        assert "next_start_offset" in description
        assert "budget_exhausted" in description
        assert "不是" in description


def test_legacy_tool_descriptions_announce_pagination_and_truncation() -> None:
    from app.agent_runtime.tools.impls.context.character import (
        ListCharactersTool,
        ReadCharacterTool,
    )
    from app.agent_runtime.tools.impls.context.world_entry import (
        ListWorldEntriesTool,
        ReadWorldEntryTool,
    )

    for tool_class in (ListWorldEntriesTool, ListCharactersTool):
        description = tool_class.model_fields["description"].default
        assert "next_cursor" in description
        assert "has_more" in description

    for tool_class in (ReadWorldEntryTool, ReadCharacterTool):
        description = tool_class.model_fields["description"].default
        assert "truncated" in description
        assert "next_read" in description


# ============== 写工具别名 ==============


def test_edit_inputs_accept_aliases_only() -> None:
    from app.agent_runtime.tools.impls.context.character import EditCharacterInput
    from app.agent_runtime.tools.impls.context.world_entry import EditWorldEntryInput

    assert EditWorldEntryInput.model_validate(
        {"title": "主角", "aliases": []}
    ).aliases == []
    assert EditCharacterInput.model_validate(
        {"name": "林舟", "aliases": ["师父"]}
    ).aliases == ["师父"]

    with pytest.raises(ValidationError):
        EditWorldEntryInput.model_validate({"title": "主角"})
    with pytest.raises(ValidationError):
        EditCharacterInput.model_validate({"name": "林舟"})


@pytest.mark.asyncio
async def test_create_world_entry_passes_aliases_and_shows_them_in_diff() -> None:
    from app.agent_runtime.tools.impls.context.world_entry import CreateWorldEntryTool

    tool = CreateWorldEntryTool(_state={**_make_state(), "current_revision_id": "rev-1"})
    created = SimpleNamespace(
        id="e1",
        world_info_id="world-1",
        name="燃魂术",
        uid=1,
        order=1,
        content="正文",
        token_count=2,
        is_enabled=True,
    )

    with patch(
        "app.agent_runtime.tools.impls.context.world_entry.create_session"
    ) as mock_cs, patch(
        "app.agent_runtime.tools.impls.context.world_entry.world_info_repo"
    ) as mock_world_repo, patch(
        "app.agent_runtime.tools.impls.context.world_entry.world_info_entry_repo"
    ) as mock_entry_repo, patch(
        "app.agent_runtime.tools.impls.context.world_entry.world_info_entry_service"
    ) as mock_entry_service, patch(
        "app.agent_runtime.tools.impls.context.world_entry.record_world_entry_diffs",
        AsyncMock(),
    ), patch(
        "app.agent_runtime.tools.impls.context.world_entry.world_entry_images_by_id",
        new_callable=AsyncMock,
        return_value={},
    ), patch(
        "app.agent_runtime.tools.impls.context.world_entry.knowledge_alias_service"
    ) as mock_alias_service:
        mock_cs.return_value = AsyncMock()
        mock_alias_service.list_entry_aliases = AsyncMock(return_value=["焚命术"])
        mock_world_repo.get_by_project_id = AsyncMock(
            return_value=SimpleNamespace(id="world-1")
        )
        mock_entry_repo.list_by_name = AsyncMock(return_value=[])
        mock_entry_service.create_entry = AsyncMock(return_value=created)

        result = await tool.ainvoke(
            {"title": "燃魂术", "content": "正文", "aliases": ["焚命术"]}
        )

    create_call = mock_entry_service.create_entry.await_args
    assert create_call is not None
    assert create_call.kwargs["aliases"] == ["焚命术"]
    diff = json.loads(result)["metadata"]["world_entry_diff"]
    assert diff["operation"] == "create"
    assert diff["aliases"] == {"added": ["焚命术"], "removed": []}


@pytest.mark.asyncio
async def test_edit_world_entry_aliases_only_updates_without_touching_content() -> None:
    from app.agent_runtime.tools.impls.context.world_entry import EditWorldEntryTool

    tool = EditWorldEntryTool(_state={**_make_state(), "current_revision_id": "rev-1"})
    entry = SimpleNamespace(
        id="e1",
        world_info_id="world-1",
        name="燃魂术",
        uid=1,
        order=1,
        content="正文",
        token_count=2,
        is_enabled=True,
    )

    with patch(
        "app.agent_runtime.tools.impls.context.world_entry.create_session"
    ) as mock_cs, patch(
        "app.agent_runtime.tools.impls.context.world_entry.world_info_repo"
    ) as mock_world_repo, patch(
        "app.agent_runtime.tools.impls.context.world_entry.world_info_entry_repo"
    ) as mock_entry_repo, patch(
        "app.agent_runtime.tools.impls.context.world_entry.world_info_entry_service"
    ) as mock_entry_service, patch(
        "app.agent_runtime.tools.impls.context.world_entry.record_world_entry_diffs",
        AsyncMock(),
    ), patch(
        "app.agent_runtime.tools.impls.context.world_entry.world_entry_images_by_id",
        new_callable=AsyncMock,
        return_value={},
    ), patch(
        "app.agent_runtime.tools.impls.context.world_entry.knowledge_alias_service"
    ) as mock_alias_service:
        mock_cs.return_value = AsyncMock()
        mock_alias_service.list_entry_aliases = AsyncMock(
            side_effect=[["旧别名"], ["新别名"]]
        )
        mock_world_repo.get_by_project_id = AsyncMock(
            return_value=SimpleNamespace(id="world-1")
        )
        mock_entry_repo.list_by_name = AsyncMock(return_value=[entry])
        mock_entry_service.update_entry = AsyncMock(return_value=entry)

        result = await tool.ainvoke({"title": "燃魂术", "aliases": ["新别名"]})

    update_call = mock_entry_service.update_entry.await_args
    assert update_call is not None
    call_kwargs = update_call.kwargs
    assert call_kwargs["aliases"] == ["新别名"]
    assert call_kwargs["content"] is None
    diff = json.loads(result)["metadata"]["world_entry_diff"]
    assert diff["aliases"] == {"added": ["新别名"], "removed": ["旧别名"]}
    assert diff["sections"][0]["lines"] == []


@pytest.mark.asyncio
async def test_create_character_rejects_invalid_alias() -> None:
    from app.agent_runtime.tools.impls.context.character import CreateCharacterTool

    tool = CreateCharacterTool(_state={**_make_state(), "current_revision_id": "rev-1"})

    with patch(
        "app.agent_runtime.tools.impls.context.character.create_session"
    ) as mock_cs, patch(
        "app.agent_runtime.tools.impls.context.character.character_repo"
    ) as mock_character_repo, patch(
        "app.agent_runtime.tools.impls.context.character.character_service"
    ) as mock_character_service:
        mock_cs.return_value = AsyncMock()
        mock_character_repo.list_by_project_and_name = AsyncMock(return_value=[])
        mock_character_service.create_character = AsyncMock(
            side_effect=ValueError("别名不能为空")
        )

        result = await tool.ainvoke(
            {"name": "林舟", "description": "主角", "aliases": [""]}
        )

    data = json.loads(result)
    assert data["type"] == "fail"
    assert "别名不合法" in data["message"]
