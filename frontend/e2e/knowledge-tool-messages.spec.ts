import { expect, test } from "@playwright/test";

import { AGENT_RUNNING_STATUS } from "../src/features/assistant/components/agent/agent-running-status";
import {
  getKnowledgeReadDetail,
  getKnowledgeSearchDetail,
  getLegacyListDetail,
  getLegacySingleReadDetail,
  highlightMatchedTerms,
  normalizeKnowledgeReadData,
  normalizeKnowledgeSearchData,
} from "../src/features/assistant/components/agent/message-blocks/tools/knowledge/knowledge-tool-message.utils";
import {
  REGISTERED_TOOL_NAMES,
  TOOL_DESCRIPTOR_META,
  getToolDescriptorMeta,
  isExploreToolName,
} from "../src/features/assistant/components/agent/message-blocks/tools/shared/tool-message-catalog";
import { resolveToolMessageVisibilityState } from "../src/features/assistant/components/agent/message-blocks/tools/shared/tool-message-utils";
import { normalizeToolResult } from "../src/features/assistant/lib/tool-result-normalization";
import type { AgentMessage } from "../src/lib/agent.types";

import contractSamples from "../../backend/tests/fixtures/knowledge_retrieval_contract_samples.json" with { type: "json" };

test.describe("T7: 检索与批量读取工具结果展示与旧工具兼容", () => {
  test("新工具在元数据目录中完整注册", () => {
    const requiredTools = [
      "search_world_entries",
      "search_characters",
      "read_world_entries",
      "read_characters",
    ] as const;

    for (const toolName of requiredTools) {
      // 1. REGISTERED_TOOL_NAMES 包含该工具
      expect(REGISTERED_TOOL_NAMES).toContain(toolName);

      // 2. 目录元数据正确设置
      const meta = TOOL_DESCRIPTOR_META[toolName];
      expect(meta).toBeDefined();
      expect(meta.group).toBe("context");
      expect(meta.isExplore).toBe(true);
      expect(meta.contentMode).toBe("expandable");

      // 3. 元数据解析 helper
      const descriptorMeta = getToolDescriptorMeta(toolName);
      expect(descriptorMeta).not.toBeNull();
      expect(descriptorMeta?.toolName).toBe(toolName);
      expect(descriptorMeta?.group).toBe("context");
      expect(descriptorMeta?.isExplore).toBe(true);
      expect(descriptorMeta?.contentMode).toBe("expandable");
      expect(isExploreToolName(toolName)).toBe(true);
    }
  });

  test("运行状态映射覆盖四个新检索与读取工具", () => {
    // 验证运行状态字典映射
    const runningStatusMap = (AGENT_RUNNING_STATUS as Record<string, string>);
    expect(runningStatusMap.worldRead).toBe("worldRead");
    expect(runningStatusMap.characterRead).toBe("characterRead");
  });

  test("搜索工具解析 T0 契约样本：候选、命中字段、正文片段与分页指示", () => {
    const page1Sample = contractSamples.search_page_1;
    const searchMsg: AgentMessage = {
      id: "msg-search-1",
      type: "tool",
      timestamp: Date.now(),
      status: "completed",
      toolName: "search_world_entries",
      toolArgs: contractSamples.search_request,
      toolResult: page1Sample,
    };

    // 1. 数据归一化解析
    const parsed = normalizeKnowledgeSearchData(page1Sample);
    expect(parsed).not.toBeNull();
    expect(parsed?.total_matches).toBe(2);
    expect(parsed?.returned_count).toBe(1);
    expect(parsed?.has_more).toBe(true);
    expect(parsed?.items.length).toBe(1);

    const item = parsed!.items[0];
    expect(item.id).toBe("entry-1");
    expect(item.name).toBe("燃魂术");
    expect(item.aliases).toEqual(["焚命术"]);
    expect(item.matched_fields).toEqual(["name", "content"]);
    expect(item.matched_terms).toEqual(["燃魂", "寿命"]);
    expect(item.excerpts.length).toBe(1);
    expect(item.excerpts[0].text).toBe("寿命");
    expect(item.excerpts[0].line_start).toBe(3);
    expect(item.excerpts[0].line_end).toBe(3);
    expect(item.excerpts[0].start_offset).toBe(20);
    expect(item.excerpts[0].end_offset).toBe(22);

    // 2. 标题栏详情摘要展示（包含关键词、匹配数与更多提示）
    const detail = getKnowledgeSearchDetail(searchMsg);
    expect(detail).toBeDefined();
    expect(detail).toContain("燃魂 寿命");
    expect(detail).toContain("2");

    // 3. 最后一页测试 (has_more: false)
    const page2Sample = contractSamples.search_page_2;
    const searchMsg2: AgentMessage = {
      id: "msg-search-2",
      type: "tool",
      timestamp: Date.now(),
      status: "completed",
      toolName: "search_world_entries",
      toolArgs: contractSamples.search_request,
      toolResult: page2Sample,
    };
    const parsed2 = normalizeKnowledgeSearchData(page2Sample);
    expect(parsed2?.has_more).toBe(false);
    expect(parsed2?.next_cursor).toBeNull();
    const detail2 = getKnowledgeSearchDetail(searchMsg2);
    expect(detail2).toBeDefined();
    expect(detail2).toContain("2");
  });

  test("批量读取工具解析 T0 契约样本：部分失败不渲染为全失败，单项错误保留", () => {
    const partialReadSample = contractSamples.read_partial_response;
    const readMsg: AgentMessage = {
      id: "msg-read-1",
      type: "tool",
      timestamp: Date.now(),
      status: "completed",
      toolName: "read_world_entries",
      toolArgs: contractSamples.read_request,
      toolResult: partialReadSample,
    };

    // 1. 数据归一化解析
    const parsed = normalizeKnowledgeReadData(partialReadSample);
    expect(parsed).not.toBeNull();
    expect(parsed?.returned_count).toBe(1);
    expect(parsed?.partial_failure).toBe(true);
    expect(parsed?.budget_exhausted).toBe(false);
    expect(parsed?.items.length).toBe(2);

    const okItem = parsed!.items[0];
    expect(okItem.id).toBe("entry-1");
    expect(okItem.status).toBe("ok");
    expect(okItem.content).toBe("甲😀乙");
    expect(okItem.truncated).toBe(false);

    const failItem = parsed!.items[1];
    expect(failItem.id).toBe("entry-2");
    expect(failItem.status).toBe("version_conflict");
    expect(failItem.content).toBeNull();

    // 2. 详情栏明确标明成功项与异常项，而不是当作全失败
    const detail = getKnowledgeReadDetail(readMsg);
    expect(detail).toBeDefined();
    expect(detail).toContain("1 项成功");
    expect(detail).toContain("1 项异常");

    // 3. 消息壳层可见性状态验证：partial_failure 绝不触发 showErrorIndicator
    const normalizedResult = normalizeToolResult(partialReadSample, {
      status: "completed",
      toolName: "read_world_entries",
    });
    expect(normalizedResult.success).toBe(true);

    const visibility = resolveToolMessageVisibilityState({
      message: readMsg,
      contentMode: "expandable",
      hasContent: true,
      hasDetail: true,
      errorMessage: undefined,
    });
    expect(visibility.showErrorIndicator).toBe(false);
    expect(visibility.canExpand).toBe(true);
  });

  test("正文截断标记永不隐藏为“读取完成”", () => {
    const truncatedSample = {
      items: [
        {
          id: "entry-long",
          status: "ok",
          name: "燃魂真经",
          content_version: "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
          total_chars: 15534,
          start_offset: 0,
          end_offset: 4000,
          start_line: 1,
          start_line_offset: 0,
          content: "正文前4000字...",
          truncated: true,
          next_start_offset: 4000,
        },
      ],
      returned_count: 1,
      partial_failure: false,
      budget_exhausted: false,
    };

    const truncatedMsg: AgentMessage = {
      id: "msg-read-trunc",
      type: "tool",
      timestamp: Date.now(),
      status: "completed",
      toolName: "read_world_entries",
      toolResult: truncatedSample,
    };

    const parsed = normalizeKnowledgeReadData(truncatedSample);
    expect(parsed?.items[0].truncated).toBe(true);
    expect(parsed?.items[0].next_start_offset).toBe(4000);

    const detail = getKnowledgeReadDetail(truncatedMsg);
    expect(detail).toBeDefined();
    // 必须包含截断标记，不能仅声明读取完成
    expect(detail).toContain("截断");
    expect(detail).not.toBe("1 项读取完成");
  });

  test("预算耗尽状态正确识别并展示", () => {
    const budgetExhaustedSample = {
      items: [
        {
          id: "entry-1",
          status: "ok",
          name: "燃魂术",
          total_chars: 100,
          start_offset: 0,
          end_offset: 100,
          start_line: 1,
          start_line_offset: 0,
          content: "已读取内容",
          truncated: false,
          next_start_offset: null,
        },
        {
          id: "entry-2",
          status: "budget_exhausted",
          name: null,
          content_version: null,
          total_chars: null,
          start_offset: 0,
          end_offset: null,
          start_line: null,
          start_line_offset: null,
          content: null,
          truncated: false,
          next_start_offset: null,
        },
      ],
      returned_count: 1,
      partial_failure: true,
      budget_exhausted: true,
    };

    const budgetMsg: AgentMessage = {
      id: "msg-read-budget",
      type: "tool",
      timestamp: Date.now(),
      status: "completed",
      toolName: "read_world_entries",
      toolResult: budgetExhaustedSample,
    };

    const parsed = normalizeKnowledgeReadData(budgetExhaustedSample);
    expect(parsed?.budget_exhausted).toBe(true);
    expect(parsed?.items[1].status).toBe("budget_exhausted");

    const detail = getKnowledgeReadDetail(budgetMsg);
    expect(detail).toBeDefined();
    expect(detail).toContain("预算耗尽");
  });

  test("旧列表与旧单读工具兼容性与截断友好展示", () => {
    // 1. 旧列表分页 (list_world_entries / list_characters)
    const legacyListMsg: AgentMessage = {
      id: "msg-list-entries",
      type: "tool",
      timestamp: Date.now(),
      status: "completed",
      toolName: "list_world_entries",
      toolResult: {
        entries: Array.from({ length: 20 }, (_, i) => ({
          id: `entry-${i + 1}`,
          title: `条目 ${i + 1}`,
          uid: i + 1,
          order: i,
        })),
        returned_count: 20,
        total_count: 598,
        has_more: true,
        next_cursor: "cursor_token_abc",
      },
    };

    const listDetail = getLegacyListDetail(legacyListMsg, "world_entry");
    expect(listDetail).toBeDefined();
    expect(listDetail).toContain("20");
    expect(listDetail).toContain("598");
    expect(listDetail).toContain("还有更多");

    // 2. 旧单读截断 (read_world_entry / read_character)
    const legacyReadTruncMsg: AgentMessage = {
      id: "msg-read-entry-trunc",
      type: "tool",
      timestamp: Date.now(),
      status: "completed",
      toolName: "read_world_entry",
      toolArgs: { title: "燃魂真经" },
      toolResult: {
        title: "燃魂真经",
        content: "前4000字带行号正文...",
        id: "entry-long-id",
        total_chars: 15534,
        content_version: "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        truncated: true,
        next_start_offset: 4000,
        next_read: {
          tool: "read_world_entries",
          args: {
            items: [{ id: "entry-long-id", start_offset: 4000 }],
          },
        },
      },
    };

    const readDetail = getLegacySingleReadDetail(legacyReadTruncMsg, "world_entry");
    expect(readDetail).toBeDefined();
    expect(readDetail).toContain("燃魂真经");
    expect(readDetail).toContain("截断");
  });

  test("关键词高亮辅助函数正确分词与切片", () => {
    const text = "每次施展燃魂术，至少消耗十年寿命。";
    const terms = ["燃魂", "寿命"];

    const segments = highlightMatchedTerms(text, terms);
    expect(segments.length).toBeGreaterThan(1);

    // 重组必须严格等于原文本
    const reconstructed = segments.map((s) => s.text).join("");
    expect(reconstructed).toBe(text);

    // 命中段标记验证
    const matchSegments = segments.filter((s) => s.isMatch);
    expect(matchSegments.length).toBe(2);
    expect(matchSegments.map((s) => s.text)).toEqual(["燃魂", "寿命"]);
  });

  test("重载与持久化消息模拟：非规范/已序列化 JSON 正确恢复", () => {
    // 模拟从历史数据库加载反序列化后的消息体
    const serializedJson = JSON.stringify(contractSamples.read_partial_response);
    const parsed = normalizeKnowledgeReadData(serializedJson);
    expect(parsed).not.toBeNull();
    expect(parsed?.returned_count).toBe(1);
    expect(parsed?.items.length).toBe(2);
  });
});
