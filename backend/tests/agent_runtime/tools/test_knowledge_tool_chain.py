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
