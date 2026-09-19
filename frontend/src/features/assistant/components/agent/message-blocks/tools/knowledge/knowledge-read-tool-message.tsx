import { Badge, Box, IconButton, Text, Tooltip } from "@radix-ui/themes";
import { AlertCircle, AlertTriangle, Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { toast } from "@/components";
import i18n from "@/i18n";
import type { AgentMessage } from "@/lib/agent.types";

import { ToolBody, ToolNotice } from "../shared/tool-message-shared";
import { getStreamingData, getToolResultData, getToolResultMessage } from "../shared/tool-message-utils";
import type { KnowledgeKind, KnowledgeReadItemPayload } from "./knowledge-tool-message.types";
import { normalizeKnowledgeReadData } from "./knowledge-tool-message.utils";

import "./knowledge-tool-message.css";

interface KnowledgeReadToolMessageProps {
  message: AgentMessage;
  kind: KnowledgeKind;
}

const COPY_FEEDBACK_MS = 1200;

function ReadItemStatusBadge({ item }: { item: KnowledgeReadItemPayload }) {
  if (item.status === "ok") {
    if (item.truncated) {
      return (
        <Badge
          size="1"
          color="amber"
          variant="surface"
        >
          <AlertTriangle size={11} />
          {i18n.t("assistant.tools.readStatus.truncated")}
        </Badge>
      );
    }
    return (
      <Badge
        size="1"
        color="green"
        variant="surface"
      >
        <Check size={11} />
        {i18n.t("assistant.tools.readStatus.ok")}
      </Badge>
    );
  }

  switch (item.status) {
    case "not_found":
      return (
        <Badge
          size="1"
          color="gray"
          variant="surface"
        >
          {i18n.t("assistant.tools.readStatus.notFound")}
        </Badge>
      );
    case "disabled":
      return (
        <Badge
          size="1"
          color="orange"
          variant="surface"
        >
          {i18n.t("assistant.tools.readStatus.disabled")}
        </Badge>
      );
    case "version_conflict":
      return (
        <Badge
          size="1"
          color="red"
          variant="surface"
        >
          {i18n.t("assistant.tools.readStatus.versionConflict")}
        </Badge>
      );
    case "invalid_range":
      return (
        <Badge
          size="1"
          color="red"
          variant="surface"
        >
          {i18n.t("assistant.tools.readStatus.invalidRange")}
        </Badge>
      );
    case "budget_exhausted":
      return (
        <Badge
          size="1"
          color="amber"
          variant="surface"
        >
          {i18n.t("assistant.tools.readStatus.budgetExhausted")}
        </Badge>
      );
  }
}

function ReadItemCard({ item }: { item: KnowledgeReadItemPayload }) {
  const [isCopied, setIsCopied] = useState(false);
  const copyTimerRef = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
    },
    [],
  );

  const handleCopy = async () => {
    if (!item.content) return;
    try {
      await navigator.clipboard.writeText(item.content);
      setIsCopied(true);
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
      copyTimerRef.current = window.setTimeout(() => {
        setIsCopied(false);
        copyTimerRef.current = null;
      }, COPY_FEEDBACK_MS);
      toast.success(i18n.t("common.copied"));
    } catch {
      toast.error(i18n.t("assistant.copyFailed"));
    }
  };

  const displayName = item.name || item.id;

  return (
    <li className="agent-knowledge-card">
      <div className="agent-knowledge-card-header">
        <div className="agent-knowledge-card-title-group">
          <span className="agent-knowledge-card-title">{displayName}</span>
          <span className="agent-knowledge-card-id">{item.id}</span>
        </div>
        <ReadItemStatusBadge item={item} />
      </div>

      {item.status === "ok" ? (
        <div className="agent-knowledge-content-container">
          <div className="agent-knowledge-content-meta">
            <span>
              {i18n.t("assistant.tools.readRangeMeta", {
                start: item.start_offset ?? 0,
                end: item.end_offset ?? 0,
                total: item.total_chars ?? 0,
                line: item.start_line ?? 1,
              })}
            </span>
            <Tooltip content={isCopied ? i18n.t("common.copied") : i18n.t("common.copy")}>
              <IconButton
                size="1"
                variant="ghost"
                color={isCopied ? "green" : "gray"}
                onClick={handleCopy}
                aria-label={i18n.t("common.copy")}
              >
                {isCopied ? <Check size={12} /> : <Copy size={12} />}
              </IconButton>
            </Tooltip>
          </div>

          <div className="agent-knowledge-content-box">
            {item.content || <Text color="gray">{i18n.t("assistant.tools.noContentToDisplay")}</Text>}
          </div>

          {item.truncated ? (
            <div className="agent-knowledge-alert agent-knowledge-alert--warning">
              <AlertTriangle size={14} />
              <div>
                <Text
                  weight="medium"
                  size="1"
                >
                  {i18n.t("assistant.tools.truncatedNotice")}
                </Text>
                <Text
                  size="1"
                  as="p"
                >
                  {i18n.t("assistant.tools.truncatedContinuationHint", {
                    end: item.end_offset ?? 0,
                    total: item.total_chars ?? 0,
                    nextOffset: item.next_start_offset ?? item.end_offset,
                    version: item.content_version ?? "",
                  })}
                </Text>
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="agent-knowledge-alert agent-knowledge-alert--error">
          <AlertCircle size={14} />
          <Text size="1">
            {item.status === "not_found" && i18n.t("assistant.tools.readStatusDesc.notFound")}
            {item.status === "disabled" && i18n.t("assistant.tools.readStatusDesc.disabled")}
            {item.status === "version_conflict" &&
              i18n.t("assistant.tools.readStatusDesc.versionConflict")}
            {item.status === "invalid_range" &&
              i18n.t("assistant.tools.readStatusDesc.invalidRange", {
                total: item.total_chars ?? 0,
              })}
            {item.status === "budget_exhausted" &&
              i18n.t("assistant.tools.readStatusDesc.budgetExhausted")}
          </Text>
        </div>
      )}
    </li>
  );
}

export function KnowledgeReadToolMessage({ message, kind }: KnowledgeReadToolMessageProps) {
  const rawData = getToolResultData(message) ?? getStreamingData(message);
  const data = normalizeKnowledgeReadData(rawData);
  const resultMessage = getToolResultMessage(message);

  if (!data || data.items.length === 0) {
    return (
      <ToolBody>
        <ToolNotice title={i18n.t("assistant.tools.noContentToDisplay")}>
          {resultMessage ?? i18n.t("assistant.tools.noContentToDisplay")}
        </ToolNotice>
      </ToolBody>
    );
  }

  const okCount = data.items.filter((i) => i.status === "ok").length;
  const failedCount = data.items.length - okCount;

  return (
    <ToolBody>
      <Box
        className="agent-knowledge-panel"
        data-kind={kind}
      >
        <div className="agent-knowledge-header-bar">
          <Text
            size="2"
            weight="medium"
          >
            {data.partial_failure
              ? i18n.t("assistant.tools.batchReadPartialFailure", {
                  ok: okCount,
                  failed: failedCount,
                })
              : i18n.t("assistant.tools.batchReadSuccess", { count: okCount })}
          </Text>
          <Text
            size="1"
            color="gray"
          >
            {data.items.length} {i18n.t("assistant.tools.affectedCount", { count: data.items.length })}
          </Text>
        </div>

        {data.partial_failure ? (
          <div className="agent-knowledge-alert agent-knowledge-alert--warning">
            <AlertTriangle size={15} />
            <div>
              <Text
                weight="medium"
                size="1"
              >
                {i18n.t("assistant.tools.partialFailureNotice")}
              </Text>
            </div>
          </div>
        ) : null}

        {data.budget_exhausted ? (
          <div className="agent-knowledge-alert agent-knowledge-alert--warning">
            <AlertCircle size={15} />
            <div>
              <Text
                weight="medium"
                size="1"
              >
                {i18n.t("assistant.tools.budgetExhaustedNotice")}
              </Text>
            </div>
          </div>
        ) : null}

        <ul className="agent-knowledge-results-list">
          {data.items.map((item) => (
            <ReadItemCard
              key={item.id}
              item={item}
            />
          ))}
        </ul>
      </Box>
    </ToolBody>
  );
}
