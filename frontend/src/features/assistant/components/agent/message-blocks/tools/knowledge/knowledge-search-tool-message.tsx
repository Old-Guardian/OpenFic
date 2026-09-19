import { Badge, Box, Flex, Text } from "@radix-ui/themes";
import { AlertCircle, AlertTriangle, Info } from "lucide-react";

import i18n from "@/i18n";
import type { AgentMessage } from "@/lib/agent.types";

import { ToolBody, ToolNotice } from "../shared/tool-message-shared";
import { getStreamingData, getToolResultData, getToolResultMessage } from "../shared/tool-message-utils";
import type { KnowledgeKind, MatchedField, SearchExcerptPayload } from "./knowledge-tool-message.types";
import {
  getKnowledgeSearchQuery,
  highlightMatchedTerms,
  normalizeKnowledgeSearchData,
} from "./knowledge-tool-message.utils";

import "./knowledge-tool-message.css";

interface KnowledgeSearchToolMessageProps {
  message: AgentMessage;
  kind: KnowledgeKind;
}

function getFieldLabel(field: MatchedField): string {
  switch (field) {
    case "name":
      return i18n.t("assistant.tools.fieldName");
    case "alias":
      return i18n.t("assistant.tools.fieldAlias");
    case "content":
      return i18n.t("assistant.tools.fieldContent");
  }
}

function ExcerptItem({
  excerpt,
  terms,
}: {
  excerpt: SearchExcerptPayload;
  terms: string[];
}) {
  const lineLabel =
    excerpt.line_end > excerpt.line_start
      ? i18n.t("assistant.tools.excerptLineRange", {
          start: excerpt.line_start,
          end: excerpt.line_end,
        })
      : i18n.t("assistant.tools.excerptLine", { line: excerpt.line_start });

  const offsetLabel = i18n.t("assistant.tools.charOffsetRange", {
    start: excerpt.start_offset,
    end: excerpt.end_offset,
  });

  const segments = highlightMatchedTerms(excerpt.text, terms);

  return (
    <div className="agent-knowledge-excerpt-box">
      <div className="agent-knowledge-excerpt-meta">
        <span>{lineLabel}</span>
        <span> · </span>
        <span>{offsetLabel}</span>
      </div>
      <div className="agent-knowledge-excerpt-text">
        {segments.map((segment, index) =>
          segment.isMatch ? (
            <mark
              key={index}
              className="agent-knowledge-highlight"
            >
              {segment.text}
            </mark>
          ) : (
            <span key={index}>{segment.text}</span>
          ),
        )}
      </div>
    </div>
  );
}

export function KnowledgeSearchToolMessage({ message, kind }: KnowledgeSearchToolMessageProps) {
  const rawData = getToolResultData(message) ?? getStreamingData(message);
  const data = normalizeKnowledgeSearchData(rawData);
  const queryInfo = getKnowledgeSearchQuery(message);
  const resultMessage = getToolResultMessage(message);

  if (!data) {
    return (
      <ToolBody>
        <ToolNotice title={i18n.t("assistant.tools.noKnowledgeSearchResults")}>
          {resultMessage ?? i18n.t("assistant.tools.noKnowledgeSearchResultsHint")}
        </ToolNotice>
      </ToolBody>
    );
  }

  if (data.reason === "no_world_book") {
    return (
      <ToolBody>
        <div className="agent-knowledge-panel rt-Box">
          <div className="agent-knowledge-alert agent-knowledge-alert--warning">
            <AlertCircle size={15} />
            <div>
              <Text weight="medium">{i18n.t("assistant.tools.noWorldBookBound")}</Text>
              <Text
                size="1"
                as="p"
                color="gray"
              >
                {resultMessage ?? i18n.t("assistant.tools.noWorldBookBound")}
              </Text>
            </div>
          </div>
        </div>
      </ToolBody>
    );
  }

  const queryText = queryInfo.query;
  const matchModeLabel =
    queryInfo.match === "any"
      ? i18n.t("assistant.tools.matchAny")
      : i18n.t("assistant.tools.matchAll");

  return (
    <ToolBody>
      <Box
        className="agent-knowledge-panel"
        data-kind={kind}
      >
        <div className="agent-knowledge-header-bar">
          <div className="agent-knowledge-query-tags">
            {queryText ? (
              <span className="agent-knowledge-tag agent-knowledge-tag--accent">
                {i18n.t("assistant.tools.searchQuery")}: {queryText}
              </span>
            ) : null}
            <span className="agent-knowledge-tag agent-knowledge-tag--muted">
              {matchModeLabel}
            </span>
          </div>
          <Text
            size="1"
            color="gray"
          >
            {i18n.t("assistant.tools.searchMatchesTotal", {
              total: data.total_matches,
              count: data.items.length,
            })}
          </Text>
        </div>

        {data.items.length === 0 ? (
          <div className="agent-knowledge-alert agent-knowledge-alert--info">
            <Info size={15} />
            <div>
              <Text weight="medium">{i18n.t("assistant.tools.noKnowledgeSearchResults")}</Text>
              <Text
                size="1"
                as="p"
                color="gray"
              >
                {i18n.t("assistant.tools.noKnowledgeSearchResultsHint")}
              </Text>
            </div>
          </div>
        ) : (
          <ul className="agent-knowledge-results-list">
            {data.items.map((item) => (
              <li
                key={item.id}
                className="agent-knowledge-card"
              >
                <div className="agent-knowledge-card-header">
                  <div className="agent-knowledge-card-title-group">
                    <span className="agent-knowledge-card-title">{item.name}</span>
                    <span className="agent-knowledge-card-id">{item.id}</span>
                  </div>
                  <Flex
                    gap="1"
                    align="center"
                  >
                    {item.matched_fields.map((field) => (
                      <Badge
                        key={field}
                        size="1"
                        variant="surface"
                        color={field === "content" ? "indigo" : "cyan"}
                      >
                        {getFieldLabel(field)}
                      </Badge>
                    ))}
                  </Flex>
                </div>

                {item.aliases.length > 0 ? (
                  <div className="agent-knowledge-aliases-list">
                    <span>{i18n.t("assistant.tools.aliasesLabel")}:</span>
                    {item.aliases.map((alias) => (
                      <span
                        key={alias}
                        className="agent-knowledge-tag agent-knowledge-tag--muted"
                      >
                        {alias}
                      </span>
                    ))}
                  </div>
                ) : null}

                {item.excerpts.length > 0 ? (
                  <div className="agent-knowledge-excerpts-list">
                    {item.excerpts.map((excerpt, idx) => (
                      <ExcerptItem
                        key={idx}
                        excerpt={excerpt}
                        terms={item.matched_terms}
                      />
                    ))}
                    {item.excerpts_truncated ? (
                      <div className="agent-knowledge-alert agent-knowledge-alert--warning">
                        <AlertTriangle size={13} />
                        <span>{i18n.t("assistant.tools.excerptsTruncatedNotice")}</span>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}

        {data.has_more ? (
          <div className="agent-knowledge-footer-pagination">
            <Info size={14} />
            <span>
              {i18n.t("assistant.tools.hasMoreSearchResults", { total: data.total_matches })}
            </span>
          </div>
        ) : null}
      </Box>
    </ToolBody>
  );
}
