import { Theme } from "@radix-ui/themes";
import { StrictMode, useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { MarkdownEditor } from "../src/components/markdown-editor";
import { useAutoSave, type SaveReason, type SaveResult } from "../src/hooks/use-auto-save";
import { countTokens, preloadTiktokenEncoding } from "../src/lib/tiktoken-utils";
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
}

declare global {
  interface Window {
    __editorSaveHarness?: HarnessApi;
  }
}

const initialContent = new URLSearchParams(window.location.search).get("case") === "plain"
  ? "Simple character description"
  : fixture.description;

/* oxlint-disable react-refresh/only-export-components -- isolated Vite test entry */
function RegressionHarness() {
  const [content, setContent] = useState(initialContent);
  const [hasChanges, setHasChanges] = useState(false);
  const [dirtyRevision, setDirtyRevision] = useState(0);
  const contentRef = useRef(initialContent);
  const dirtyRef = useRef(false);
  const revisionRef = useRef(0);
  const contentChangesRef = useRef(0);
  const saveCallsRef = useRef<SaveCall[]>([]);
  const saveModeRef = useRef<SaveMode>("immediate");
  const pendingSaveRef = useRef<PendingSave | null>(null);

  const saveAdapter = useCallback(async (reason: SaveReason, revision: number): Promise<SaveResult> => {
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
  }, []);

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
      setSaveMode: (mode) => { saveModeRef.current = mode; },
      resolveSave: () => pendingSaveRef.current?.resolve(),
      rejectSave: () => pendingSaveRef.current?.reject(new Error("Controlled save failure")),
    };
    return () => { delete window.__editorSaveHarness; };
  }, []);

  return (
    <Theme>
      <div data-testid="regression-harness" style={{ height: 700 }}>
        <MarkdownEditor
          title={fixture.name}
          onTitleChange={() => {}}
          content={content}
          onContentChange={handleContentChange}
          onSave={() => { void autoSave.save("manual"); }}
          hasChanges={hasChanges}
          isSaving={autoSave.isSaving}
          wordCount={countTokens(content)}
          wordCountLabel="Token"
        />
      </div>
    </Theme>
  );
}

void preloadTiktokenEncoding().then(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <RegressionHarness />
    </StrictMode>,
  );
});
