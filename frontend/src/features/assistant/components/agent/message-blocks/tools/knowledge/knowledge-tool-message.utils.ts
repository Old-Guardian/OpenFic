/**
 * Knowledge Retrieval Tool Message Utilities
 *
 * 世界书与角色检索及批量读取工具结果的解析与展示辅助函数。
 */

import i18n from "@/i18n";
import type { AgentMessage } from "@/lib/agent.types";

import {
  asNumber,
  asString,
  asStringArray,
  getCharacterList,
  getCharacterPayload,
  getStreamingData,
  getToolResultData,
  getWorldEntryList,
  getWorldEntryPayload,
  isRecord,
} from "../shared/tool-message-utils";
import type {
  KnowledgeKind,
  KnowledgeReadData,
  KnowledgeReadItemPayload,
  KnowledgeReadQueryPayload,
  KnowledgeReadTargetPayload,
  KnowledgeSearchData,
  KnowledgeSearchItemPayload,
  KnowledgeSearchQueryPayload,
  MatchedField,
  ReadStatus,
  SearchExcerptPayload,
} from "./knowledge-tool-message.types";

function parseMaybeJson(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function normalizeSearchExcerpt(value: unknown): SearchExcerptPayload | null {
  if (!isRecord(value)) return null;
  const text = asString(value.text);
  const startOffset = asNumber(value.start_offset);
  const endOffset = asNumber(value.end_offset);
  const lineStart = asNumber(value.line_start);
  const lineEnd = asNumber(value.line_end);

  if (
    text === undefined ||
    startOffset === undefined ||
    endOffset === undefined ||
    lineStart === undefined ||
    lineEnd === undefined
  ) {
    return null;
  }

  return {
    text,
    start_offset: startOffset,
    end_offset: endOffset,
    line_start: lineStart,
    line_end: lineEnd,
  };
}

function normalizeMatchedField(value: unknown): MatchedField | null {
  if (value === "name" || value === "alias" || value === "content") return value;
  return null;
}

function normalizeKnowledgeSearchItem(value: unknown): KnowledgeSearchItemPayload | null {
  if (!isRecord(value)) return null;
  const id = asString(value.id);
  const name = asString(value.name);
  if (!id || !name) return null;

  const kind: KnowledgeKind = value.kind === "character" ? "character" : "world_entry";
  const aliases = asStringArray(value.aliases);
  const matchedFields = Array.isArray(value.matched_fields)
    ? value.matched_fields
        .map(normalizeMatchedField)
        .filter((field): field is MatchedField => field !== null)
    : [];
  const matchedTerms = asStringArray(value.matched_terms);
  const excerpts = Array.isArray(value.excerpts)
    ? value.excerpts
        .map(normalizeSearchExcerpt)
        .filter((excerpt): excerpt is SearchExcerptPayload => excerpt !== null)
    : [];
  const contentVersion = asString(value.content_version) ?? "";
  const excerptsTruncated = Boolean(value.excerpts_truncated);

  return {
    id,
    kind,
    name,
    aliases,
    matched_fields: matchedFields.length > 0 ? matchedFields : ["name"],
    matched_terms: matchedTerms,
    excerpts,
    content_version: contentVersion,
    excerpts_truncated: excerptsTruncated,
  };
}

export function normalizeKnowledgeSearchData(raw: unknown): KnowledgeSearchData | null {
  const parsed = parseMaybeJson(raw);
  if (!isRecord(parsed)) return null;

  // Handle case where result might be wrapped in data
  const data = isRecord(parsed.data) ? parsed.data : parsed;
  if (!Array.isArray(data.items)) return null;

  const items = data.items
    .map(normalizeKnowledgeSearchItem)
    .filter((item): item is KnowledgeSearchItemPayload => item !== null);

  const returnedCount = asNumber(data.returned_count) ?? items.length;
  const totalMatches = asNumber(data.total_matches) ?? returnedCount;
  const hasMore = Boolean(data.has_more);
  const nextCursor = asString(data.next_cursor) ?? null;
  const matchScope = "literal_terms" as const;
  const reason = asString(data.reason) ?? null;

  return {
    items,
    returned_count: returnedCount,
    total_matches: totalMatches,
    has_more: hasMore,
    next_cursor: nextCursor,
    match_scope: matchScope,
    reason,
  };
}

function normalizeReadStatus(value: unknown): ReadStatus {
  if (
    value === "ok" ||
    value === "not_found" ||
    value === "disabled" ||
    value === "version_conflict" ||
    value === "invalid_range" ||
    value === "budget_exhausted"
  ) {
    return value;
  }
  return "ok";
}

function normalizeKnowledgeReadItem(value: unknown): KnowledgeReadItemPayload | null {
  if (!isRecord(value)) return null;
  const id = asString(value.id);
  if (!id) return null;

  const status = normalizeReadStatus(value.status);
  const name = asString(value.name) ?? null;
  const contentVersion = asString(value.content_version) ?? null;
  const totalChars = asNumber(value.total_chars) ?? null;
  const startOffset = asNumber(value.start_offset) ?? null;
  const endOffset = asNumber(value.end_offset) ?? null;
  const startLine = asNumber(value.start_line) ?? null;
  const startLineOffset = asNumber(value.start_line_offset) ?? null;
  const content = typeof value.content === "string" ? value.content : null;
  const truncated = Boolean(value.truncated);
  const nextStartOffset = asNumber(value.next_start_offset) ?? null;

  return {
    id,
    status,
    name,
    content_version: contentVersion,
    total_chars: totalChars,
    start_offset: startOffset,
    end_offset: endOffset,
    start_line: startLine,
    start_line_offset: startLineOffset,
    content,
    truncated,
    next_start_offset: nextStartOffset,
  };
}

export function normalizeKnowledgeReadData(raw: unknown): KnowledgeReadData | null {
  const parsed = parseMaybeJson(raw);
  if (!isRecord(parsed)) return null;

  const data = isRecord(parsed.data) ? parsed.data : parsed;
  if (!Array.isArray(data.items)) return null;

  const items = data.items
    .map(normalizeKnowledgeReadItem)
    .filter((item): item is KnowledgeReadItemPayload => item !== null);

  const returnedCount = asNumber(data.returned_count) ?? items.filter((i) => i.status === "ok").length;
  const partialFailure =
    typeof data.partial_failure === "boolean"
      ? data.partial_failure
      : items.some((i) => i.status !== "ok");
  const budgetExhausted =
    typeof data.budget_exhausted === "boolean"
      ? data.budget_exhausted
      : items.some((i) => i.status === "budget_exhausted");

  return {
    items,
    returned_count: returnedCount,
    partial_failure: partialFailure,
    budget_exhausted: budgetExhausted,
  };
}

export function getKnowledgeSearchQuery(message: AgentMessage): KnowledgeSearchQueryPayload {
  const args = getStreamingData(message);
  return {
    query: asString(args.query),
    match: args.match === "any" ? "any" : "all",
    limit: asNumber(args.limit),
    cursor: asString(args.cursor) ?? null,
  };
}

export function getKnowledgeReadQuery(message: AgentMessage): KnowledgeReadQueryPayload {
  const args = getStreamingData(message);
  const rawItems = Array.isArray(args.items) ? args.items : [];
  const items: KnowledgeReadTargetPayload[] = rawItems
    .filter(isRecord)
    .map((item) => ({
      id: asString(item.id) ?? "",
      start_offset: asNumber(item.start_offset),
      expected_version: asString(item.expected_version),
    }))
    .filter((item) => item.id);

  return {
    items,
    max_chars_per_item: asNumber(args.max_chars_per_item),
  };
}

export function getKnowledgeSearchDetail(message: AgentMessage): string | undefined {
  const resultData = normalizeKnowledgeSearchData(
    getToolResultData(message) ?? getStreamingData(message),
  );
  const queryInfo = getKnowledgeSearchQuery(message);
  const query = queryInfo.query;

  if (resultData) {
    if (resultData.reason === "no_world_book") {
      return i18n.t("assistant.tools.noWorldBookBound");
    }
    const matchCountText = i18n.t("assistant.tools.matchCount", { count: resultData.total_matches });
    const queryPrefix = query ? `“${query}” · ` : "";
    if (resultData.has_more) {
      return `${queryPrefix}${matchCountText} (${i18n.t("assistant.tools.hasMoreSearchResults", { total: resultData.total_matches }).split("，")[0]})`;
    }
    return `${queryPrefix}${matchCountText}`;
  }

  return query ? `“${query}”` : undefined;
}

export function getKnowledgeReadDetail(message: AgentMessage): string | undefined {
  const resultData = normalizeKnowledgeReadData(
    getToolResultData(message) ?? getStreamingData(message),
  );

  if (resultData) {
    const okCount = resultData.items.filter((i) => i.status === "ok").length;
    const failedCount = resultData.items.length - okCount;
    const hasTruncated = resultData.items.some((i) => i.status === "ok" && i.truncated);

    if (resultData.budget_exhausted) {
      return okCount > 0
        ? `${i18n.t("assistant.tools.batchReadSuccess", { count: okCount })} · ${i18n.t("assistant.tools.batchReadBudgetExhausted")}`
        : i18n.t("assistant.tools.batchReadBudgetExhausted");
    }

    if (resultData.partial_failure && failedCount > 0) {
      return `${okCount} 项成功 · ${failedCount} 项异常`;
    }

    if (hasTruncated) {
      return `${i18n.t("assistant.tools.batchReadSuccess", { count: okCount })} · ${i18n.t("assistant.tools.truncatedNotice")}`;
    }

    return i18n.t("assistant.tools.batchReadSuccess", { count: okCount });
  }

  const query = getKnowledgeReadQuery(message);
  if (query.items && query.items.length > 0) {
    return `${query.items.length} 项读取中`;
  }

  return undefined;
}

export interface HighlightSegment {
  text: string;
  isMatch: boolean;
}

export function highlightMatchedTerms(text: string, terms: string[]): HighlightSegment[] {
  if (!text || terms.length === 0) {
    return [{ text, isMatch: false }];
  }

  // Sort terms by length descending to match longest terms first
  const cleanTerms = Array.from(new Set(terms.filter((t) => t.trim().length > 0))).sort(
    (a, b) => b.length - a.length,
  );
  if (cleanTerms.length === 0) return [{ text, isMatch: false }];

  // Find intervals of matched terms
  const intervals: Array<{ start: number; end: number }> = [];
  const lowerText = text.toLowerCase();

  for (const term of cleanTerms) {
    const lowerTerm = term.toLowerCase();
    let index = lowerText.indexOf(lowerTerm);
    while (index !== -1) {
      intervals.push({ start: index, end: index + term.length });
      index = lowerText.indexOf(lowerTerm, index + 1);
    }
  }

  if (intervals.length === 0) {
    return [{ text, isMatch: false }];
  }

  // Merge overlapping intervals
  intervals.sort((a, b) => a.start - b.start || b.end - a.end);
  const merged: Array<{ start: number; end: number }> = [];
  for (const iv of intervals) {
    const last = merged[merged.length - 1];
    if (!last || iv.start >= last.end) {
      merged.push({ ...iv });
    } else if (iv.end > last.end) {
      last.end = iv.end;
    }
  }

  // Build segments
  const segments: HighlightSegment[] = [];
  let cursor = 0;
  for (const interval of merged) {
    if (interval.start > cursor) {
      segments.push({ text: text.slice(cursor, interval.start), isMatch: false });
    }
    segments.push({ text: text.slice(interval.start, interval.end), isMatch: true });
    cursor = interval.end;
  }
  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor), isMatch: false });
  }

  return segments;
}

export function getLegacyListDetail(
  message: AgentMessage,
  kind: "character" | "world_entry",
): string | undefined {
  const items = kind === "character" ? getCharacterList(message) : getWorldEntryList(message);
  const data = getToolResultData(message);
  const totalCount =
    isRecord(data) && typeof data.total_count === "number" ? data.total_count : undefined;
  const hasMore = isRecord(data) && Boolean(data.has_more);
  if (items.length === 0) return undefined;
  if (totalCount !== undefined) {
    return hasMore
      ? i18n.t("assistant.tools.pagedItemCountWithMore", {
          count: items.length,
          total: totalCount,
        })
      : i18n.t("assistant.tools.pagedItemCount", {
          count: items.length,
          total: totalCount,
        });
  }
  return kind === "character"
    ? i18n.t("assistant.tools.characterCount", { count: items.length })
    : i18n.t("assistant.tools.worldEntryCount", { count: items.length });
}

export function getLegacySingleReadDetail(
  message: AgentMessage,
  kind: "character" | "world_entry",
): string | undefined {
  const name =
    kind === "character"
      ? getCharacterPayload(message).name
      : getWorldEntryPayload(message).title;
  const data = getToolResultData(message);
  const isTruncated = isRecord(data) && data.truncated === true;
  if (!name) return undefined;
  return isTruncated
    ? `${name} · ${i18n.t("assistant.tools.truncatedNotice")}`
    : name;
}
