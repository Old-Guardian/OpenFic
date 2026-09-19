# -*- coding: utf-8 -*-
"""批量分段读取服务 (T4) 的作用域、版本、区间、预算与无损续读测试。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import event

from app.storage.models.character import Character
from app.storage.models.project import Project
from app.storage.models.world_info import WorldInfo
from app.storage.models.world_info_entry import WorldInfoEntry
from app.storage.services import knowledge_read_service
from app.storage.services.knowledge_contracts import (
    READ_CONTENT_BUDGET_CHARS,
    SERIALIZED_OUTPUT_BUDGET_CHARS,
    KnowledgeErrorCode,
    KnowledgeReadItemRequest,
    KnowledgeReadRequest,
    ReadStatus,
    compute_content_version,
)
from app.storage.services.knowledge_read_service import KnowledgeReadError


async def _create_project(session, title: str = "读取测试项目") -> Project:
    project = Project(title=title, description="读取测试")
    session.add(project)
    await session.flush()
    return project


async def _create_world_info(session, project: Project) -> WorldInfo:
    world = WorldInfo(project_id=project.id, name="主世界书", description="")
    session.add(world)
    await session.flush()
    return world


async def _add_entry(
    session,
    world: WorldInfo,
    *,
    uid: int,
    name: str,
    content: str,
    is_enabled: bool = True,
) -> WorldInfoEntry:
    entry = WorldInfoEntry(
        world_info_id=world.id,
        uid=uid,
        name=name,
        order=uid,
        content=content,
        is_enabled=is_enabled,
    )
    session.add(entry)
    await session.flush()
    return entry


def _serialized(response) -> str:
    return json.dumps(
        response.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


@pytest.mark.asyncio
async def test_short_content_reads_full_text(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)
    entry = await _add_entry(session, world, uid=1, name="短条目", content="甲乙丙😀丁")

    response = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(items=[KnowledgeReadItemRequest(id=entry.id)]),
    )

    assert response.returned_count == 1
    assert response.partial_failure is False
    assert response.budget_exhausted is False

    item = response.items[0]
    assert item.status is ReadStatus.OK
    assert item.id == entry.id
    assert item.name == "短条目"
    assert item.content == "甲乙丙😀丁"
    assert item.total_chars == 5
    assert item.start_offset == 0
    assert item.end_offset == 5
    assert item.start_line == 1
    assert item.start_line_offset == 0
    assert item.truncated is False
    assert item.next_start_offset is None
    assert item.content_version == compute_content_version("甲乙丙😀丁")


@pytest.mark.asyncio
async def test_characters_read_uses_description_and_scopes_to_project(session) -> None:
    project = await _create_project(session)
    other = await _create_project(session, "别的项目")

    mine = Character(project_id=project.id, name="本项角色", description="本项目的角色描述")
    theirs = Character(project_id=other.id, name="别项角色", description="别的项目角色描述")
    session.add_all([mine, theirs])
    await session.flush()

    response = await knowledge_read_service.read_characters(
        session,
        project.id,
        KnowledgeReadRequest(
            items=[
                KnowledgeReadItemRequest(id=mine.id),
                KnowledgeReadItemRequest(id=theirs.id),
            ]
        ),
    )

    assert [item.id for item in response.items] == [mine.id, theirs.id]
    assert response.items[0].status is ReadStatus.OK
    assert response.items[0].content == "本项目的角色描述"
    assert response.items[1].status is ReadStatus.NOT_FOUND
    assert response.items[1].content is None
    assert response.returned_count == 1
    assert response.partial_failure is True


@pytest.mark.asyncio
async def test_long_content_reassembles_losslessly_across_segments(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)
    content = "".join(f"第{index:04d}行内容😀\n" for index in range(500))
    entry = await _add_entry(session, world, uid=1, name="长条目", content=content)

    collected: list[str] = []
    offset = 0
    version: str | None = None
    for _ in range(200):
        requested = (
            KnowledgeReadItemRequest(id=entry.id)
            if offset == 0
            else KnowledgeReadItemRequest(
                id=entry.id, start_offset=offset, expected_version=version
            )
        )
        response = await knowledge_read_service.read_world_entries(
            session,
            project.id,
            KnowledgeReadRequest(items=[requested], max_chars_per_item=1000),
        )
        item = response.items[0]
        assert item.status is ReadStatus.OK
        assert item.start_offset == offset
        assert item.end_offset is not None
        assert item.end_offset == offset + len(item.content or "")
        collected.append(item.content or "")
        version = item.content_version
        if not item.truncated:
            break
        assert item.next_start_offset is not None
        assert item.next_start_offset > offset
        offset = item.next_start_offset
    else:  # pragma: no cover - 只在续读停滞时触发
        pytest.fail("续读未收敛，cursor 停滞")

    assert "".join(collected) == content


@pytest.mark.asyncio
async def test_max_single_line_truncates_inline_without_stalling(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)
    content = "长" * 100_000
    entry = await _add_entry(session, world, uid=1, name="超长单行", content=content)

    first = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(
            items=[KnowledgeReadItemRequest(id=entry.id)], max_chars_per_item=8000
        ),
    )
    head = first.items[0]
    assert head.status is ReadStatus.OK
    assert head.content == "长" * 8000
    assert head.total_chars == 100_000
    assert head.truncated is True
    assert head.next_start_offset == 8000
    assert head.start_line == 1
    assert head.start_line_offset == 0

    second = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(
            items=[
                KnowledgeReadItemRequest(
                    id=entry.id,
                    start_offset=8000,
                    expected_version=head.content_version,
                )
            ],
            max_chars_per_item=8000,
        ),
    )
    tail = second.items[0]
    assert tail.status is ReadStatus.OK
    assert tail.content == "长" * 8000
    assert tail.start_line == 1
    assert tail.start_line_offset == 8000
    assert (head.content or "") + (tail.content or "") == content[:16000]


@pytest.mark.asyncio
async def test_crlf_line_and_inline_offsets(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)
    content = "第一行\r\n第二行😀\r\n第三行"
    entry = await _add_entry(session, world, uid=1, name="换行条目", content=content)

    whole = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(items=[KnowledgeReadItemRequest(id=entry.id)]),
    )
    assert whole.items[0].content == content
    assert whole.items[0].total_chars == 14

    middle = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(
            items=[
                KnowledgeReadItemRequest(
                    id=entry.id,
                    start_offset=7,
                    expected_version=whole.items[0].content_version,
                )
            ]
        ),
    )
    item = middle.items[0]
    assert item.status is ReadStatus.OK
    assert item.content == content[7:]
    assert item.start_line == 2
    assert item.start_line_offset == 2


@pytest.mark.asyncio
async def test_empty_and_terminal_offsets_are_completed_intervals(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)
    empty = await _add_entry(session, world, uid=1, name="空条目", content="")
    filled = await _add_entry(session, world, uid=2, name="有内容", content="abc")

    response = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(
            items=[
                KnowledgeReadItemRequest(id=empty.id),
                KnowledgeReadItemRequest(
                    id=filled.id,
                    start_offset=3,
                    expected_version=compute_content_version("abc"),
                ),
            ]
        ),
    )

    empty_item, terminal_item = response.items
    assert empty_item.status is ReadStatus.OK
    assert empty_item.content == ""
    assert empty_item.total_chars == 0
    assert empty_item.start_offset == 0
    assert empty_item.end_offset == 0
    assert empty_item.start_line == 1
    assert empty_item.start_line_offset == 0
    assert empty_item.truncated is False
    assert empty_item.next_start_offset is None

    assert terminal_item.status is ReadStatus.OK
    assert terminal_item.content == ""
    assert terminal_item.start_offset == 3
    assert terminal_item.end_offset == 3
    assert terminal_item.truncated is False
    assert terminal_item.next_start_offset is None


@pytest.mark.asyncio
async def test_out_of_range_offset_reports_invalid_range(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)
    entry = await _add_entry(session, world, uid=1, name="越界条目", content="abc")

    response = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(
            items=[
                KnowledgeReadItemRequest(
                    id=entry.id,
                    start_offset=4,
                    expected_version=compute_content_version("abc"),
                )
            ]
        ),
    )

    item = response.items[0]
    assert item.status is ReadStatus.INVALID_RANGE
    assert item.start_offset == 4
    assert item.total_chars == 3
    assert item.content is None
    assert item.truncated is False
    assert response.returned_count == 0
    assert response.partial_failure is True


@pytest.mark.asyncio
async def test_scope_isolation_hides_foreign_and_disabled_entries(session) -> None:
    mine = await _create_project(session, "项目A")
    other = await _create_project(session, "项目B")
    world_mine = await _create_world_info(session, mine)
    world_other = await _create_world_info(session, other)

    enabled = await _add_entry(
        session, world_mine, uid=1, name="可用秘宝", content="可用秘宝正文"
    )
    disabled = await _add_entry(
        session,
        world_mine,
        uid=2,
        name="禁用秘宝",
        content="禁用秘宝正文",
        is_enabled=False,
    )
    foreign = await _add_entry(
        session, world_other, uid=1, name="别项秘宝", content="别项秘宝正文"
    )

    response = await knowledge_read_service.read_world_entries(
        session,
        mine.id,
        KnowledgeReadRequest(
            items=[
                KnowledgeReadItemRequest(id=enabled.id),
                KnowledgeReadItemRequest(id=disabled.id),
                KnowledgeReadItemRequest(id=foreign.id),
                KnowledgeReadItemRequest(id="not-a-real-id"),
            ]
        ),
    )

    assert [item.id for item in response.items] == [
        enabled.id,
        disabled.id,
        foreign.id,
        "not-a-real-id",
    ]
    assert response.items[0].status is ReadStatus.OK
    for item in response.items[1:]:
        assert item.status in {ReadStatus.DISABLED, ReadStatus.NOT_FOUND}
        assert item.name is None
        assert item.content is None
        assert item.content_version is None
        assert item.total_chars is None
    assert response.items[1].status is ReadStatus.DISABLED
    assert response.items[2].status is ReadStatus.NOT_FOUND
    assert response.items[3].status is ReadStatus.NOT_FOUND
    assert response.returned_count == 1
    assert response.partial_failure is True


@pytest.mark.asyncio
async def test_project_without_world_book_reports_not_found(session) -> None:
    project = await _create_project(session, "无世界书项目")

    response = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(items=[KnowledgeReadItemRequest(id="whatever")]),
    )

    assert response.items[0].status is ReadStatus.NOT_FOUND
    assert response.returned_count == 0


@pytest.mark.asyncio
async def test_missing_project_is_a_context_error(session) -> None:
    with pytest.raises(KnowledgeReadError) as exc_info:
        await knowledge_read_service.read_characters(
            session,
            "missing-project",
            KnowledgeReadRequest(items=[KnowledgeReadItemRequest(id="whatever")]),
        )
    assert exc_info.value.code is KnowledgeErrorCode.CONTEXT_ERROR


@pytest.mark.asyncio
async def test_continuation_rejects_content_changed_after_first_read(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)
    entry = await _add_entry(session, world, uid=1, name="会变的条目", content="A" * 100)

    first = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(
            items=[KnowledgeReadItemRequest(id=entry.id)], max_chars_per_item=40
        ),
    )
    stale_version = first.items[0].content_version
    assert first.items[0].truncated is True

    entry.content = "B" * 100
    await session.flush()

    continued = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(
            items=[
                KnowledgeReadItemRequest(
                    id=entry.id, start_offset=40, expected_version=stale_version
                )
            ]
        ),
    )
    conflict = continued.items[0]
    assert conflict.status is ReadStatus.VERSION_CONFLICT
    assert conflict.start_offset == 40
    assert conflict.content is None
    assert conflict.content_version is None
    assert continued.returned_count == 0

    # 首读同样拒绝过期版本，避免拼接修改前后的内容
    fresh = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(
            items=[KnowledgeReadItemRequest(id=entry.id, expected_version=stale_version)]
        ),
    )
    assert fresh.items[0].status is ReadStatus.VERSION_CONFLICT

    # 用当前版本重新读取即可继续
    recovered = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(
            items=[
                KnowledgeReadItemRequest(
                    id=entry.id,
                    start_offset=40,
                    expected_version=compute_content_version("B" * 100),
                )
            ]
        ),
    )
    assert recovered.items[0].status is ReadStatus.OK
    assert recovered.items[0].content == "B" * 60


@pytest.mark.asyncio
async def test_mixed_statuses_preserve_input_order_and_counts(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)
    ok = await _add_entry(session, world, uid=1, name="正常", content="正常正文")
    disabled = await _add_entry(
        session, world, uid=2, name="禁用", content="禁用正文", is_enabled=False
    )

    response = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(
            items=[
                KnowledgeReadItemRequest(id=ok.id),
                KnowledgeReadItemRequest(id="missing"),
                KnowledgeReadItemRequest(id=disabled.id),
                KnowledgeReadItemRequest(id="phantom"),
            ]
        ),
    )

    assert [item.id for item in response.items] == [ok.id, "missing", disabled.id, "phantom"]
    assert [item.status for item in response.items] == [
        ReadStatus.OK,
        ReadStatus.NOT_FOUND,
        ReadStatus.DISABLED,
        ReadStatus.NOT_FOUND,
    ]
    assert response.returned_count == 1
    assert response.partial_failure is True
    assert response.budget_exhausted is False


@pytest.mark.asyncio
async def test_content_budget_is_allocated_in_input_order(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)
    entries = [
        await _add_entry(
            session, world, uid=index, name=f"预算条目{index}", content="x" * 5000
        )
        for index in range(1, 11)
    ]

    response = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(
            items=[KnowledgeReadItemRequest(id=entry.id) for entry in entries],
            max_chars_per_item=4000,
        ),
    )

    per_item_capacity = 4000
    expected_ok = READ_CONTENT_BUDGET_CHARS // per_item_capacity
    assert expected_ok == 4
    assert [item.status for item in response.items[:expected_ok]] == [
        ReadStatus.OK
    ] * expected_ok
    assert all(
        item.status is ReadStatus.BUDGET_EXHAUSTED
        for item in response.items[expected_ok:]
    )
    assert response.returned_count == expected_ok
    assert response.partial_failure is True
    assert response.budget_exhausted is True

    delivered = "".join(item.content or "" for item in response.items)
    assert len(delivered) == READ_CONTENT_BUDGET_CHARS
    # 预算耗尽项保留原 offset，供单独重试
    for item in response.items[expected_ok:]:
        assert item.start_offset == 0
        assert item.content is None


@pytest.mark.asyncio
async def test_serialized_budget_shortens_last_interval_and_degrades_rest(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)
    # 控制字符在 JSON 中膨胀为 \u00XX（6 字符），确保序列化预算先于正文预算触发
    entries = [
        await _add_entry(
            session, world, uid=index, name=f"膨胀条目{index}", content="\x01" * 8000
        )
        for index in range(1, 3)
    ]

    response = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(
            items=[KnowledgeReadItemRequest(id=entry.id) for entry in entries],
            max_chars_per_item=8000,
        ),
    )

    assert len(_serialized(response)) <= SERIALIZED_OUTPUT_BUDGET_CHARS
    assert response.items[0].status is ReadStatus.OK
    assert response.items[0].truncated is True
    assert response.items[0].next_start_offset == response.items[0].end_offset
    assert len(response.items[0].content or "") < 8000
    assert response.items[1].status is ReadStatus.BUDGET_EXHAUSTED
    assert response.items[1].content is None
    assert response.returned_count == 1
    assert response.partial_failure is True
    assert response.budget_exhausted is True

    # 缩短后的区间仍可无损续读
    head = response.items[0]
    resumed = await knowledge_read_service.read_world_entries(
        session,
        project.id,
        KnowledgeReadRequest(
            items=[
                KnowledgeReadItemRequest(
                    id=head.id,
                    start_offset=head.next_start_offset or 0,
                    expected_version=head.content_version,
                )
            ],
            max_chars_per_item=1,
        ),
    )
    assert resumed.items[0].status is ReadStatus.OK
    assert resumed.items[0].start_offset == head.end_offset


@pytest.mark.asyncio
async def test_batch_read_query_count_is_bounded_and_independent_of_item_count(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)
    entries = [
        await _add_entry(session, world, uid=index, name=f"查询条目{index}", content="正文")
        for index in range(1, 11)
    ]

    async def run(item_count: int) -> int:
        counter = {"value": 0}

        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            if statement.strip().upper().startswith("SELECT"):
                counter["value"] += 1

        bind = session.sync_session.get_bind()
        event.listen(bind, "before_cursor_execute", before_cursor_execute)
        try:
            await knowledge_read_service.read_world_entries(
                session,
                project.id,
                KnowledgeReadRequest(
                    items=[
                        KnowledgeReadItemRequest(id=entry.id)
                        for entry in entries[:item_count]
                    ]
                ),
            )
        finally:
            event.remove(bind, "before_cursor_execute", before_cursor_execute)
        return counter["value"]

    single = await run(1)
    many = await run(10)

    assert single <= 3, f"单项读取 SELECT 次数超标: {single}"
    assert many == single, f"SELECT 次数随结果数量增长: {single} -> {many}"


@pytest.mark.asyncio
async def test_character_batch_read_query_count_is_bounded(session) -> None:
    project = await _create_project(session)
    characters = [
        Character(project_id=project.id, name=f"批量角色{index}", description="角色正文")
        for index in range(1, 11)
    ]
    session.add_all(characters)
    await session.flush()

    counter = {"value": 0}

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().upper().startswith("SELECT"):
            counter["value"] += 1

    bind = session.sync_session.get_bind()
    event.listen(bind, "before_cursor_execute", before_cursor_execute)
    try:
        response = await knowledge_read_service.read_characters(
            session,
            project.id,
            KnowledgeReadRequest(
                items=[KnowledgeReadItemRequest(id=c.id) for c in characters]
            ),
        )
    finally:
        event.remove(bind, "before_cursor_execute", before_cursor_execute)

    assert response.returned_count == 10
    assert counter["value"] <= 3, f"角色批量读取 SELECT 次数超标: {counter['value']}"


def test_read_request_bounds_reject_duplicates_and_bad_ranges() -> None:
    with pytest.raises(ValidationError):
        KnowledgeReadRequest(
            items=[
                KnowledgeReadItemRequest(id="entry-1"),
                KnowledgeReadItemRequest(id="entry-1"),
            ]
        )
    with pytest.raises(ValidationError):
        KnowledgeReadRequest(
            items=[KnowledgeReadItemRequest(id="entry-1", start_offset=1)]
        )
    with pytest.raises(ValidationError):
        KnowledgeReadRequest(
            items=[KnowledgeReadItemRequest(id="entry-1")], max_chars_per_item=0
        )
    with pytest.raises(ValidationError):
        KnowledgeReadRequest(
            items=[KnowledgeReadItemRequest(id=f"entry-{index}") for index in range(11)]
        )
