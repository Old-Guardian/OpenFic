# -*- coding: utf-8 -*-
"""按 ID 批量分段读取工具 - 世界书与角色的 Agent 工具薄封装。

依据第一阶段实施方案 §6、§7.1、§7.2 与 §8：工具只把模型参数交给共享的
``knowledge_read_service`` 并原样序列化返回 DTO；作用域一律使用工具运行时
绑定的项目。正文截断时必须给出续读路径，模型不得把 ``budget_exhausted``
当成条目不存在。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.agent_runtime.tools.base import AgentTool
from app.agent_runtime.tools.errors import ToolExecutionError
from app.agent_runtime.tools.registry import ToolRegistry
from app.storage.database import create_session
from app.storage.services import knowledge_read_service
from app.storage.services.knowledge_contracts import KnowledgeReadRequest
from app.storage.services.knowledge_read_service import KnowledgeReadError

_ID_DESCRIPTION = "目标 ID，来自 search_* 或 list_* 结果的 id 字段"
_START_OFFSET_DESCRIPTION = "从正文第几个 Unicode 字符开始读，默认 0"
_EXPECTED_VERSION_DESCRIPTION = (
    "续读（start_offset > 0）时必填：上一次返回的 content_version，"
    "用于确认正文没有在两次读取之间被修改"
)
_ITEMS_DESCRIPTION = "要读取的目标，1~10 项，ID 不得重复；一次批量读取总量最多 16,000 字符"
_MAX_CHARS_DESCRIPTION = "每项最多返回的字符数，1~8000，默认 4000"

_READ_TOOL_SHARED_NOTES = """
每项返回一个 status：

- ok：本次返回一个有效区间，内容为原文切片（未插入行号）；truncated=true 时正文还没读完，
  必须按 next_start_offset 与本次 content_version 继续读取，直到 truncated=false
- not_found：不存在或不属于当前项目
- disabled：该条目在管理界面被禁用，工具不能读取，也不能通过名称绕过
- version_conflict：正文已被修改，本次没有返回内容；请从 start_offset=0 重新读取
- invalid_range：start_offset 超出正文长度，请修正范围
- budget_exhausted：本次总预算不足，该项正文尚未读取；这不是「不存在」，
  请单独或缩小批次重试同一个 id 与 offset

多段续读按顺序拼接必须等于原文，可以放心用多次调用读完一个长条目。
"""


def _serialize(payload: BaseModel) -> str:
    """与契约预算一致的紧凑序列化（ensure_ascii=False，无多余空格）。"""
    return json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _as_mapping(item: Any) -> Any:
    """把嵌套参数归一化为契约可校验的形式。

    LangChain 会把 ``args_schema`` 校验后的嵌套模型原样传给 ``_execute``，
    因此这里既要接受 ``KnowledgeReadTargetInput`` 实例，也要接受测试或直接
    调用时传入的普通字典。
    """
    if isinstance(item, BaseModel):
        return item.model_dump()
    return item


def _build_read_request(
    *,
    items: list[Any],
    max_chars_per_item: int,
) -> KnowledgeReadRequest:
    try:
        return KnowledgeReadRequest.model_validate(
            {
                "items": [_as_mapping(item) for item in items],
                "max_chars_per_item": max_chars_per_item,
            }
        )
    except ValidationError as exc:
        raise ToolExecutionError(f"读取参数不合法: {exc}") from exc


def build_next_read_hint(
    *,
    tool_name: str,
    item_id: str,
    next_start_offset: int | None,
    content_version: str | None,
) -> dict[str, Any]:
    """构造可直接用于下一次批量读取工具调用的续读参数。"""
    return {
        "tool": tool_name,
        "args": {
            "items": [
                {
                    "id": item_id,
                    "start_offset": next_start_offset,
                    "expected_version": content_version,
                }
            ]
        },
    }


class KnowledgeReadTargetInput(BaseModel):
    id: str = Field(description=_ID_DESCRIPTION)
    start_offset: int = Field(default=0, description=_START_OFFSET_DESCRIPTION)
    expected_version: str | None = Field(
        default=None, description=_EXPECTED_VERSION_DESCRIPTION
    )


class ReadWorldEntriesInput(BaseModel):
    items: list[KnowledgeReadTargetInput] = Field(description=_ITEMS_DESCRIPTION)
    max_chars_per_item: int = Field(default=4000, description=_MAX_CHARS_DESCRIPTION)


class ReadCharactersInput(BaseModel):
    items: list[KnowledgeReadTargetInput] = Field(description=_ITEMS_DESCRIPTION)
    max_chars_per_item: int = Field(default=4000, description=_MAX_CHARS_DESCRIPTION)


@ToolRegistry.register
class ReadWorldEntriesTool(AgentTool):
    name: str = "read_world_entries"
    description: str = (
        """按 ID 批量读取项目世界书条目的正文，可分段续读长条目。

用 search_world_entries 拿到 id 后读取原文；需要确定具体规则或措辞时以原文为准，
不要凭搜索结果片段推断。
"""
        + _READ_TOOL_SHARED_NOTES
    )
    access_level: str = "readonly"
    args_schema: type[BaseModel] = ReadWorldEntriesInput

    async def _execute(
        self,
        items: list[Any],
        max_chars_per_item: int = 4000,
    ) -> str:
        request = _build_read_request(
            items=items, max_chars_per_item=max_chars_per_item
        )
        session = await create_session()
        try:
            response = await knowledge_read_service.read_world_entries(
                session, self.project_id, request
            )
        except KnowledgeReadError as exc:
            raise ToolExecutionError(exc.message) from exc
        finally:
            await session.close()
        return _serialize(response)


@ToolRegistry.register
class ReadCharactersTool(AgentTool):
    name: str = "read_characters"
    description: str = (
        """按 ID 批量读取项目角色的完整描述，可分段续读长描述。

用 search_characters 拿到 id 后读取原文；需要确定角色设定细节时以原文为准，
不要凭搜索结果片段推断。
"""
        + _READ_TOOL_SHARED_NOTES
    )
    access_level: str = "readonly"
    args_schema: type[BaseModel] = ReadCharactersInput

    async def _execute(
        self,
        items: list[Any],
        max_chars_per_item: int = 4000,
    ) -> str:
        request = _build_read_request(
            items=items, max_chars_per_item=max_chars_per_item
        )
        session = await create_session()
        try:
            response = await knowledge_read_service.read_characters(
                session, self.project_id, request
            )
        except KnowledgeReadError as exc:
            raise ToolExecutionError(exc.message) from exc
        finally:
            await session.close()
        return _serialize(response)


__all__ = [
    "KnowledgeReadTargetInput",
    "ReadCharactersInput",
    "ReadCharactersTool",
    "ReadWorldEntriesInput",
    "ReadWorldEntriesTool",
]
