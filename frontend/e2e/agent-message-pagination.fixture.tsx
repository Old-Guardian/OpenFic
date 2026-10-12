import { Theme } from "@radix-ui/themes";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";

import { AppShellContext } from "@/features/app-shell/components/app-shell-context";
import { AgentMessages } from "@/features/assistant/components/agent/agent-messages";
import {
  prependAgentMessages,
  useAgentMessagePagination,
  type AgentMessagePage,
} from "@/features/assistant/hooks/use-agent-message-pagination";
import type { AgentMessage } from "@/lib/agent.types";
import "@/i18n";

import "@/styles/index.css";
import "@/features/assistant/components/assistant-sidebar.css";

const startsWithMessages = new URLSearchParams(location.search).get("mode") === "older";
const initialMessages: AgentMessage[] = startsWithMessages
  ? Array.from({ length: 12 }, (_, index) => ({
      id: `current-${index}`,
      type: "user_request",
      role: "user",
      display: "list",
      status: "completed",
      timestamp: Date.UTC(2026, 9, 12, 0, index),
      content: `Current message ${index}`,
    }))
  : [];

export function Fixture() {
  const [sessionId, setSessionId] = useState("A");
  const [messages, setMessages] = useState(initialMessages);
  const [historyHost, setHistoryHost] = useState<HTMLDivElement | null>(null);
  const fetchPage = useCallback(async (key: string, cursor: number): Promise<AgentMessagePage> => {
    const response = await fetch(`/__pagination_page/${key}?cursor=${cursor}`);
    if (!response.ok) throw new Error("Page request failed");
    return response.json();
  }, []);
  const applyPage = useCallback((older: AgentMessage[]) => {
    setMessages((current) => prependAgentMessages(current, older));
  }, []);
  const {
    resetPagination,
    loadEarlier,
    messagesHasMore,
    isLoadingEarlier,
    hasEarlierError,
    generation,
  } = useAgentMessagePagination(fetchPage, applyPage);
  useEffect(() => {
    resetPagination("A", { messagesCursor: 300, messagesHasMore: true });
  }, [resetPagination]);
  return (
    <>
      <button
        onClick={() => {
          setSessionId("B");
          setMessages([]);
          resetPagination("B", { messagesCursor: 1000, messagesHasMore: true });
        }}
      >
        Switch session
      </button>
      <output data-testid="pagination-state">
        {JSON.stringify({ sessionId, isLoadingEarlier, messagesHasMore, hasEarlierError })}
      </output>
      <div ref={setHistoryHost} />
      <div
        className="ai-sidebar-messages"
        style={{ height: 400, overflowY: "auto" }}
      >
        <AgentMessages
          messages={messages}
          isRunning={false}
          isRollbacking={false}
          status="idle"
          currentStage=""
          scrollToBottomKey={sessionId}
          onRollback={async () => null}
          historyStatusHost={historyHost}
          historyGeneration={generation}
          onLoadEarlier={loadEarlier}
          messagesHasMore={messagesHasMore}
          isLoadingEarlier={isLoadingEarlier}
          hasEarlierError={hasEarlierError}
        />
      </div>
    </>
  );
}

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const shell = {
  isMobile: false,
  isSidebarOpen: false,
  isSettingsOpen: false,
  isAssistantSidebarOpen: false,
  openSidebar() {},
  closeSidebar() {},
  toggleSidebar() {},
  openSettings() {},
  closeSettings() {},
  appendToAssistant() {},
  openAssistantSidebar() {},
  closeAssistantSidebar() {},
  registerAssistantSidebarHost() {},
  clearAssistantSidebarHost() {},
};
createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={client}>
    <MemoryRouter>
      <AppShellContext.Provider value={shell}>
        <Theme>
          <Fixture />
        </Theme>
      </AppShellContext.Provider>
    </MemoryRouter>
  </QueryClientProvider>,
);
