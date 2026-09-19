import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.agent_runtime.revisions import (
    current_revision_id_from_state,
    record_world_entry_diffs,
    world_entry_images_by_id,
)
from app.agent_runtime.tools.base import AgentTool
from app.core.editor_content_limits import EditorContentLimitError, validate_editor_content
from app.agent_runtime.tools.errors import ToolExecutionError
from app.agent_runtime.tools.impls._aliases import (
    alias_changes,
    is_alias_list,
    normalize_preview_aliases,
)
from app.agent_runtime.tools.impls._locks import keyed_lock
from app.agent_runtime.tools.impls.context.knowledge_read import build_next_read_hint
from app.agent_runtime.tools.registry import ToolRegistry
from app.agent_runtime.tools.text_match import fuzzy_replace
from app.storage.database import create_session
from app.storage.models.world_info_entry import WorldInfoEntry
from app.storage.repos import world_info_entry_repo, world_info_repo
from app.storage.services import (
    knowledge_alias_service,
    knowledge_read_service,
    knowledge_search_service,
    world_info_entry_service,
)
from app.storage.services.knowledge_contracts import (
    LEGACY_LIST_DEFAULT_LIMIT,
    LEGACY_LIST_MAX_LIMIT,
    LEGACY_SINGLE_READ_MAX_CHARS,
    KnowledgeReadItemRequest,
    KnowledgeReadRequest,
    ReadStatus,
    SearchReason,
)
from app.storage.services.knowledge_read_service import KnowledgeReadError
from app.storage.services.knowledge_search_service import KnowledgeSearchError


class ListWorldEntriesInput(BaseModel):
    limit: int | None = Field(
        default=None,
        description=f"本次返回条数，1~{LEGACY_LIST_MAX_LIMIT}，默认 {LEGACY_LIST_DEFAULT_LIMIT}",
    )
    cursor: str | None = Field(
        default=None,
        description="上一页返回的 next_cursor，仅用于继续翻页；数据变化后需从第一页重新列出",
    )


class ReadWorldEntryInput(BaseModel):
    title: str = Field(description="条目标题（正式名称，不含别名）")


class CreateWorldEntryInput(BaseModel):
    title: str = Field(description="条目标题")
    content: str = Field(description="条目内容")
    aliases: list[str] | None = Field(
        default=None,
        description="可选别名列表，最多 20 个、每个 1~100 字符；别名不参与标题唯一性校验",
    )


class EditWorldEntryInput(BaseModel):
    title: str = Field(description="条目标题")
    new_title: str | None = Field(default=None, description="新条目标题，可选")
    old_content: str | None = Field(default=None, description="要查找并替换的原始文本")
    new_content: str | None = Field(default=None, description="用于替换 old_content 的新文本")
    replace_all: bool = Field(default=False, description="是否替换命中的全部 old_content，false 时只替换首个匹配项")
    aliases: list[str] | None = Field(
        default=None,
        description="整体替换别名列表：缺省表示保留既有别名，[] 表示清空",
    )

    @field_validator("old_content", mode="after")
    @classmethod
    def reject_empty_old_content(cls, v):
        if v is not None and v == "":
            raise ValueError("old_content 不能为空字符串")
        return v

    @model_validator(mode="after")
    def check_edit_fields(self) -> "EditWorldEntryInput":
        has_title = self.new_title is not None
        has_content = self.old_content is not None and self.new_content is not None
        has_aliases = self.aliases is not None
        if not has_title and not has_content and not has_aliases:
            raise ValueError("new_title、old_content/new_content 和 aliases 至少提供一类")
        return self


class DeleteWorldEntryInput(BaseModel):
    title: str = Field(description="条目标题")


@dataclass(frozen=True)
class WorldEntryPreview:
    id: str
    title: str
    uid: int
    order: int
    content: str
    token_count: int
    is_enabled: bool
    aliases: tuple[str, ...] = ()


def _preview_from_entry(
    entry: WorldInfoEntry,
    *,
    aliases: Sequence[str] = (),
) -> WorldEntryPreview:
    return WorldEntryPreview(
        id=entry.id,
        title=entry.name,
        uid=entry.uid,
        order=entry.order,
        content=entry.content,
        token_count=getattr(entry, "token_count", 0),
        is_enabled=getattr(entry, "is_enabled", True),
        aliases=tuple(aliases),
    )


async def _entry_preview(session, entry: WorldInfoEntry) -> WorldEntryPreview:
    """构建带别名的条目预览；别名单独查询，避免改动既有取图接口。"""
    aliases = await knowledge_alias_service.list_entry_aliases(session, entry.id)
    return _preview_from_entry(entry, aliases=aliases)


def _format_content_with_line_numbers(content: str) -> str:
    if not content:
        return ""
    return "\n".join(
        f"{line_number}|{line}"
        for line_number, line in enumerate(content.splitlines(), start=1)
    )


def _diff_lines(before: str | None, after: str | None) -> list[dict]:
    lines: list[dict] = []
    if before is not None:
        lines.extend(
            {
                "type": "removed",
                "before_line_number": line_number,
                "after_line_number": None,
                "text": line,
            }
            for line_number, line in enumerate(before.splitlines() or [""], start=1)
        )
    if after is not None:
        lines.extend(
            {
                "type": "added",
                "before_line_number": None,
                "after_line_number": line_number,
                "text": line,
            }
            for line_number, line in enumerate(after.splitlines() or [""], start=1)
        )
    return lines


def _build_world_entry_diff(
    before: WorldEntryPreview | None,
    after: WorldEntryPreview | None,
) -> dict:
    target = after or before
    if target is None:
        raise ToolExecutionError("缺少世界书条目 diff 数据")
    if before is None:
        operation = "create"
        lines = _diff_lines(None, after.content if after else "")
    elif after is None:
        operation = "delete"
        lines = _diff_lines(before.content, None)
    else:
        operation = "edit"
        lines = _diff_lines(before.content, after.content) if before.content != after.content else []
    diff = {
        "operation": operation,
        "entry_title": target.title,
        "sections": [{"type": "content", "lines": lines}],
    } | ({"entry_id": target.id} if target.id else {})
    alias_diff = alias_changes(
        before.aliases if before is not None else (),
        after.aliases if after is not None else (),
    )
    if alias_diff is not None:
        diff["aliases"] = alias_diff
    return diff


async def _get_project_world_info(session, project_id: str):
    world_info = await world_info_repo.get_by_project_id(session, project_id)
    if world_info is None:
        raise ToolExecutionError("当前项目未绑定世界书")
    return world_info


async def _resolve_entry_by_title(session, world_info_id: str, title: str) -> WorldInfoEntry:
    normalized_title = title.strip()
    if not normalized_title:
        raise ToolExecutionError("世界书条目标题不能为空")
    matches = await world_info_entry_repo.list_by_name(session, world_info_id, normalized_title)
    if not matches:
        raise ToolExecutionError(f"世界书条目不存在: {normalized_title}")
    if len(matches) > 1:
        raise ToolExecutionError(
            f"世界书条目标题不唯一: {normalized_title}，请用 search_world_entries 取得 id 后按 id 读取"
        )
    return matches[0]


async def _ensure_title_available(
    session,
    world_info_id: str,
    title: str,
    exclude_entry_id: str | None = None,
) -> str:
    normalized_title = title.strip()
    if not normalized_title:
        raise ToolExecutionError("世界书条目标题不能为空")
    existing = await world_info_entry_repo.list_by_name(
        session, world_info_id, normalized_title
    )
    if any(entry.id != exclude_entry_id for entry in existing):
        raise ToolExecutionError(f"世界书条目标题已存在: {normalized_title}")
    return normalized_title


def _require_revision_id(state: dict) -> str:
    revision_id = current_revision_id_from_state(state)
    if revision_id is None:
        raise ToolExecutionError("缺少当前 revision，无法执行世界书条目修改")
    return revision_id


@ToolRegistry.register
class ListWorldEntriesTool(AgentTool):
    name: str = "list_world_entries"
    description: str = f"""分页列出项目世界书中启用的设定条目（标题、uid、order、稳定 id）。

默认每页 {LEGACY_LIST_DEFAULT_LIMIT} 条、最多 {LEGACY_LIST_MAX_LIMIT} 条；has_more=true 时用返回的
next_cursor 继续翻页，翻到 has_more=false 才算枚举完毕。只列出启用条目，
禁用条目需要人工在管理界面处理。按关键词查找设定请优先用 search_world_entries。"""
    access_level: str = "readonly"
    args_schema: type[BaseModel] = ListWorldEntriesInput

    async def _execute(self, limit: int | None = None, cursor: str | None = None) -> str:
        resolved_limit = LEGACY_LIST_DEFAULT_LIMIT if limit is None else limit
        if not 1 <= resolved_limit <= LEGACY_LIST_MAX_LIMIT:
            raise ToolExecutionError(
                f"limit 必须在 1~{LEGACY_LIST_MAX_LIMIT} 之间，收到 {resolved_limit}"
            )
        session = await create_session()
        try:
            try:
                page = await knowledge_search_service.list_world_entries(
                    session, self.project_id, limit=resolved_limit, cursor=cursor
                )
            except KnowledgeSearchError as exc:
                raise ToolExecutionError(exc.message) from exc
            if page.reason is SearchReason.NO_WORLD_BOOK:
                raise ToolExecutionError("当前项目未绑定世界书")
            return json.dumps(
                {
                    "entries": [
                        {
                            "title": entry.name,
                            "uid": entry.uid,
                            "order": entry.order,
                            "id": entry.id,
                        }
                        for entry in page.items
                    ],
                    "returned_count": len(page.items),
                    "total_count": page.total,
                    "has_more": page.has_more,
                    "next_cursor": page.next_cursor,
                },
                ensure_ascii=False,
            )
        finally:
            await session.close()


@ToolRegistry.register
class ReadWorldEntryTool(AgentTool):
    name: str = "read_world_entry"
    description: str = f"""按正式名称读取项目世界书中某个启用条目的正文（含行号）。

只按正式名称定位，不匹配别名；别名定位请先用 search_world_entries 再按 id 读取。
单次最多返回 {LEGACY_SINGLE_READ_MAX_CHARS} 字符：truncated=true 时正文尚未读完，
必须按返回的 next_read 继续读取，否则会漏掉内容。"""
    access_level: str = "readonly"
    args_schema: type[BaseModel] = ReadWorldEntryInput

    async def _execute(self, title: str) -> str:
        session = await create_session()
        try:
            world_info = await _get_project_world_info(session, self.project_id)
            entry = await _resolve_entry_by_title(session, world_info.id, title)
            if not entry.is_enabled:
                raise ToolExecutionError(
                    f"世界书条目已禁用: {entry.name}，工具不能读取，请在管理界面启用后再试"
                )
            try:
                response = await knowledge_read_service.read_world_entries(
                    session,
                    self.project_id,
                    KnowledgeReadRequest(
                        items=[KnowledgeReadItemRequest(id=entry.id)],
                        max_chars_per_item=LEGACY_SINGLE_READ_MAX_CHARS,
                    ),
                )
            except KnowledgeReadError as exc:
                raise ToolExecutionError(exc.message) from exc
            item = response.items[0]
            if item.status is not ReadStatus.OK:
                raise ToolExecutionError(
                    f"读取世界书条目失败: {entry.name}（{item.status.value}）"
                )
            payload: dict[str, Any] = {
                "title": entry.name,
                "uid": entry.uid,
                "order": entry.order,
                "id": entry.id,
                "content": _format_content_with_line_numbers(item.content or ""),
                "total_chars": item.total_chars,
                "content_version": item.content_version,
                "truncated": item.truncated,
                "next_start_offset": item.next_start_offset,
            }
            if item.truncated:
                payload["next_read"] = build_next_read_hint(
                    tool_name="read_world_entries",
                    item_id=entry.id,
                    next_start_offset=item.next_start_offset,
                    content_version=item.content_version,
                )
            return json.dumps(payload, ensure_ascii=False)
        finally:
            await session.close()


@ToolRegistry.register
class CreateWorldEntryTool(AgentTool):
    name: str = "create_world_entry"
    description: str = "在项目世界书中创建设定条目"
    access_level: str = "write"
    args_schema: type[BaseModel] = CreateWorldEntryInput

    async def build_interrupt_preview(self, args: dict[str, Any]) -> dict | None:
        session = self.get_runtime_db_session()
        title = args.get("title")
        content = args.get("content")
        aliases = args.get("aliases")
        if session is None or not isinstance(title, str) or not isinstance(content, str):
            return None
        if aliases is not None and not is_alias_list(aliases):
            return None
        try:
            validate_editor_content(content)
        except EditorContentLimitError:
            return None
        try:
            world_info = await _get_project_world_info(session, self.project_id)
            normalized_title = await _ensure_title_available(session, world_info.id, title)
            preview_aliases = normalize_preview_aliases(aliases, entity_name=normalized_title)
        except (EditorContentLimitError, ToolExecutionError):
            return None
        if preview_aliases is None:
            return None
        after = WorldEntryPreview(
            id="",
            title=normalized_title,
            uid=0,
            order=0,
            content=content,
            token_count=0,
            is_enabled=True,
            aliases=tuple(preview_aliases),
        )
        return {
            "type": "preview",
            "success": True,
            "reason": "approval_preview",
            "message": "世界书条目创建待审批",
            "metadata": {"world_entry_diff": _build_world_entry_diff(None, after)},
        }

    async def _execute(
        self,
        title: str,
        content: str,
        aliases: list[str] | None = None,
    ) -> str:
        revision_id = _require_revision_id(self._state)
        try:
            validate_editor_content(content)
        except EditorContentLimitError as exc:
            raise ToolExecutionError(str(exc)) from exc
        session = await create_session()
        try:
            async with await keyed_lock(("world", self.project_id)):
                world_info = await _get_project_world_info(session, self.project_id)
                normalized_title = await _ensure_title_available(session, world_info.id, title)
                try:
                    entry = await world_info_entry_service.create_entry(
                        session,
                        world_info.id,
                        name=normalized_title,
                        content=content,
                        is_enabled=True,
                        aliases=aliases,
                    )
                except ValueError as exc:
                    raise ToolExecutionError(f"别名不合法: {exc}") from exc
                await record_world_entry_diffs(
                    session,
                    revision_id=revision_id,
                    project_id=self.project_id,
                    before={},
                    after=await world_entry_images_by_id(session, [entry], project_id=self.project_id),
                )
                await session.commit()
                entry_preview = await _entry_preview(session, entry)
                return json.dumps(
                    {
                        "success": True,
                        "metadata": {
                            "world_info_id": world_info.id,
                            "world_entry_diff": _build_world_entry_diff(None, entry_preview),
                        },
                    },
                    ensure_ascii=False,
                )
        except ToolExecutionError:
            raise
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@ToolRegistry.register
class EditWorldEntryTool(AgentTool):
    name: str = "edit_world_entry"
    description: str = "编辑项目世界书中的设定条目"
    access_level: str = "write"
    args_schema: type[BaseModel] = EditWorldEntryInput

    async def build_interrupt_preview(self, args: dict[str, Any]) -> dict | None:
        session = self.get_runtime_db_session()
        title = args.get("title")
        new_title = args.get("new_title")
        old_content = args.get("old_content")
        new_content = args.get("new_content")
        aliases = args.get("aliases")
        if (
            session is None
            or not isinstance(title, str)
            or (new_title is not None and not isinstance(new_title, str))
            or (old_content is not None and not isinstance(old_content, str))
            or (new_content is not None and not isinstance(new_content, str))
            or (aliases is not None and not is_alias_list(aliases))
        ):
            return None
        try:
            world_info = await _get_project_world_info(session, self.project_id)
            entry = await _resolve_entry_by_title(session, world_info.id, title)
            before = await _entry_preview(session, entry)
            content = before.content
            if old_content is not None and new_content is not None:
                replace_result = fuzzy_replace(
                    content, old_content, new_content,
                    replace_all=bool(args.get("replace_all")),
                )
                if replace_result is None:
                    return None
                content = replace_result.new_content
                validate_editor_content(content)
            updated_title = (
                await _ensure_title_available(
                    session,
                    world_info.id,
                    new_title,
                    exclude_entry_id=entry.id,
                )
                if new_title is not None
                else before.title
            )
            updated_aliases = (
                normalize_preview_aliases(aliases, entity_name=updated_title)
                if aliases is not None
                else list(before.aliases)
            )
        except (EditorContentLimitError, ToolExecutionError):
            return None
        if updated_aliases is None:
            return None
        after = WorldEntryPreview(
            id=before.id,
            title=updated_title,
            uid=before.uid,
            order=before.order,
            content=content,
            token_count=before.token_count,
            is_enabled=before.is_enabled,
            aliases=tuple(updated_aliases),
        )
        return {
            "type": "preview",
            "success": True,
            "reason": "approval_preview",
            "message": "世界书条目修改待审批",
            "metadata": {"world_entry_diff": _build_world_entry_diff(before, after)},
        }

    async def _execute(
        self,
        title: str,
        new_title: str | None = None,
        old_content: str | None = None,
        new_content: str | None = None,
        replace_all: bool = False,
        aliases: list[str] | None = None,
    ) -> str:
        revision_id = _require_revision_id(self._state)
        session = await create_session()
        try:
            async with await keyed_lock(("world", self.project_id)):
                world_info = await _get_project_world_info(session, self.project_id)
                entry = await _resolve_entry_by_title(session, world_info.id, title)
                before = await _entry_preview(session, entry)
                content = entry.content
                if old_content is not None and new_content is not None:
                    replace_result = fuzzy_replace(
                        content, old_content, new_content, replace_all=replace_all
                    )
                    if replace_result is None:
                        raise ToolExecutionError("未在世界书条目内容中找到要替换的文本")
                    content = replace_result.new_content
                    try:
                        validate_editor_content(content)
                    except EditorContentLimitError as exc:
                        raise ToolExecutionError(str(exc)) from exc
                normalized_new_title = None
                if new_title is not None:
                    normalized_new_title = await _ensure_title_available(
                        session,
                        world_info.id,
                        new_title,
                        exclude_entry_id=entry.id,
                    )
                before_images = await world_entry_images_by_id(
                    session, [entry], project_id=self.project_id
                )
                try:
                    updated = await world_info_entry_service.update_entry(
                        session,
                        entry.id,
                        name=normalized_new_title,
                        content=content if content != before.content else None,
                        aliases=aliases,
                    )
                except ValueError as exc:
                    raise ToolExecutionError(f"别名不合法: {exc}") from exc
                await record_world_entry_diffs(
                    session,
                    revision_id=revision_id,
                    project_id=self.project_id,
                    before=before_images,
                    after=await world_entry_images_by_id(
                        session, [updated], project_id=self.project_id
                    ),
                )
                await session.commit()
                after = await _entry_preview(session, updated)
                return json.dumps(
                    {
                        "success": True,
                        "metadata": {
                            "world_info_id": world_info.id,
                            "world_entry_diff": _build_world_entry_diff(before, after),
                        },
                    },
                    ensure_ascii=False,
                )
        except ToolExecutionError:
            raise
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@ToolRegistry.register
class DeleteWorldEntryTool(AgentTool):
    name: str = "delete_world_entry"
    description: str = "删除项目世界书中指定的设定条目"
    access_level: str = "write"
    args_schema: type[BaseModel] = DeleteWorldEntryInput

    async def _execute(self, title: str) -> str:
        revision_id = _require_revision_id(self._state)
        session = await create_session()
        try:
            async with await keyed_lock(("world", self.project_id)):
                world_info = await _get_project_world_info(session, self.project_id)
                entry = await _resolve_entry_by_title(session, world_info.id, title)
                before = await _entry_preview(session, entry)
                before_images = await world_entry_images_by_id(
                    session, [entry], project_id=self.project_id
                )
                await world_info_entry_service.delete_entry(session, entry.id)
                await record_world_entry_diffs(
                    session,
                    revision_id=revision_id,
                    project_id=self.project_id,
                    before=before_images,
                    after={},
                )
                await session.commit()
                return json.dumps(
                    {
                        "success": True,
                        "metadata": {
                            "world_info_id": world_info.id,
                            "world_entry_diff": _build_world_entry_diff(before, None),
                        },
                    },
                    ensure_ascii=False,
                )
        except ToolExecutionError:
            raise
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
