import json
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.agent_runtime.revisions import (
    character_images_by_id,
    current_revision_id_from_state,
    record_character_diffs,
    record_relationship_before,
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
from app.storage.models.character import Character, CharacterRelationship
from app.storage.repos import character_repo, character_relationship_repo
from app.storage.services import (
    character_service,
    character_relationship_service,
    knowledge_alias_service,
    knowledge_read_service,
    knowledge_search_service,
)
from app.storage.services.knowledge_contracts import (
    LEGACY_LIST_DEFAULT_LIMIT,
    LEGACY_LIST_MAX_LIMIT,
    LEGACY_SINGLE_READ_MAX_CHARS,
    KnowledgeReadItemRequest,
    KnowledgeReadRequest,
    ReadStatus,
)
from app.storage.services.knowledge_read_service import KnowledgeReadError
from app.storage.services.knowledge_search_service import KnowledgeSearchError


class ListCharactersInput(BaseModel):
    limit: int | None = Field(
        default=None,
        description=f"本次返回条数，1~{LEGACY_LIST_MAX_LIMIT}，默认 {LEGACY_LIST_DEFAULT_LIMIT}",
    )
    cursor: str | None = Field(
        default=None,
        description="上一页返回的 next_cursor，仅用于继续翻页；数据变化后需从第一页重新列出",
    )


class ReadCharacterInput(BaseModel):
    name: str = Field(description="要读取的角色名称（正式名称，不含别名）")


class CreateCharacterInput(BaseModel):
    name: str = Field(description="新角色名称")
    description: str = Field(description="新角色描述")
    aliases: list[str] | None = Field(
        default=None,
        description="可选别名列表，最多 20 个、每个 1~100 字符；别名不参与名称唯一性校验",
    )


class EditCharacterInput(BaseModel):
    name: str = Field(description="要编辑的角色名称")
    new_name: str | None = Field(default=None, description="可选的新角色名称")
    old_description: str | None = Field(default=None, description="要查找并替换的原始描述文本")
    new_description: str | None = Field(default=None, description="用于替换 old_description 的新描述文本")
    replace_all: bool = Field(default=False, description="是否替换命中的全部 old_description")
    aliases: list[str] | None = Field(
        default=None,
        description="整体替换别名列表：缺省表示保留既有别名，[] 表示清空",
    )

    @field_validator("old_description", mode="after")
    @classmethod
    def reject_empty_old_description(cls, v):
        if v is not None and v == "":
            raise ValueError("old_description 不能为空字符串")
        return v

    @model_validator(mode="after")
    def check_edit_fields(self) -> "EditCharacterInput":
        has_name = self.new_name is not None
        has_description = (
            self.old_description is not None and self.new_description is not None
        )
        has_aliases = self.aliases is not None
        if not has_name and not has_description and not has_aliases:
            raise ValueError("new_name、old_description/new_description 和 aliases 至少提供一类")
        return self


class DeleteCharacterInput(BaseModel):
    name: str = Field(description="要删除的角色名称")


class QueryRelationshipsInput(BaseModel):
    name: str = Field(description="起点角色名称")
    target_name: str | None = Field(default=None, description="可选的终点角色名称")
    max_hops: int = Field(default=2, ge=1, le=4, description="最多经过的关系数，1-4")


class CreateRelationshipInput(BaseModel):
    source_name: str = Field(description="关系起点角色名称")
    target_name: str = Field(description="关系终点角色名称")
    name: str = Field(min_length=1, max_length=200, description="关系名称")
    description: str = Field(default="", description="关系说明")


class EditRelationshipInput(BaseModel):
    source_name: str = Field(description="关系起点角色名称")
    target_name: str = Field(description="关系终点角色名称")
    name: str = Field(min_length=1, max_length=200, description="关系名称")
    description: str = Field(default="", description="关系说明")


class DeleteRelationshipInput(BaseModel):
    source_name: str = Field(description="关系起点角色名称")
    target_name: str = Field(description="关系终点角色名称")


def find_relationship_paths(relations: list[CharacterRelationship], start: str, target: str | None, max_hops: int) -> list[list[CharacterRelationship]]:
    paths: list[list[CharacterRelationship]] = []
    queue: deque[tuple[str, list[CharacterRelationship], frozenset[str]]] = deque([(start, [], frozenset({start}))])
    while queue and len(paths) < 100:
        current, path, visited = queue.popleft()
        if path and (target is None or current == target):
            paths.append(path)
        if len(path) >= max_hops or (target is not None and current == target):
            continue
        for relation in relations:
            neighbor = relation.target_character_id if relation.source_character_id == current else relation.source_character_id if relation.target_character_id == current else None
            if neighbor is not None and neighbor not in visited:
                queue.append((neighbor, [*path, relation], visited | {neighbor}))
    return paths[:100]


def _direct_relationships(
    relations: list[CharacterRelationship], character_id: str, names: dict[str, str]
) -> list[dict]:
    result = []
    for relation in relations:
        other_id = (
            relation.target_character_id
            if relation.source_character_id == character_id
            else relation.source_character_id
            if relation.target_character_id == character_id
            else None
        )
        if other_id is None or (other_name := names.get(other_id)) is None:
            continue
        result.append(
            {
                "character_id": other_id,
                "character_name": other_name,
                "name": relation.name,
                "description": relation.description,
            }
        )
    return result


async def _resolve_relationship(session, project_id: str, source_name: str, target_name: str) -> CharacterRelationship:
    source = await _resolve_character_by_name(session, project_id, source_name)
    target = await _resolve_character_by_name(session, project_id, target_name)
    a, b = sorted((source.id, target.id))
    relation = await character_relationship_repo.get_pair(session, project_id, a, b)
    if relation is None:
        raise ToolExecutionError("这两个角色之间不存在关系")
    return relation


@dataclass(frozen=True)
class CharacterPreview:
    id: str
    name: str
    description: str
    aliases: tuple[str, ...] = ()


def _preview_from_character(
    character: Character,
    *,
    aliases: Sequence[str] = (),
) -> CharacterPreview:
    return CharacterPreview(
        id=character.id,
        name=character.name,
        description=character.description,
        aliases=tuple(aliases),
    )


async def _character_preview(session, character: Character) -> CharacterPreview:
    """构建带别名的角色预览；别名单独查询，避免改动既有取图接口。"""
    aliases = await knowledge_alias_service.list_character_aliases(session, character.id)
    return _preview_from_character(character, aliases=aliases)


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


def _build_character_diff(
    before: CharacterPreview | None,
    after: CharacterPreview | None,
) -> dict:
    target = after or before
    if target is None:
        raise ToolExecutionError("缺少角色 diff 数据")
    if before is None:
        operation = "create"
        lines = _diff_lines(None, after.description if after else "")
    elif after is None:
        operation = "delete"
        lines = _diff_lines(before.description, None)
    else:
        operation = "edit"
        lines = _diff_lines(before.description, after.description) if before.description != after.description else []
    diff = {
        "operation": operation,
        "character_name": target.name,
        "sections": [{"type": "content", "lines": lines}],
    } | ({"character_id": target.id} if target.id else {})
    alias_diff = alias_changes(
        before.aliases if before is not None else (),
        after.aliases if after is not None else (),
    )
    if alias_diff is not None:
        diff["aliases"] = alias_diff
    return diff


async def _list_project_characters(session, project_id: str) -> list[Character]:
    return await character_repo.list_all_by_project(session, project_id)


async def _resolve_character_by_name(session, project_id: str, name: str) -> Character:
    normalized_name = name.strip()
    if not normalized_name:
        raise ToolExecutionError("角色名称不能为空")
    matches = await character_repo.list_by_project_and_name(
        session, project_id, normalized_name
    )
    if not matches:
        raise ToolExecutionError(f"角色不存在: {normalized_name}")
    if len(matches) > 1:
        raise ToolExecutionError(
            f"角色名称不唯一: {normalized_name}，请用 search_characters 取得 id 后按 id 读取"
        )
    return matches[0]


async def _ensure_name_available(
    session,
    project_id: str,
    name: str,
    exclude_character_id: str | None = None,
) -> str:
    normalized_name = name.strip()
    if not normalized_name:
        raise ToolExecutionError("角色名称不能为空")
    existing = await character_repo.list_by_project_and_name(
        session, project_id, normalized_name
    )
    if any(character.id != exclude_character_id for character in existing):
        raise ToolExecutionError(f"角色名称已存在: {normalized_name}")
    return normalized_name


def _require_revision_id(state: dict) -> str:
    revision_id = current_revision_id_from_state(state)
    if revision_id is None:
        raise ToolExecutionError("缺少当前 revision，无法执行角色修改")
    return revision_id


@ToolRegistry.register
class ListCharactersTool(AgentTool):
    name: str = "list_characters"
    description: str = f"""分页列出当前项目的角色名称与稳定 id。

默认每页 {LEGACY_LIST_DEFAULT_LIMIT} 条、最多 {LEGACY_LIST_MAX_LIMIT} 条；has_more=true 时用返回的
next_cursor 继续翻页，翻到 has_more=false 才算枚举完毕。按关键词查找角色
请优先用 search_characters。"""
    access_level: str = "readonly"
    args_schema: type[BaseModel] = ListCharactersInput

    async def _execute(self, limit: int | None = None, cursor: str | None = None) -> str:
        resolved_limit = LEGACY_LIST_DEFAULT_LIMIT if limit is None else limit
        if not 1 <= resolved_limit <= LEGACY_LIST_MAX_LIMIT:
            raise ToolExecutionError(
                f"limit 必须在 1~{LEGACY_LIST_MAX_LIMIT} 之间，收到 {resolved_limit}"
            )
        session = await create_session()
        try:
            try:
                page = await knowledge_search_service.list_characters(
                    session, self.project_id, limit=resolved_limit, cursor=cursor
                )
            except KnowledgeSearchError as exc:
                raise ToolExecutionError(exc.message) from exc
            relations = await character_relationship_repo.list_for_project(session, self.project_id)
            names = {
                item.id: item.name
                for item in await _list_project_characters(session, self.project_id)
            }
            return json.dumps(
                {
                    "characters": [
                        {
                            "name": character.name,
                            "id": character.id,
                            "relationships": _direct_relationships(
                                relations, character.id, names
                            ),
                        }
                        for character in page.items
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
class ReadCharacterTool(AgentTool):
    name: str = "read_character"
    description: str = f"""按正式名称读取当前项目中单个启用角色的描述（含行号）。

只按正式名称定位，不匹配别名；别名定位请先用 search_characters 再按 id 读取。
单次最多返回 {LEGACY_SINGLE_READ_MAX_CHARS} 字符：truncated=true 时描述尚未读完，
必须按返回的 next_read 继续读取，否则会漏掉内容。"""
    access_level: str = "readonly"
    args_schema: type[BaseModel] = ReadCharacterInput

    async def _execute(self, name: str) -> str:
        session = await create_session()
        try:
            character = await _resolve_character_by_name(session, self.project_id, name)
            try:
                response = await knowledge_read_service.read_characters(
                    session,
                    self.project_id,
                    KnowledgeReadRequest(
                        items=[KnowledgeReadItemRequest(id=character.id)],
                        max_chars_per_item=LEGACY_SINGLE_READ_MAX_CHARS,
                    ),
                )
            except KnowledgeReadError as exc:
                raise ToolExecutionError(exc.message) from exc
            item = response.items[0]
            if item.status is not ReadStatus.OK:
                raise ToolExecutionError(
                    f"读取角色失败: {character.name}（{item.status.value}）"
                )
            payload: dict[str, Any] = {
                "name": character.name,
                "id": character.id,
                "description": _format_content_with_line_numbers(item.content or ""),
                "total_chars": item.total_chars,
                "content_version": item.content_version,
                "truncated": item.truncated,
                "next_start_offset": item.next_start_offset,
            }
            relations = await character_relationship_repo.list_for_project(session, self.project_id)
            names = {
                item.id: item.name
                for item in await _list_project_characters(session, self.project_id)
            }
            payload["relationships"] = _direct_relationships(relations, character.id, names)
            if item.truncated:
                payload["next_read"] = build_next_read_hint(
                    tool_name="read_characters",
                    item_id=character.id,
                    next_start_offset=item.next_start_offset,
                    content_version=item.content_version,
                )
            return json.dumps(payload, ensure_ascii=False)
        finally:
            await session.close()


@ToolRegistry.register
class CreateCharacterTool(AgentTool):
    name: str = "create_character"
    description: str = "在当前项目中创建角色。"
    access_level: str = "write"
    args_schema: type[BaseModel] = CreateCharacterInput

    async def build_interrupt_preview(self, args: dict[str, Any]) -> dict | None:
        session = self.get_runtime_db_session()
        name = args.get("name")
        description = args.get("description")
        aliases = args.get("aliases")
        if session is None or not isinstance(name, str) or not isinstance(description, str):
            return None
        if aliases is not None and not is_alias_list(aliases):
            return None
        try:
            validate_editor_content(description)
        except EditorContentLimitError:
            return None
        try:
            normalized_name = await _ensure_name_available(session, self.project_id, name)
        except ToolExecutionError:
            return None
        preview_aliases = normalize_preview_aliases(aliases, entity_name=normalized_name)
        if preview_aliases is None:
            return None
        after = CharacterPreview(
            id="",
            name=normalized_name,
            description=description,
            aliases=tuple(preview_aliases),
        )
        return {
            "type": "preview",
            "success": True,
            "reason": "approval_preview",
            "message": "角色创建待审批",
            "metadata": {"character_diff": _build_character_diff(None, after)},
        }

    async def _execute(
        self,
        name: str,
        description: str,
        aliases: list[str] | None = None,
    ) -> str:
        revision_id = _require_revision_id(self._state)
        try:
            validate_editor_content(description)
        except EditorContentLimitError as exc:
            raise ToolExecutionError(str(exc)) from exc
        session = await create_session()
        try:
            async with await keyed_lock(("characters", self.project_id)):
                normalized_name = await _ensure_name_available(session, self.project_id, name)
                try:
                    character = await character_service.create_character(
                        session,
                        self.project_id,
                        name=normalized_name,
                        description=description,
                        aliases=aliases,
                    )
                except ValueError as exc:
                    raise ToolExecutionError(f"别名不合法: {exc}") from exc
                await record_character_diffs(
                    session,
                    revision_id=revision_id,
                    project_id=self.project_id,
                    before={},
                    after=await character_images_by_id(session, [character]),
                )
                await session.commit()
                character_preview = await _character_preview(session, character)
                return json.dumps(
                    {
                        "success": True,
                        "metadata": {
                            "character_diff": _build_character_diff(None, character_preview),
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
class EditCharacterTool(AgentTool):
    name: str = "edit_character"
    description: str = "编辑当前项目角色的名称或描述。修改描述时使用查找替换模式。"
    access_level: str = "write"
    args_schema: type[BaseModel] = EditCharacterInput

    async def build_interrupt_preview(self, args: dict[str, Any]) -> dict | None:
        session = self.get_runtime_db_session()
        name = args.get("name")
        new_name = args.get("new_name")
        old_description = args.get("old_description")
        new_description = args.get("new_description")
        aliases = args.get("aliases")
        if (
            session is None
            or not isinstance(name, str)
            or (new_name is not None and not isinstance(new_name, str))
            or (old_description is not None and not isinstance(old_description, str))
            or (new_description is not None and not isinstance(new_description, str))
            or (aliases is not None and not is_alias_list(aliases))
        ):
            return None
        try:
            character = await _resolve_character_by_name(session, self.project_id, name)
            before = await _character_preview(session, character)
            description = before.description
            if old_description is not None and new_description is not None:
                replace_result = fuzzy_replace(
                    description, old_description, new_description,
                    replace_all=bool(args.get("replace_all")),
                )
                if replace_result is None:
                    return None
                description = replace_result.new_content
                validate_editor_content(description)
            updated_name = (
                await _ensure_name_available(
                    session,
                    self.project_id,
                    new_name,
                    exclude_character_id=character.id,
                )
                if new_name is not None
                else before.name
            )
            updated_aliases = (
                normalize_preview_aliases(aliases, entity_name=updated_name)
                if aliases is not None
                else list(before.aliases)
            )
        except (EditorContentLimitError, ToolExecutionError):
            return None
        if updated_aliases is None:
            return None
        after = CharacterPreview(
            id=before.id,
            name=updated_name,
            description=description,
            aliases=tuple(updated_aliases),
        )
        return {
            "type": "preview",
            "success": True,
            "reason": "approval_preview",
            "message": "角色修改待审批",
            "metadata": {"character_diff": _build_character_diff(before, after)},
        }

    async def _execute(
        self,
        name: str,
        new_name: str | None = None,
        old_description: str | None = None,
        new_description: str | None = None,
        replace_all: bool = False,
        aliases: list[str] | None = None,
    ) -> str:
        revision_id = _require_revision_id(self._state)
        session = await create_session()
        try:
            async with await keyed_lock(("characters", self.project_id)):
                character = await _resolve_character_by_name(session, self.project_id, name)
                before = await _character_preview(session, character)
                description = character.description
                if old_description is not None and new_description is not None:
                    replace_result = fuzzy_replace(
                        description, old_description, new_description,
                        replace_all=replace_all,
                    )
                    if replace_result is None:
                        raise ToolExecutionError("未在角色描述中找到要替换的文本")
                    description = replace_result.new_content
                    try:
                        validate_editor_content(description)
                    except EditorContentLimitError as exc:
                        raise ToolExecutionError(str(exc)) from exc
                normalized_new_name = None
                if new_name is not None:
                    normalized_new_name = await _ensure_name_available(
                        session,
                        self.project_id,
                        new_name,
                        exclude_character_id=character.id,
                    )
                before_images = await character_images_by_id(session, [character])
                try:
                    updated = await character_service.update_character(
                        session,
                        character.id,
                        name=normalized_new_name,
                        description=description if description != before.description else None,
                        aliases=aliases,
                    )
                except ValueError as exc:
                    raise ToolExecutionError(f"别名不合法: {exc}") from exc
                await record_character_diffs(
                    session,
                    revision_id=revision_id,
                    project_id=self.project_id,
                    before=before_images,
                    after=await character_images_by_id(session, [updated]),
                )
                await session.commit()
                after = await _character_preview(session, updated)
                return json.dumps(
                    {
                        "success": True,
                        "metadata": {
                            "character_diff": _build_character_diff(before, after),
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
class DeleteCharacterTool(AgentTool):
    name: str = "delete_character"
    description: str = "根据名称删除当前项目中的单个角色。"
    access_level: str = "write"
    args_schema: type[BaseModel] = DeleteCharacterInput

    async def _execute(self, name: str) -> str:
        revision_id = _require_revision_id(self._state)
        session = await create_session()
        try:
            async with await keyed_lock(("characters", self.project_id)):
                character = await _resolve_character_by_name(session, self.project_id, name)
                before = await _character_preview(session, character)
                before_images = await character_images_by_id(session, [character])
                for relation in await character_relationship_repo.list_for_project(session, self.project_id):
                    if character.id in (relation.source_character_id, relation.target_character_id):
                        await record_relationship_before(session, revision_id, self.project_id, relation.id, relation)
                await character_service.delete_character(session, character.id)
                await record_character_diffs(
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
                            "character_diff": _build_character_diff(before, None),
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
class QueryCharacterRelationshipsTool(AgentTool):
    name: str = "query_character_relationships"
    description: str = (
        "查询角色在关系图中的直接或多跳路径，最多四跳。返回的 paths 是路径列表，"
        "每条路径由按顺序排列的关系列表组成，并非去重的关系清单。"
        "不同路径可能共享同一条关系（例如单跳路径也是两跳路径的前缀），"
        "同一关系重复出现在多个路径中不代表存在多条边。"
        "指定 target_name 时仅返回到该角色的路径；未指定时返回从起点可达的路径。"
    )
    access_level: str = "readonly"
    args_schema: type[BaseModel] = QueryRelationshipsInput

    async def _execute(self, name: str, target_name: str | None = None, max_hops: int = 2) -> str:
        session = await create_session()
        try:
            start = await _resolve_character_by_name(session, self.project_id, name)
            target = await _resolve_character_by_name(session, self.project_id, target_name) if target_name else None
            names = {character.id: character.name for character in await _list_project_characters(session, self.project_id)}
            relations = await character_relationship_repo.list_for_project(session, self.project_id)
            paths = find_relationship_paths(relations, start.id, target.id if target else None, max_hops)
            result = []
            for path in paths:
                current = start.id
                steps = []
                for relation in path:
                    next_id = relation.target_character_id if relation.source_character_id == current else relation.source_character_id
                    steps.append(
                        {
                            "from": names[current],
                            "to": names[next_id],
                            "name": relation.name,
                            "description": relation.description,
                        }
                    )
                    current = next_id
                result.append(steps)
            return json.dumps({"paths": result, "truncated": len(paths) == 100}, ensure_ascii=False)
        finally:
            await session.close()


@ToolRegistry.register
class CreateCharacterRelationshipTool(AgentTool):
    name: str = "create_character_relationship"
    description: str = "建立两个角色之间唯一的无向关系。"
    access_level: str = "write"
    args_schema: type[BaseModel] = CreateRelationshipInput

    async def _execute(self, source_name: str, target_name: str, name: str, description: str = "") -> str:
        revision_id = _require_revision_id(self._state)
        session = await create_session()
        try:
            async with await keyed_lock(("characters", self.project_id)):
                source = await _resolve_character_by_name(session, self.project_id, source_name)
                target = await _resolve_character_by_name(session, self.project_id, target_name)
                relation = await character_relationship_service.create_relationship(session, self.project_id, source.id, target.id, name, description)
                await record_relationship_before(session, revision_id, self.project_id, relation.id, None)
                await session.commit()
                return json.dumps(
                    {
                        "success": True,
                        "name": relation.name,
                        "source": source.name,
                        "target": target.name,
                    },
                    ensure_ascii=False,
                )
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@ToolRegistry.register
class EditCharacterRelationshipTool(AgentTool):
    name: str = "edit_character_relationship"
    description: str = "修改两个角色之间关系的名称与说明。"
    access_level: str = "write"
    args_schema: type[BaseModel] = EditRelationshipInput

    async def _execute(self, source_name: str, target_name: str, name: str, description: str = "") -> str:
        revision_id = _require_revision_id(self._state)
        session = await create_session()
        try:
            async with await keyed_lock(("characters", self.project_id)):
                relation = await _resolve_relationship(session, self.project_id, source_name, target_name)
                await record_relationship_before(session, revision_id, self.project_id, relation.id, relation)
                updated = await character_relationship_service.update_relationship(session, relation.id, name, description)
                await session.commit()
                return json.dumps(
                    {"success": True, "name": updated.name}, ensure_ascii=False
                )
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@ToolRegistry.register
class DeleteCharacterRelationshipTool(AgentTool):
    name: str = "delete_character_relationship"
    description: str = "删除两个角色之间的关系。"
    access_level: str = "write"
    args_schema: type[BaseModel] = DeleteRelationshipInput

    async def _execute(self, source_name: str, target_name: str) -> str:
        revision_id = _require_revision_id(self._state)
        session = await create_session()
        try:
            async with await keyed_lock(("characters", self.project_id)):
                relation = await _resolve_relationship(session, self.project_id, source_name, target_name)
                await record_relationship_before(session, revision_id, self.project_id, relation.id, relation)
                await character_relationship_service.delete_relationship(session, relation.id)
                await session.commit()
                return json.dumps({"success": True}, ensure_ascii=False)
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
