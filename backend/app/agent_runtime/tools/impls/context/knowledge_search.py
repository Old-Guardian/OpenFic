# -*- coding: utf-8 -*-
"""关键词检索工具 - 世界书与角色的 Agent 工具薄封装。

依据第一阶段实施方案 §5、§7.2 与 §8：工具只负责把模型参数交给共享的
``knowledge_search_service``，并把返回 DTO 原样序列化；作用域一律使用工具
运行时绑定的项目，不接受模型提供的 project_id，也不提供 include_disabled。
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from app.agent_runtime.tools.base import AgentTool
from app.agent_runtime.tools.errors import ToolExecutionError
from app.agent_runtime.tools.registry import ToolRegistry
from app.storage.database import create_session
from app.storage.services import knowledge_search_service
from app.storage.services.knowledge_contracts import (
    KnowledgeSearchRequest,
    SearchMatchMode,
)
from app.storage.services.knowledge_search_service import KnowledgeSearchError

_QUERY_DESCRIPTION = (
    "关键词，用空格分隔多个词（最多 8 个，重复的词会被忽略）。"
    "按名称、别名与正文的字面子串匹配，中文连续子串即可命中，英文不区分大小写，"
    "不理解同义词、正则或布尔语法。结果为空时请换用别名、减少关键词或改用 match=any，"
    "不要据此断言设定不存在。"
)
_MATCH_DESCRIPTION = "all：每个词都要命中（默认）；any：任一词命中即可。"
_LIMIT_DESCRIPTION = "本次返回条数，1~20，默认 10。"
_CURSOR_DESCRIPTION = (
    "上一页返回的 next_cursor，仅用于继续翻页。数据发生变化后旧 cursor 会失效"
    "（cursor_stale），需要从第一页重新检索。"
)

_SEARCH_TOOL_SHARED_NOTES = """
返回项含稳定 id、命中字段、命中词与正文片段；片段是原文切片，不是摘要。

- has_more=true 只说明还有下一页，不代表已经盘点完毕；要盘点完必须翻到 has_more=false
- 命中正文但内容很长时，用 read_%(read_tool)s 按 id 读取原文，必要时续读
- 词面检索为空不等于设定不存在：先换别名、减少关键词或改用 match=any
"""


def _serialize(payload: BaseModel) -> str:
    """与契约预算一致的紧凑序列化（ensure_ascii=False，无多余空格）。"""
    return json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _build_search_request(
    *,
    query: str,
    match: str,
    limit: int,
    cursor: str | None,
) -> KnowledgeSearchRequest:
    try:
        return KnowledgeSearchRequest(
            query=query,
            match=SearchMatchMode(match),
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise ToolExecutionError(f"检索参数不合法: {exc}") from exc


class SearchWorldEntriesInput(BaseModel):
    query: str = Field(description=_QUERY_DESCRIPTION)
    match: Literal["all", "any"] = Field(default="all", description=_MATCH_DESCRIPTION)
    limit: int = Field(default=10, description=_LIMIT_DESCRIPTION)
    cursor: str | None = Field(default=None, description=_CURSOR_DESCRIPTION)


class SearchCharactersInput(BaseModel):
    query: str = Field(description=_QUERY_DESCRIPTION)
    match: Literal["all", "any"] = Field(default="all", description=_MATCH_DESCRIPTION)
    limit: int = Field(default=10, description=_LIMIT_DESCRIPTION)
    cursor: str | None = Field(default=None, description=_CURSOR_DESCRIPTION)


@ToolRegistry.register
class SearchWorldEntriesTool(AgentTool):
    name: str = "search_world_entries"
    description: str = (
        """在项目世界书的启用条目中按关键词检索设定。

不确定某个设定是否存在、叫什么名字，或只记得其中的词句时，先用它找到候选，
再用 read_world_entries 按 id 读取原文；按正文内容定位设定时直接给正文里出现过的词。
"""
        + _SEARCH_TOOL_SHARED_NOTES.replace("%(read_tool)s", "read_world_entries")
    )
    access_level: str = "readonly"
    args_schema: type[BaseModel] = SearchWorldEntriesInput

    async def _execute(
        self,
        query: str,
        match: str = "all",
        limit: int = 10,
        cursor: str | None = None,
    ) -> str:
        request = _build_search_request(
            query=query, match=match, limit=limit, cursor=cursor
        )
        session = await create_session()
        try:
            response = await knowledge_search_service.search_world_entries(
                session, self.project_id, request
            )
        except KnowledgeSearchError as exc:
            raise ToolExecutionError(exc.message) from exc
        finally:
            await session.close()
        return _serialize(response)


@ToolRegistry.register
class SearchCharactersTool(AgentTool):
    name: str = "search_characters"
    description: str = (
        """在项目角色中按关键词检索。

不确定某个角色是否存在、叫什么名字，或只记得其中的词句时，先用它找到候选，
再用 read_characters 按 id 读取完整描述。
"""
        + _SEARCH_TOOL_SHARED_NOTES.replace("%(read_tool)s", "read_characters")
    )
    access_level: str = "readonly"
    args_schema: type[BaseModel] = SearchCharactersInput

    async def _execute(
        self,
        query: str,
        match: str = "all",
        limit: int = 10,
        cursor: str | None = None,
    ) -> str:
        request = _build_search_request(
            query=query, match=match, limit=limit, cursor=cursor
        )
        session = await create_session()
        try:
            response = await knowledge_search_service.search_characters(
                session, self.project_id, request
            )
        except KnowledgeSearchError as exc:
            raise ToolExecutionError(exc.message) from exc
        finally:
            await session.close()
        return _serialize(response)


__all__ = [
    "SearchCharactersInput",
    "SearchCharactersTool",
    "SearchWorldEntriesInput",
    "SearchWorldEntriesTool",
]
