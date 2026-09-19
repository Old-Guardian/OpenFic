# -*- coding: utf-8 -*-
"""关键词检索服务 (T3) 核心功能、契约与生命周期测试。"""

from __future__ import annotations

import pytest
from sqlalchemy import event

from app.storage.models.character import Character
from app.storage.models.project import Project
from app.storage.models.world_info import WorldInfo
from app.storage.models.world_info_entry import WorldInfoEntry
from app.storage.repos import (
    character_alias_repo,
    world_info_entry_alias_repo,
)
from app.storage.services import (
    character_service,
    knowledge_search_service,
)
from app.storage.services.knowledge_contracts import (
    MAX_EXCERPTS_PER_ITEM,
    SERIALIZED_OUTPUT_BUDGET_CHARS,
    KnowledgeErrorCode,
    KnowledgeKind,
    KnowledgeSearchRequest,
    SearchMatchMode,
    SearchReason,
)
from app.storage.services.knowledge_search_service import KnowledgeSearchError


async def _create_project(session, title: str = "检索测试项目") -> Project:
    project = Project(title=title, description="检索测试")
    session.add(project)
    await session.flush()
    return project


async def _create_world_info(session, project: Project) -> WorldInfo:
    world = WorldInfo(project_id=project.id, name="主世界书", description="")
    session.add(world)
    await session.flush()
    return world


@pytest.mark.asyncio
async def test_search_world_entries_chinese_substring_and_case(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)

    # 1. 中文连续子串与正文命中
    e1 = WorldInfoEntry(
        world_info_id=world.id,
        uid=1,
        name="燃魂术",
        order=1,
        content="每次施展至少消耗十年寿命。",
        is_enabled=True,
    )
    # 2. 英文 ASCII 大小写与别名命中
    e2 = WorldInfoEntry(
        world_info_id=world.id,
        uid=2,
        name="寒冰决",
        order=2,
        content="Frost Nova 冰霜新星法术。",
        is_enabled=True,
    )
    session.add_all([e1, e2])
    await session.flush()

    await world_info_entry_alias_repo.replace_for_entry(
        session, e1.id, ["焚命术", "SoulBurn"]
    )
    await world_info_entry_alias_repo.replace_for_entry(
        session, e2.id, ["FROST-BITE", "极寒法"]
    )

    # 中文连续子串与别名命中
    req1 = KnowledgeSearchRequest(query="焚命", match=SearchMatchMode.ALL)
    res1 = await knowledge_search_service.search_world_entries(session, project.id, req1)
    assert res1.total_matches == 1
    assert res1.returned_count == 1
    assert res1.items[0].id == e1.id
    assert res1.items[0].name == "燃魂术"
    assert "alias" in res1.items[0].matched_fields

    # 英文 ASCII 大小写不敏感匹配
    req2 = KnowledgeSearchRequest(query="soulburn", match=SearchMatchMode.ALL)
    res2 = await knowledge_search_service.search_world_entries(session, project.id, req2)
    assert res2.total_matches == 1
    assert res2.items[0].id == e1.id

    req3 = KnowledgeSearchRequest(query="frost-bite", match=SearchMatchMode.ALL)
    res3 = await knowledge_search_service.search_world_entries(session, project.id, req3)
    assert res3.total_matches == 1
    assert res3.items[0].id == e2.id

    # 仅正文独有词命中
    req4 = KnowledgeSearchRequest(query="十年寿命", match=SearchMatchMode.ALL)
    res4 = await knowledge_search_service.search_world_entries(session, project.id, req4)
    assert res4.total_matches == 1
    assert res4.items[0].id == e1.id
    assert res4.items[0].matched_fields == ["content"]
    assert len(res4.items[0].excerpts) == 1
    assert "十年寿命" in res4.items[0].excerpts[0].text


@pytest.mark.asyncio
async def test_search_match_modes_all_and_any_cross_field(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)

    # e1: 名称含 "燃魂"，正文含 "寿命" -> 跨字段同时满足
    e1 = WorldInfoEntry(
        world_info_id=world.id,
        uid=1,
        name="燃魂术",
        order=1,
        content="禁术秘典，消耗寿命为代价。",
        is_enabled=True,
    )
    # e2: 仅名称含 "燃魂"，不含 "寿命"
    e2 = WorldInfoEntry(
        world_info_id=world.id,
        uid=2,
        name="燃魂丹",
        order=2,
        content="短时间提升灵力的丹药。",
        is_enabled=True,
    )
    session.add_all([e1, e2])
    await session.flush()

    # match=all: 必须同时满足两个词（允许跨字段：词1在名称，词2在正文）
    req_all = KnowledgeSearchRequest(query="燃魂 寿命", match=SearchMatchMode.ALL)
    res_all = await knowledge_search_service.search_world_entries(session, project.id, req_all)
    assert res_all.total_matches == 1
    assert res_all.items[0].id == e1.id
    assert set(res_all.items[0].matched_fields) == {"name", "content"}
    assert set(res_all.items[0].matched_terms) == {"燃魂", "寿命"}

    # match=any: 任一满足即可
    req_any = KnowledgeSearchRequest(query="燃魂 寿命", match=SearchMatchMode.ANY)
    res_any = await knowledge_search_service.search_world_entries(session, project.id, req_any)
    assert res_any.total_matches == 2
    matched_ids = {item.id for item in res_any.items}
    assert matched_ids == {e1.id, e2.id}


@pytest.mark.asyncio
async def test_search_special_characters_literal_matching(session) -> None:
    project = await _create_project(session)

    c1 = Character(
        project_id=project.id,
        name="Special_Name%100",
        description=r"Code is C:\Users\HP and discount is 50%_off! Also 'quote' and \"double\".",
    )
    c2 = Character(
        project_id=project.id,
        name="NormalCharacter",
        description="Nothing special here.",
    )
    session.add_all([c1, c2])
    await session.flush()

    # 测试 % 和 _ 作为普通字符匹配，不作为 LIKE 通配符
    req_percent = KnowledgeSearchRequest(query="%100")
    res_percent = await knowledge_search_service.search_characters(session, project.id, req_percent)
    assert res_percent.total_matches == 1
    assert res_percent.items[0].id == c1.id

    req_underscore = KnowledgeSearchRequest(query="%_off")
    res_underscore = await knowledge_search_service.search_characters(session, project.id, req_underscore)
    assert res_underscore.total_matches == 1
    assert res_underscore.items[0].id == c1.id

    # 反斜杠与引号字面匹配
    req_backslash = KnowledgeSearchRequest(query=r"C:\Users\HP")
    res_backslash = await knowledge_search_service.search_characters(session, project.id, req_backslash)
    assert res_backslash.total_matches == 1
    assert res_backslash.items[0].id == c1.id

    req_quote = KnowledgeSearchRequest(query="'quote'")
    res_quote = await knowledge_search_service.search_characters(session, project.id, req_quote)
    assert res_quote.total_matches == 1
    assert res_quote.items[0].id == c1.id


@pytest.mark.asyncio
async def test_search_stable_ranking_and_deduplication(session) -> None:
    """验证 5 档排序分级与同档打破平衡规则：
    1. 正式名称与完整 query 精确匹配
    2. 任一别名与完整 query 精确匹配
    3. 名称包含查询词
    4. 别名包含查询词
    5. 仅正文命中
    6. 同档按命中词数量降序；再按实体 ID 升序
    """
    project = await _create_project(session)

    # A: 正式名称精确匹配 "燃魂" -> Rank 1
    cA = Character(project_id=project.id, name="燃魂", description="描述A")
    # B: 别名精确匹配 "燃魂" -> Rank 2
    cB = Character(project_id=project.id, name="秘传甲", description="描述B")
    # C: 名称包含 "燃魂" -> Rank 3
    cC = Character(project_id=project.id, name="燃魂秘要", description="描述C")
    # D: 别名包含 "燃魂" -> Rank 4
    cD = Character(project_id=project.id, name="秘传乙", description="描述D")
    # E: 仅正文命中 "燃魂" -> Rank 5
    cE = Character(project_id=project.id, name="普通条目", description="正文中提到了燃魂法术")

    session.add_all([cA, cB, cC, cD, cE])
    await session.flush()

    # 为 B 和 D 写入别名；为 B 写入多个别名测试防止重复结果
    await character_alias_repo.replace_for_character(session, cB.id, ["燃魂", "别名二", "别名三"])
    await character_alias_repo.replace_for_character(session, cD.id, ["九转燃魂决"])

    req = KnowledgeSearchRequest(query="燃魂", match=SearchMatchMode.ALL)
    res = await knowledge_search_service.search_characters(session, project.id, req)

    # 5 个条目全部命中，且每个条目只出现一次（无 JOIN 导致的重复）
    assert res.total_matches == 5
    ids = [item.id for item in res.items]
    assert len(ids) == len(set(ids))
    assert ids == [cA.id, cB.id, cC.id, cD.id, cE.id]


@pytest.mark.asyncio
async def test_search_tie_breaking_by_term_count_and_id(session) -> None:
    project = await _create_project(session)

    # 在 Rank 3（名称包含）内：
    # c1 包含两个词 "燃魂" 与 "寿命"
    c1 = Character(project_id=project.id, name="燃魂寿命研究", description="")
    # c2 仅包含一个词 "燃魂"
    c2 = Character(project_id=project.id, name="燃魂之引", description="")

    session.add_all([c1, c2])
    await session.flush()

    # match=any 下，c1 命中 2 词，c2 命中 1 词，同属 Rank 3，c1 应排在 c2 之前
    req = KnowledgeSearchRequest(query="燃魂 寿命", match=SearchMatchMode.ANY)
    res = await knowledge_search_service.search_characters(session, project.id, req)
    assert res.total_matches == 2
    assert res.items[0].id == c1.id
    assert res.items[1].id == c2.id


@pytest.mark.asyncio
async def test_search_scope_and_isolation(session) -> None:
    p1 = await _create_project(session, "项目1")
    p2 = await _create_project(session, "项目2")

    w1 = await _create_world_info(session, p1)
    w2 = await _create_world_info(session, p2)

    # 项目1中的条目（含禁用项）
    e1_enabled = WorldInfoEntry(
        world_info_id=w1.id, uid=1, name="秘宝", order=1, content="可用秘宝", is_enabled=True
    )
    e1_disabled = WorldInfoEntry(
        world_info_id=w1.id, uid=2, name="秘宝禁用", order=2, content="禁用秘宝", is_enabled=False
    )
    # 项目2中同名条目
    e2_enabled = WorldInfoEntry(
        world_info_id=w2.id, uid=1, name="秘宝", order=1, content="项目2秘宝", is_enabled=True
    )

    session.add_all([e1_enabled, e1_disabled, e2_enabled])
    await session.flush()

    # 查询项目1：应只查出 e1_enabled，严禁出现 e1_disabled 和 e2_enabled
    req = KnowledgeSearchRequest(query="秘宝")
    res1 = await knowledge_search_service.search_world_entries(session, p1.id, req)
    assert res1.total_matches == 1
    assert res1.items[0].id == e1_enabled.id

    # 查询未绑定世界书的项目
    p3 = await _create_project(session, "无世界书项目")
    res3 = await knowledge_search_service.search_world_entries(session, p3.id, req)
    assert res3.total_matches == 0
    assert res3.reason == SearchReason.NO_WORLD_BOOK
    assert not res3.items

    # 查询不存在的项目 -> context_error
    with pytest.raises(KnowledgeSearchError) as exc_info:
        await knowledge_search_service.search_world_entries(
            session, "non_existent_project_id", req
        )
    assert exc_info.value.code == KnowledgeErrorCode.CONTEXT_ERROR


@pytest.mark.asyncio
async def test_search_pagination_cursor_and_integrity(session) -> None:
    project = await _create_project(session)

    # 创建 25 个角色
    characters = [
        Character(
            project_id=project.id,
            name=f"角色-{i:02d}",
            description=f"关于通用线索的描述 {i:02d}",
        )
        for i in range(25)
    ]
    session.add_all(characters)
    await session.flush()

    # 遍历分页：limit=10
    req_page1 = KnowledgeSearchRequest(query="通用线索", limit=10)
    page1 = await knowledge_search_service.search_characters(session, project.id, req_page1)
    assert page1.returned_count == 10
    assert page1.total_matches == 25
    assert page1.has_more is True
    assert page1.next_cursor is not None

    req_page2 = KnowledgeSearchRequest(query="通用线索", limit=10, cursor=page1.next_cursor)
    page2 = await knowledge_search_service.search_characters(session, project.id, req_page2)
    assert page2.returned_count == 10
    assert page2.total_matches == 25
    assert page2.has_more is True
    assert page2.next_cursor is not None

    req_page3 = KnowledgeSearchRequest(query="通用线索", limit=10, cursor=page2.next_cursor)
    page3 = await knowledge_search_service.search_characters(session, project.id, req_page3)
    assert page3.returned_count == 5
    assert page3.total_matches == 25
    assert page3.has_more is False
    assert page3.next_cursor is None

    # 跨页无重无漏
    all_retrieved_ids = [
        item.id for page in (page1, page2, page3) for item in page.items
    ]
    assert len(all_retrieved_ids) == 25
    assert len(set(all_retrieved_ids)) == 25


@pytest.mark.asyncio
async def test_cursor_validation_and_staleness(session) -> None:
    p1 = await _create_project(session, "项目A")
    p2 = await _create_project(session, "项目B")

    c1 = Character(project_id=p1.id, name="剑客", description="青锋宝剑")
    session.add(c1)
    await session.flush()

    req = KnowledgeSearchRequest(query="宝剑", limit=1)
    res = await knowledge_search_service.search_characters(session, p1.id, req)
    valid_cursor = res.next_cursor
    # 结果只有 1 条，limit=1 时 has_more 为 False，next_cursor 为 None
    # 我们再添加一条以生成有效 cursor
    c2 = Character(project_id=p1.id, name="刀客", description="绝世宝剑")
    session.add(c2)
    await session.flush()

    res = await knowledge_search_service.search_characters(session, p1.id, req)
    valid_cursor = res.next_cursor
    assert valid_cursor is not None

    # 1. 篡改或非法的 cursor
    with pytest.raises(KnowledgeSearchError) as exc_invalid:
        await knowledge_search_service.search_characters(
            session, p1.id, KnowledgeSearchRequest(query="宝剑", cursor="invalid_base64_string")
        )
    assert exc_invalid.value.code == KnowledgeErrorCode.INVALID_CURSOR

    # 2. 跨项目复用 cursor
    with pytest.raises(KnowledgeSearchError) as exc_cross_project:
        await knowledge_search_service.search_characters(
            session, p2.id, KnowledgeSearchRequest(query="宝剑", cursor=valid_cursor)
        )
    assert exc_cross_project.value.code == KnowledgeErrorCode.INVALID_CURSOR

    # 3. 跨查询词复用 cursor
    with pytest.raises(KnowledgeSearchError) as exc_cross_query:
        await knowledge_search_service.search_characters(
            session, p1.id, KnowledgeSearchRequest(query="其他词", cursor=valid_cursor)
        )
    assert exc_cross_query.value.code == KnowledgeErrorCode.INVALID_CURSOR

    # 4. 跨实体类别复用 cursor (角色 cursor 用于世界书搜索)
    await _create_world_info(session, p1)
    with pytest.raises(KnowledgeSearchError) as exc_cross_kind:
        await knowledge_search_service.search_world_entries(
            session, p1.id, KnowledgeSearchRequest(query="宝剑", cursor=valid_cursor)
        )
    assert exc_cross_kind.value.code == KnowledgeErrorCode.INVALID_CURSOR

    # 5. 数据变更使旧 cursor 失效 (CURSOR_STALE)
    # 模拟 T2 场景：只改别名也会更新 updated_at
    await character_service.update_character(
        session, c1.id, aliases=["白衣剑客"]
    )
    # 再次使用原 cursor 发起查询 (保持 limit=1)
    with pytest.raises(KnowledgeSearchError) as exc_stale:
        await knowledge_search_service.search_characters(
            session, p1.id, KnowledgeSearchRequest(query="宝剑", limit=1, cursor=valid_cursor)
        )
    assert exc_stale.value.code == KnowledgeErrorCode.CURSOR_STALE
    assert exc_stale.value.retryable is True


@pytest.mark.asyncio
async def test_excerpt_extraction_lines_and_merging(session) -> None:
    # 构造含多行、换行符和 emoji 的正文
    content = (
        "第一行：普通介绍。\n"
        "第二行：包含关键词A的地方。\n"
        "第三行：没有任何匹配。\n"
        "第四行：包含😀关键词B与相邻关键词A在此处。\n"
        "第五行：长尾说明。"
    )
    terms = ("关键词A", "关键词B")
    excerpts, truncated = knowledge_search_service.extract_excerpts(content, terms)

    assert len(excerpts) <= MAX_EXCERPTS_PER_ITEM
    assert not truncated  # 所有命中都被切片覆盖
    for excerpt in excerpts:
        assert excerpt.end_offset - excerpt.start_offset == len(excerpt.text)
        assert content[excerpt.start_offset : excerpt.end_offset] == excerpt.text
        assert excerpt.line_start >= 1
        assert excerpt.line_end >= excerpt.line_start


@pytest.mark.asyncio
async def test_query_count_upper_bound(session) -> None:
    """验证单次搜索业务 SELECT 语句不超过 6 次。"""
    project = await _create_project(session)
    world = await _create_world_info(session, project)

    e = WorldInfoEntry(
        world_info_id=world.id,
        uid=1,
        name="测试条目",
        order=1,
        content="正文内容",
        is_enabled=True,
    )
    session.add(e)
    await session.flush()
    await world_info_entry_alias_repo.replace_for_entry(session, e.id, ["别名条目"])

    # 监听并统计 SELECT 语句次数
    select_count = 0

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        nonlocal select_count
        stmt_upper = statement.strip().upper()
        if stmt_upper.startswith("SELECT"):
            select_count += 1

    bind = session.sync_session.get_bind()
    event.listen(bind, "before_cursor_execute", before_cursor_execute)

    try:
        req = KnowledgeSearchRequest(query="测试", limit=10)
        res = await knowledge_search_service.search_world_entries(session, project.id, req)
        assert res.total_matches >= 1
        # 严格小于等于 6 次业务 SELECT
        assert select_count <= 6, f"SELECT 执行次数超标: {select_count}"
    finally:
        event.remove(session.sync_session.get_bind(), "before_cursor_execute", before_cursor_execute)


@pytest.mark.asyncio
async def test_excerpts_truncation_flag() -> None:
    """验证命中位置超过 3 段时 excerpts_truncated 设为 True。"""
    # 构造 5 处相距遥远的匹配位置
    blocks = [f"段落{i} 关键词目标 " + ("无关填充文本" * 40) for i in range(5)]
    content = "\n\n".join(blocks)
    excerpts, truncated = knowledge_search_service.extract_excerpts(content, ["关键词目标"])
    assert len(excerpts) == MAX_EXCERPTS_PER_ITEM  # 3
    assert truncated is True

    # 仅 2 处匹配时，全部覆盖，truncated 必须为 False
    short_content = "段落A 关键词目标 填充\n段落B 关键词目标 填充"
    excerpts2, truncated2 = knowledge_search_service.extract_excerpts(short_content, ["关键词目标"])
    assert len(excerpts2) <= 2
    assert truncated2 is False


@pytest.mark.asyncio
async def test_serialized_output_budget_shrinking() -> None:
    """验证响应 JSON 超出 32,768 字符时自动裁剪并更新游标。"""
    from app.storage.services.knowledge_contracts import KnowledgeSearchItem, SearchExcerpt

    # 构造每个约 2,600 字符的大候选条目，使 20 项必然超过 32,768
    items: list[KnowledgeSearchItem] = []
    for i in range(20):
        items.append(
            KnowledgeSearchItem(
                id=f"entry_{i:04d}",
                kind=KnowledgeKind.WORLD_ENTRY,
                name=f"条目名称_{i:04d}" + "甲" * 50,
                aliases=[f"别名_{i:04d}_{j}_" + "乙" * 80 for j in range(20)],
                matched_fields=["name", "content"],
                matched_terms=["关键词"],
                excerpts=[
                    SearchExcerpt(
                        text="关键词" + "正" * 230,
                        start_offset=0,
                        end_offset=233,
                        line_start=1,
                        line_end=1,
                    )
                    for _ in range(3)
                ],
                content_version="sha256:" + "a" * 64,
                excerpts_truncated=False,
            )
        )

    full_resp = knowledge_search_service.KnowledgeSearchResponse(
        items=items,
        returned_count=len(items),
        total_matches=50,
        has_more=True,
        next_cursor="dummy_cursor",
        match_scope="literal_terms",
    )
    serialized_len = len(knowledge_search_service._serialize_response(full_resp))
    assert serialized_len > SERIALIZED_OUTPUT_BUDGET_CHARS

    shrunk_resp = knowledge_search_service._apply_serialized_output_budget(
        response=full_resp,
        kind=KnowledgeKind.WORLD_ENTRY,
        project_id="test_proj",
        query_fingerprint="sha256:" + "1" * 64,
        dataset_fingerprint="sha256:" + "2" * 64,
        limit=20,
        offset=0,
        total_matches=50,
    )
    shrunk_len = len(knowledge_search_service._serialize_response(shrunk_resp))
    assert shrunk_len <= SERIALIZED_OUTPUT_BUDGET_CHARS
    assert len(shrunk_resp.items) < len(items)
    assert shrunk_resp.returned_count == len(shrunk_resp.items)
    assert shrunk_resp.has_more is True
    # 验证 next_cursor 正确记录了缩减后的 offset
    assert shrunk_resp.next_cursor is not None
    decoded = knowledge_search_service.decode_cursor(shrunk_resp.next_cursor)
    assert decoded.offset == len(shrunk_resp.items)



@pytest.mark.asyncio
async def test_list_world_entries_paginates_enabled_entries_only(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)
    entries = [
        WorldInfoEntry(
            world_info_id=world.id,
            uid=index,
            name=f"条目{index:02d}",
            order=index,
            content="正文",
            is_enabled=index % 2 == 1,
        )
        for index in range(1, 8)
    ]
    session.add_all(entries)
    await session.flush()

    page1 = await knowledge_search_service.list_world_entries(
        session, project.id, limit=2
    )
    assert [item.name for item in page1.items] == ["条目01", "条目03"]
    assert page1.total == 4
    assert page1.has_more is True
    assert page1.next_cursor is not None
    assert page1.reason is None

    page2 = await knowledge_search_service.list_world_entries(
        session, project.id, limit=2, cursor=page1.next_cursor
    )
    assert [item.name for item in page2.items] == ["条目05", "条目07"]
    assert page2.total == 4
    assert page2.has_more is False
    assert page2.next_cursor is None

    collected = [item.name for item in (*page1.items, *page2.items)]
    assert collected == ["条目01", "条目03", "条目05", "条目07"]


@pytest.mark.asyncio
async def test_list_world_entries_reports_missing_world_book(session) -> None:
    project = await _create_project(session, "未绑定世界书")

    page = await knowledge_search_service.list_world_entries(
        session, project.id, limit=20
    )

    assert page.items == []
    assert page.total == 0
    assert page.has_more is False
    assert page.reason is SearchReason.NO_WORLD_BOOK


@pytest.mark.asyncio
async def test_list_characters_cursor_is_scoped_and_stales_on_change(session) -> None:
    project = await _create_project(session)
    other = await _create_project(session, "另一个项目")
    characters = [
        Character(project_id=project.id, name=f"角色{index:02d}", description="描述")
        for index in range(3)
    ]
    session.add_all(characters)
    await session.flush()

    page1 = await knowledge_search_service.list_characters(session, project.id, limit=2)
    assert page1.total == 3
    assert page1.has_more is True
    cursor = page1.next_cursor
    assert cursor is not None

    # 换 limit 后 cursor 失效
    with pytest.raises(KnowledgeSearchError) as exc_limit:
        await knowledge_search_service.list_characters(
            session, project.id, limit=1, cursor=cursor
        )
    assert exc_limit.value.code == KnowledgeErrorCode.INVALID_CURSOR

    # 跨项目复用失效
    with pytest.raises(KnowledgeSearchError) as exc_project:
        await knowledge_search_service.list_characters(
            session, other.id, limit=2, cursor=cursor
        )
    assert exc_project.value.code == KnowledgeErrorCode.INVALID_CURSOR

    # 数据变化后失效
    await character_service.update_character(session, characters[0].id, aliases=["别称"])
    with pytest.raises(KnowledgeSearchError) as exc_stale:
        await knowledge_search_service.list_characters(
            session, project.id, limit=2, cursor=cursor
        )
    assert exc_stale.value.code == KnowledgeErrorCode.CURSOR_STALE

    # 重新从第一页列举可正常继续
    fresh = await knowledge_search_service.list_characters(session, project.id, limit=2)
    assert fresh.next_cursor is not None
    page2 = await knowledge_search_service.list_characters(
        session, project.id, limit=2, cursor=fresh.next_cursor
    )
    assert page2.total == 3
    assert page2.has_more is False


@pytest.mark.asyncio
async def test_list_and_search_cursors_are_not_interchangeable(session) -> None:
    project = await _create_project(session)
    world = await _create_world_info(session, project)
    entries = [
        WorldInfoEntry(
            world_info_id=world.id,
            uid=index,
            name=f"目标条目{index}",
            order=index,
            content="正文",
            is_enabled=True,
        )
        for index in range(1, 4)
    ]
    session.add_all(entries)
    await session.flush()

    page = await knowledge_search_service.list_world_entries(
        session, project.id, limit=1
    )
    assert page.next_cursor is not None

    # 目录 cursor 不能用于搜索
    with pytest.raises(KnowledgeSearchError) as exc_cross:
        await knowledge_search_service.search_world_entries(
            session,
            project.id,
            KnowledgeSearchRequest(query="目标条目", limit=1, cursor=page.next_cursor),
        )
    assert exc_cross.value.code == KnowledgeErrorCode.INVALID_CURSOR

    search = await knowledge_search_service.search_world_entries(
        session, project.id, KnowledgeSearchRequest(query="目标条目", limit=1)
    )
    assert search.next_cursor is not None

    # 搜索 cursor 不能用于目录
    with pytest.raises(KnowledgeSearchError) as exc_reverse:
        await knowledge_search_service.list_world_entries(
            session, project.id, limit=1, cursor=search.next_cursor
        )
    assert exc_reverse.value.code == KnowledgeErrorCode.INVALID_CURSOR


@pytest.mark.asyncio
async def test_list_world_entries_requires_existing_project(session) -> None:
    with pytest.raises(KnowledgeSearchError) as exc_info:
        await knowledge_search_service.list_world_entries(
            session, "missing-project", limit=20
        )
    assert exc_info.value.code == KnowledgeErrorCode.CONTEXT_ERROR
