import { Theme } from "@radix-ui/themes";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";

import { ProviderIdSelect } from "@/components/provider-id-select";
import { LabeledSelect } from "@/components/select";
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

const mode = new URLSearchParams(location.search).get("mode");
const isStreaming = mode === "streaming";
const startsWithMessages = mode === "older" || isStreaming;
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
if (isStreaming) {
  initialMessages.push({
    id: "streaming-output",
    type: "agent_output",
    role: "assistant",
    display: "list",
    status: "running",
    timestamp: Date.UTC(2026, 9, 12, 1),
    content: "Streaming output",
    isStreaming: true,
  });
}

function MobileSelectionFixture() {
  const [choice, setChoice] = useState("alpha");
  const [provider, setProvider] = useState("");
  return (
    <>
      <LabeledSelect
        label="Mobile choice"
        value={choice}
        onChange={setChoice}
        options={[
          { value: "alpha", label: "Alpha" },
          { value: "beta", label: "Beta" },
        ]}
      />
      <ProviderIdSelect
        placeholder="Choose provider"
        value={provider}
        onChange={setProvider}
        providers={["google-vertex", "google-vertex-anthropic"].map((providerType) => ({
          providerType,
          displayName: "Vertex AI",
          defaultUrl: null,
          api: null,
          iconPath: null,
          modelsDevProviderId: null,
          supportedTaskTypes: [],
          modelCounts: {},
        }))}
      />
      <output data-testid="selection-state">{JSON.stringify({ choice, provider })}</output>
    </>
  );
}

export function Fixture() {
  const [sessionId, setSessionId] = useState("A");
  const [messages, setMessages] = useState(initialMessages);
  const [historyHost, setHistoryHost] = useState<HTMLDivElement | null>(null);
  const [atBottom, setAtBottom] = useState(true);
  const scrollToBottomRef = useRef<(() => void) | null>(null);
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
    resetPagination("A", {
      messagesCursor: isStreaming ? null : 300,
      messagesHasMore: !isStreaming,
    });
  }, [resetPagination]);
  return (
    <>
      {isStreaming && (
        <>
          <button
            onClick={() =>
              setMessages((current) =>
                current.map((message) =>
                  message.id === "streaming-output"
                    ? {
                        ...message,
                        content: `${message.content}\n\n${"Streaming paragraph\n\n".repeat(30)}`,
                      }
                    : message,
                ),
              )
            }
          >
            Append output
          </button>
          <button onClick={() => scrollToBottomRef.current?.()}>Return to bottom</button>
          <output data-testid="scroll-state">{String(atBottom)}</output>
        </>
      )}
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
          isRunning={isStreaming}
          isRollbacking={false}
          status={isStreaming ? "running" : "idle"}
          currentStage=""
          scrollToBottomKey={sessionId}
          onRollback={async () => null}
          historyStatusHost={historyHost}
          historyGeneration={generation}
          onLoadEarlier={loadEarlier}
          messagesHasMore={messagesHasMore}
          isLoadingEarlier={isLoadingEarlier}
          hasEarlierError={hasEarlierError}
          onAtBottomChange={setAtBottom}
          scrollToBottomFnRef={scrollToBottomRef}
        />
      </div>
    </>
  );
}

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const shell = {
  isMobile: window.matchMedia("(max-width: 767px)").matches,
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
        <Theme>{mode === "mobile-selection" ? <MobileSelectionFixture /> : <Fixture />}</Theme>
      </AppShellContext.Provider>
    </MemoryRouter>
  </QueryClientProvider>,
);
