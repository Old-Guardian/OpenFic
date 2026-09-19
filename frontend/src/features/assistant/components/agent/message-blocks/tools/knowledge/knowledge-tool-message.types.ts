/**
 * Knowledge Retrieval Tool Message Types
 *
 * 世界书与角色检索及批量读取工具消息的类型定义。
 */

export interface SearchExcerptPayload {
  text: string;
  start_offset: number;
  end_offset: number;
  line_start: number;
  line_end: number;
}

export type KnowledgeKind = "world_entry" | "character";
export type MatchedField = "name" | "alias" | "content";

export interface KnowledgeSearchItemPayload {
  id: string;
  kind: KnowledgeKind;
  name: string;
  aliases: string[];
  matched_fields: MatchedField[];
  matched_terms: string[];
  excerpts: SearchExcerptPayload[];
  content_version: string;
  excerpts_truncated: boolean;
}

export interface KnowledgeSearchData {
  items: KnowledgeSearchItemPayload[];
  returned_count: number;
  total_matches: number;
  has_more: boolean;
  next_cursor: string | null;
  match_scope: "literal_terms";
  reason?: string | null;
}

export type ReadStatus =
  | "ok"
  | "not_found"
  | "disabled"
  | "version_conflict"
  | "invalid_range"
  | "budget_exhausted";

export interface KnowledgeReadItemPayload {
  id: string;
  status: ReadStatus;
  name?: string | null;
  content_version?: string | null;
  total_chars?: number | null;
  start_offset?: number | null;
  end_offset?: number | null;
  start_line?: number | null;
  start_line_offset?: number | null;
  content?: string | null;
  truncated?: boolean;
  next_start_offset?: number | null;
}

export interface KnowledgeReadData {
  items: KnowledgeReadItemPayload[];
  returned_count: number;
  partial_failure: boolean;
  budget_exhausted: boolean;
}

export interface KnowledgeSearchQueryPayload {
  query?: string;
  match?: "all" | "any";
  limit?: number;
  cursor?: string | null;
}

export interface KnowledgeReadTargetPayload {
  id: string;
  start_offset?: number;
  expected_version?: string | null;
}

export interface KnowledgeReadQueryPayload {
  items?: KnowledgeReadTargetPayload[];
  max_chars_per_item?: number;
}
