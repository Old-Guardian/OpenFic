# -*- coding: utf-8 -*-
"""T5 离线链路：新检索/读取工具的结果必须作为 ToolMessage 到达后续模型调用。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent_runtime.graph.react_agent import create_react_agent
from app.agent_runtime.types import ReactAgentConfig, TerminationCondition

VERSION = "sha256:" + "a" * 64


def _search_response() -> object:
    from app.storage.services.knowledge_contracts import (
        KnowledgeKind,
        KnowledgeSearchItem,
        KnowledgeSearchResponse,
    )

    return KnowledgeSearchResponse(
        items=[
            KnowledgeSearchItem(
                id="e1",
                kind=KnowledgeKind.WORLD_ENTRY,
                name="燃魂术",
                aliases=[],
                matched_fields=["content"],
                matched_terms=["寿命"],
                excerpts=[],
                content_version=VERSION,
                excerpts_truncated=False,
            )
        ],
        returned_count=1,
        total_matches=1,
        has_more=False,
        next_cursor=None,
        match_scope="literal_terms",
    )


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
                total_chars=6000,
                start_offset=0,
                end_offset=4000,
                start_line=1,
                start_line_offset=0,
                content="甲" * 4000,
                truncated=True,
                next_start_offset=4000,
            )
        ],
        returned_count=1,
        partial_failure=False,
        budget_exhausted=False,
    )


async def test_search_then_read_results_reach_next_model_call() -> None:
    from app.agent_runtime.tools.impls.context.knowledge_read import (
        ReadWorldEntriesTool,
    )
    from app.agent_runtime.tools.impls.context.knowledge_search import (
        SearchWorldEntriesTool,
    )

    state = {"session_id": "sess-1", "project_id": "proj-1"}
    search_tool = SearchWorldEntriesTool(_state=state)
    read_tool = ReadWorldEntriesTool(_state=state)

    model = Mock()
    model.bind_tools.return_value = model
    responses = iter(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "c1",
                        "name": "search_world_entries",
                        "args": {"query": "寿命"},
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "c2",
                        "name": "read_world_entries",
                        "args": {"items": [{"id": "e1"}]},
                    }
                ],
            ),
            AIMessage(content="已按原文确认规则"),
        ]
    )

    async def invoke_model(*_args, **_kwargs):
        return next(responses)

    config = ReactAgentConfig(
        name="writer",
        tools=[search_tool, read_tool],
        termination=TerminationCondition(mode="no_tool_call"),
        max_iterations=3,
    )

    with (
        patch(
            "app.agent_runtime.graph.react_agent._invoke_model",
            side_effect=invoke_model,
        ),
        patch(
            "app.agent_runtime.tools.impls.context.knowledge_search.create_session"
        ) as search_session,
        patch(
            "app.agent_runtime.tools.impls.context.knowledge_search.knowledge_search_service"
        ) as search_service,
        patch(
            "app.agent_runtime.tools.impls.context.knowledge_read.create_session"
        ) as read_session,
        patch(
            "app.agent_runtime.tools.impls.context.knowledge_read.knowledge_read_service"
        ) as read_service,
    ):
        search_session.return_value = AsyncMock()
        search_service.search_world_entries = AsyncMock(return_value=_search_response())
        read_session.return_value = AsyncMock()
        read_service.read_world_entries = AsyncMock(return_value=_read_response())

        result = await create_react_agent(config, model=model).ainvoke(
            {
                "messages": [HumanMessage(content="查一下燃魂术的代价")],
                "iteration_count": 0,
                "is_done": False,
                "final_output": None,
            }
        )

    tool_messages = [
        message for message in result["messages"] if isinstance(message, ToolMessage)
    ]
    assert [message.tool_call_id for message in tool_messages] == ["c1", "c2"]

    # 搜索结果（含稳定 id）进入上下文
    search_payload = json.loads(tool_messages[0].content)
    assert search_payload["items"][0]["id"] == "e1"
    assert search_payload["items"][0]["name"] == "燃魂术"

    # 批量读取结果（含截断与续读参数）进入上下文
    read_payload = json.loads(tool_messages[1].content)
    assert read_payload["items"][0]["status"] == "ok"
    assert read_payload["items"][0]["truncated"] is True
    assert read_payload["items"][0]["next_start_offset"] == 4000

    # 后续模型调用确实拿到了上述结果
    assert result["messages"][-1].content == "已按原文确认规则"


async def test_search_read_and_continue_reading_multi_turn() -> None:
    from app.agent_runtime.tools.impls.context.knowledge_read import (
        ReadWorldEntriesTool,
    )
    from app.agent_runtime.tools.impls.context.knowledge_search import (
        SearchWorldEntriesTool,
    )
    from app.storage.services.knowledge_contracts import (
        KnowledgeKind,
        KnowledgeReadItem,
        KnowledgeReadResponse,
        KnowledgeSearchItem,
        KnowledgeSearchResponse,
        ReadStatus,
    )

    state = {"session_id": "sess-1", "project_id": "proj-1"}
    search_tool = SearchWorldEntriesTool(_state=state)
    read_tool = ReadWorldEntriesTool(_state=state)

    model = Mock()
    model.bind_tools.return_value = model

    # Turn 1: model calls search_world_entries
    # Turn 2: model inspects search result, calls read_world_entries(items=[{"id": "long-1"}])
    # Turn 3: model sees truncated=True and calls read_world_entries(items=[{"id": "long-1", "start_offset": 4000, "expected_version": VERSION}])
    # Turn 4: model provides full answer
    responses = iter(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "search_world_entries",
                        "args": {"query": "燃魂真经"},
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-2",
                        "name": "read_world_entries",
                        "args": {"items": [{"id": "long-1"}]},
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-3",
                        "name": "read_world_entries",
                        "args": {
                            "items": [
                                {
                                    "id": "long-1",
                                    "start_offset": 4000,
                                    "expected_version": VERSION,
                                }
                            ]
                        },
                    }
                ],
            ),
            AIMessage(content="完整真经内容拼接已完成"),
        ]
    )

    async def invoke_model(*_args, **_kwargs):
        return next(responses)

    config = ReactAgentConfig(
        name="writer",
        tools=[search_tool, read_tool],
        termination=TerminationCondition(mode="no_tool_call"),
        max_iterations=5,
    )

    read_segment_1 = KnowledgeReadResponse(
        items=[
            KnowledgeReadItem(
                id="long-1",
                status=ReadStatus.OK,
                name="燃魂真经",
                content_version=VERSION,
                total_chars=7500,
                start_offset=0,
                end_offset=4000,
                start_line=1,
                start_line_offset=0,
                content="甲" * 4000,
                truncated=True,
                next_start_offset=4000,
            )
        ],
        returned_count=1,
        partial_failure=False,
        budget_exhausted=False,
    )
    read_segment_2 = KnowledgeReadResponse(
        items=[
            KnowledgeReadItem(
                id="long-1",
                status=ReadStatus.OK,
                name="燃魂真经",
                content_version=VERSION,
                total_chars=7500,
                start_offset=4000,
                end_offset=7500,
                start_line=10,
                start_line_offset=0,
                content="乙" * 3500,
                truncated=False,
                next_start_offset=None,
            )
        ],
        returned_count=1,
        partial_failure=False,
        budget_exhausted=False,
    )

    search_resp = KnowledgeSearchResponse(
        items=[
            KnowledgeSearchItem(
                id="long-1",
                kind=KnowledgeKind.WORLD_ENTRY,
                name="燃魂真经",
                aliases=[],
                matched_fields=["name"],
                matched_terms=["燃魂真经"],
                excerpts=[],
                content_version=VERSION,
                excerpts_truncated=False,
            )
        ],
        returned_count=1,
        total_matches=1,
        has_more=False,
        next_cursor=None,
        match_scope="literal_terms",
    )

    with (
        patch("app.agent_runtime.graph.react_agent._invoke_model", side_effect=invoke_model),
        patch("app.agent_runtime.tools.impls.context.knowledge_search.create_session") as s_session,
        patch("app.agent_runtime.tools.impls.context.knowledge_search.knowledge_search_service") as s_service,
        patch("app.agent_runtime.tools.impls.context.knowledge_read.create_session") as r_session,
        patch("app.agent_runtime.tools.impls.context.knowledge_read.knowledge_read_service") as r_service,
    ):
        s_session.return_value = AsyncMock()
        s_service.search_world_entries = AsyncMock(return_value=search_resp)
        r_session.return_value = AsyncMock()
        r_service.read_world_entries = AsyncMock(side_effect=[read_segment_1, read_segment_2])

        result = await create_react_agent(config, model=model).ainvoke(
            {
                "messages": [HumanMessage(content="读取燃魂真经全文")],
                "iteration_count": 0,
                "is_done": False,
                "final_output": None,
            }
        )

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 3
    assert [m.tool_call_id for m in tool_messages] == ["call-1", "call-2", "call-3"]

    # 验证第一段截断和续读 offset
    p1 = json.loads(tool_messages[1].content)
    assert p1["items"][0]["truncated"] is True
    assert p1["items"][0]["next_start_offset"] == 4000

    # 验证第二段读取完成
    p2 = json.loads(tool_messages[2].content)
    assert p2["items"][0]["truncated"] is False
    assert p2["items"][0]["content"] == "乙" * 3500

    assert result["messages"][-1].content == "完整真经内容拼接已完成"


async def test_disabled_entry_not_leaked_in_agent_chain() -> None:
    from app.agent_runtime.tools.impls.context.knowledge_read import (
        ReadWorldEntriesTool,
    )
    from app.storage.services.knowledge_contracts import (
        KnowledgeReadItem,
        KnowledgeReadResponse,
        ReadStatus,
    )

    state = {"session_id": "sess-1", "project_id": "proj-1"}
    read_tool = ReadWorldEntriesTool(_state=state)

    model = Mock()
    model.bind_tools.return_value = model
    responses = iter(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "c-dis",
                        "name": "read_world_entries",
                        "args": {"items": [{"id": "entry-disabled"}]},
                    }
                ],
            ),
            AIMessage(content="该条目已被禁用，无法读取正文"),
        ]
    )

    async def invoke_model(*_args, **_kwargs):
        return next(responses)

    config = ReactAgentConfig(
        name="writer",
        tools=[read_tool],
        termination=TerminationCondition(mode="no_tool_call"),
        max_iterations=2,
    )

    disabled_response = KnowledgeReadResponse(
        items=[
            KnowledgeReadItem(
                id="entry-disabled",
                status=ReadStatus.DISABLED,
                name=None,
                content_version=None,
                total_chars=None,
                start_offset=None,
                end_offset=None,
                start_line=None,
                start_line_offset=None,
                content=None,
                truncated=False,
                next_start_offset=None,
            )
        ],
        returned_count=0,
        partial_failure=True,
        budget_exhausted=False,
    )

    with (
        patch("app.agent_runtime.graph.react_agent._invoke_model", side_effect=invoke_model),
        patch("app.agent_runtime.tools.impls.context.knowledge_read.create_session") as r_session,
        patch("app.agent_runtime.tools.impls.context.knowledge_read.knowledge_read_service") as r_service,
    ):
        r_session.return_value = AsyncMock()
        r_service.read_world_entries = AsyncMock(return_value=disabled_response)

        result = await create_react_agent(config, model=model).ainvoke(
            {
                "messages": [HumanMessage(content="读取已禁用的条目")],
                "iteration_count": 0,
                "is_done": False,
                "final_output": None,
            }
        )

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    read_payload = json.loads(tool_messages[0].content)
    assert read_payload["items"][0]["status"] == "disabled"
    assert "content" not in read_payload["items"][0] or read_payload["items"][0]["content"] is None
