import { Theme } from "@radix-ui/themes";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { MarkdownEditor } from "../src/components/markdown-editor";
import { CharacterEditor } from "../src/features/characters/components/character-editor";
import { useEditorSessionStore } from "../src/features/editor-session/lib/editor-session-store";
import { EntryEditor } from "../src/features/world-info/components/entry-editor";
import { useAutoSave, type SaveReason, type SaveResult } from "../src/hooks/use-auto-save";
import type { Character } from "../src/lib/character.types";
import { countTokens, preloadTiktokenEncoding } from "../src/lib/tiktoken-utils";
import type { WorldInfoEntry } from "../src/lib/world-info.types";
import fixture from "./fixtures/character-save-baseline.json" with { type: "json" };
import "../src/i18n";

type SaveMode = "immediate" | "deferred" | "failed";
type SaveCall = { reason: SaveReason; revision: number; content: string };
type PendingSave = { resolve: () => void; reject: (error: Error) => void };

interface HarnessApi {
  snapshot: () => {
    contentChanges: number;
    saveCalls: SaveCall[];
    content: string;
    dirty: boolean;
    revision: number;
    tokenCount: number;
  };
  setSaveMode: (mode: SaveMode) => void;
  resolveSave: () => void;
  rejectSave: () => void;
  setLocked: (locked: boolean) => void;
}

interface EntityHarnessApi {
  snapshot: () => { dirty: boolean; saveCalls: number };
  remount: () => void;
  discard: () => Promise<void>;
  setLocked: (locked: boolean) => void;
}

declare global {
  interface Window {
    __editorSaveHarness?: HarnessApi;
    __entityRegressionHarness?: EntityHarnessApi;
  }
}

const initialContent =
  new URLSearchParams(window.location.search).get("case") === "plain"
    ? "Simple character description"
    : fixture.description;

/* oxlint-disable react-refresh/only-export-components -- isolated Vite test entry */
function RegressionHarness() {
  const [content, setContent] = useState(initialContent);
  const [isLocked, setLocked] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);
  const [dirtyRevision, setDirtyRevision] = useState(0);
  const contentRef = useRef(initialContent);
  const dirtyRef = useRef(false);
  const revisionRef = useRef(0);
  const contentChangesRef = useRef(0);
  const saveCallsRef = useRef<SaveCall[]>([]);
  const saveModeRef = useRef<SaveMode>("immediate");
  const pendingSaveRef = useRef<PendingSave | null>(null);

  const saveAdapter = useCallback(
    async (reason: SaveReason, revision: number): Promise<SaveResult> => {
      saveCallsRef.current.push({ reason, revision, content: contentRef.current });
      if (saveModeRef.current === "failed") {
        return { status: "failed", error: new Error("Controlled save failure") };
      }
      if (saveModeRef.current === "deferred") {
        try {
          await new Promise<void>((resolve, reject) => {
            pendingSaveRef.current = { resolve, reject };
          });
        } catch (error) {
          return { status: "failed", error };
        } finally {
          pendingSaveRef.current = null;
        }
      }
      if (revisionRef.current === revision) {
        dirtyRef.current = false;
        setHasChanges(false);
      }
      return { status: "saved", savedRevision: revision };
    },
    [],
  );

  const autoSave = useAutoSave({
    documentKey: "character:isolated-fixture",
    enabled: false,
    delayMs: 1500,
    dirtyRevision,
    hasChanges,
    save: saveAdapter,
  });

  const handleContentChange = useCallback((nextContent: string) => {
    contentChangesRef.current += 1;
    contentRef.current = nextContent;
    dirtyRef.current = true;
    revisionRef.current += 1;
    setContent(nextContent);
    setHasChanges(true);
    setDirtyRevision(revisionRef.current);
  }, []);

  useEffect(() => {
    window.__editorSaveHarness = {
      snapshot: () => ({
        contentChanges: contentChangesRef.current,
        saveCalls: [...saveCallsRef.current],
        content: contentRef.current,
        dirty: dirtyRef.current,
        revision: revisionRef.current,
        tokenCount: countTokens(contentRef.current),
      }),
      setSaveMode: (mode) => {
        saveModeRef.current = mode;
      },
      resolveSave: () => pendingSaveRef.current?.resolve(),
      rejectSave: () => pendingSaveRef.current?.reject(new Error("Controlled save failure")),
      setLocked,
    };
    return () => {
      delete window.__editorSaveHarness;
    };
  }, []);

  return (
    <Theme>
      <div
        data-testid="regression-harness"
        style={{ height: 700 }}
      >
        <MarkdownEditor
          title={fixture.name}
          onTitleChange={() => {}}
          content={content}
          onContentChange={handleContentChange}
          onSave={() => {
            void autoSave.save("manual");
          }}
          hasChanges={hasChanges}
          isSaving={autoSave.isSaving}
          isLocked={isLocked}
          wordCount={countTokens(content)}
          wordCountLabel="Token"
        />
      </div>
    </Theme>
  );
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: Infinity, retry: false } },
});
queryClient.setQueryData(["settings"], { editorAutoSave: false });

const fixtureCharacter: Character = {
  id: "isolated-fixture",
  projectId: "isolated-project",
  name: fixture.name,
  description: fixture.description,
  aliases: fixture.aliases,
  imageUrl: null,
  isFavorited: false,
  createdAt: "2026-09-23T00:00:00Z",
  updatedAt: "2026-09-23T00:00:00Z",
};

const fixtureEntry: WorldInfoEntry = {
  id: "isolated-fixture",
  worldInfoId: "isolated-world",
  uid: 1,
  name: fixture.name,
  order: 0,
  content: fixture.description,
  tokenCount: countTokens(fixture.description),
  isEnabled: true,
  aliases: fixture.aliases,
  createdAt: "2026-09-23T00:00:00Z",
  updatedAt: "2026-09-23T00:00:00Z",
};

function EntityRegressionHarness({ kind }: { kind: "character" | "world-info" }) {
  const [mountKey, setMountKey] = useState(0);
  const [isLocked, setLocked] = useState(false);
  const saveCallsRef = useRef(0);
  const documentKey = `${kind}:isolated-fixture`;

  useEffect(() => {
    window.__entityRegressionHarness = {
      snapshot: () => ({
        dirty: useEditorSessionStore.getState().getDirtySessions([documentKey]).length > 0,
        saveCalls: saveCallsRef.current,
      }),
      remount: () => setMountKey((key) => key + 1),
      discard: async () => {
        await useEditorSessionStore.getState().sessions.get(documentKey)?.discard?.();
      },
      setLocked,
    };
    return () => {
      delete window.__entityRegressionHarness;
    };
  }, [documentKey]);

  return (
    <QueryClientProvider client={queryClient}>
      <Theme>
        <div
          data-testid="entity-regression-harness"
          style={{ height: 700 }}
        >
          {kind === "character" ? (
            <CharacterEditor
              key={mountKey}
              character={fixtureCharacter}
              isAgentLocked={isLocked}
              onSave={() => {
                saveCallsRef.current += 1;
              }}
            />
          ) : (
            <EntryEditor
              key={mountKey}
              entry={fixtureEntry}
              worldInfoId="isolated-world"
              entries={[]}
              isAgentLocked={isLocked}
            />
          )}
        </div>
      </Theme>
    </QueryClientProvider>
  );
}

void preloadTiktokenEncoding().then(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      {new URLSearchParams(window.location.search).get("mode") === "character" ? (
        <EntityRegressionHarness kind="character" />
      ) : new URLSearchParams(window.location.search).get("mode") === "world-info" ? (
        <EntityRegressionHarness kind="world-info" />
      ) : (
        <RegressionHarness />
      )}
    </StrictMode>,
  );
});
