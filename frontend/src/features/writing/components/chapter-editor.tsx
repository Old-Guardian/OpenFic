import { Box, Flex, Text } from "@radix-ui/themes";
import { useQuery } from "@tanstack/react-query";
import { useEditor, EditorContent } from "@tiptap/react";
import { AtSign } from "lucide-react";
import { AnimatePresence } from "motion/react";
import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { useTranslation } from "react-i18next";
import wordsCountModule from "words-count";

import { toast } from "@/components";
import { TitleInput, EditorToolbar, Spinner } from "@/components";
import { ContextMenu } from "@/components";
import {
  buildChapterMentionTag,
  buildLineRangeMentionTag,
} from "@/features/assistant/lib/mention-text";
import { useEditorSession } from "@/features/editor-session";
import { useEditorAutoSaveSetting } from "@/features/settings/hooks/use-editor-auto-save-setting";
import { fetchSettings } from "@/features/settings/lib/settings-api";
import { useAutoSave, type SaveReason, type SaveResult } from "@/hooks/use-auto-save";
import { useScrollbarAutoHide } from "@/hooks/use-scrollbar-auto-hide";
import { fetchChapter } from "@/lib/api-client";
import type { Chapter } from "@/lib/chapter.types";
import {
  getEditorContentLimit,
  MAX_EDITOR_CONTENT_CHARACTERS,
  MAX_EDITOR_CONTENT_LINES,
} from "@/lib/editor-content-limits";
import { htmlToNewlines, newlinesToHtml } from "@/lib/html-utils";
import { createToastThrottler } from "@/lib/ui-utils";

import { useUpdateChapter } from "../hooks/use-chapters";
import {
  shouldShowWritingEditorLoading,
  useWritingEditorEntity,
} from "../hooks/use-writing-editor-entity";
import {
  useWritingWorkingCopy,
  type WritingDraft,
  type WritingWorkingCopyController,
} from "../hooks/use-writing-working-copy";
import { createChapterEditorDraft, isChapterEditorDraftDirty } from "../lib/chapter-editor-draft";
import { createEditorExtensions } from "../lib/editor-config";
import {
  getNextWritingWorkingCopyTimestamp,
  isRemoteWritingEntityNewer,
} from "../lib/writing-working-copy";
import { useTabsStore } from "../store/use-tabs-store";
import { FindReplacePanel } from "./find-replace-panel";

const MANUAL_SAVE_EVENT = "openfic:chapter-editor-manual-save";

interface WordsCountModule {
  wordsCount: (text: string) => number;
}

const wordsCount = (wordsCountModule as unknown as WordsCountModule).wordsCount;

function getLineNumberDigits(lineCount: number): number {
  return String(Math.max(lineCount, 1)).length;
}

interface ChapterEditorProps {
  chapterId: string | null;
  scrollTop?: number;
  onChapterUpdate?: (chapter: Chapter) => void;
  onScrollPositionChange?: (chapterId: string, scrollTop: number) => void;
  onAddToConversation?: (markup: string) => void;
  isAgentLocked?: boolean;
  onSelectionChange?: (hasSelection: boolean) => void;
  addSelectionToConversationRef?: React.MutableRefObject<(() => void) | null>;
}

interface ChapterEditorContentProps {
  chapter: Chapter;
  scrollTop: number;
  initialDraft: WritingDraft;
  initialDraftUpdatedAt: Date;
  workingCopy: WritingWorkingCopyController;
  onChapterUpdate?: (chapter: Chapter) => void;
  onScrollPositionChange?: (chapterId: string, scrollTop: number) => void;
  onAddToConversation?: (markup: string) => void;
  isAgentLocked?: boolean;
  onSelectionChange?: (hasSelection: boolean) => void;
  addSelectionToConversationRef?: React.MutableRefObject<(() => void) | null>;
}

function ChapterEditorContent({
  chapter,
  scrollTop,
  initialDraft,
  initialDraftUpdatedAt,
  workingCopy,
  onChapterUpdate,
  onScrollPositionChange,
  onAddToConversation,
  isAgentLocked = false,
  onSelectionChange,
  addSelectionToConversationRef,
}: ChapterEditorContentProps) {
  const { t } = useTranslation();
  const updateMutation = useUpdateChapter();
  const { containerRef, scrollbarProps } = useScrollbarAutoHide();
  const editorContentRef = useRef<HTMLDivElement>(null);
  const initialScrollTopRef = useRef(scrollTop);
  const latestScrollTopRef = useRef(scrollTop);
  const scrollPositionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { updateTabTitle } = useTabsStore();
  const { clearWorkingCopy, persistWorkingCopy } = workingCopy;

  const { data: settings } = useQuery({
    queryKey: ["settings"],
    queryFn: fetchSettings,
  });
  const showLineNumbers = settings?.editorShowLineNumbers ?? false;
  const autoIndentRef = useRef(settings?.editorAutoIndent ?? false);
  const autoConvertPunctuationRef = useRef(settings?.editorAutoConvertPunctuation ?? false);
  const autoPairSymbolsRef = useRef(settings?.editorAutoPairSymbols ?? false);

  useEffect(() => {
    autoIndentRef.current = settings?.editorAutoIndent ?? false;
    autoConvertPunctuationRef.current = settings?.editorAutoConvertPunctuation ?? false;
    autoPairSymbolsRef.current = settings?.editorAutoPairSymbols ?? false;
  }, [
    settings?.editorAutoIndent,
    settings?.editorAutoConvertPunctuation,
    settings?.editorAutoPairSymbols,
  ]);

  const [title, setTitle] = useState(initialDraft.title);
  const titleRef = useRef(initialDraft.title);
  const lastSavedDraftRef = useRef(
    createChapterEditorDraft({
      title: chapter.title,
      content: chapter.content,
    }),
  );
  const [hasChanges, setHasChanges] = useState(
    isChapterEditorDraftDirty(lastSavedDraftRef.current, initialDraft),
  );
  const [dirtyRevision, setDirtyRevision] = useState(0);
  const [isSaving, setIsSaving] = useState(false);
  const [findReplaceMode, setFindReplaceMode] = useState<"closed" | "find" | "replace">("closed");
  const [wordCount, setWordCount] = useState(() => wordsCount(initialDraft.content));
  const [lineNumberDigits, setLineNumberDigits] = useState(1);
  const latestDraftRef = useRef(initialDraft);
  const latestDraftUpdatedAtRef = useRef(initialDraftUpdatedAt);
  const hasChangesRef = useRef(isChapterEditorDraftDirty(lastSavedDraftRef.current, initialDraft));
  const baseUpdatedAtRef = useRef(chapter.updatedAt);

  const { enabled: autoSaveEnabled, isReady: isAutoSaveReady } = useEditorAutoSaveSetting();

  const showLockedToast = useMemo(
    () => createToastThrottler(t("writing.agentLockedChapterEdit")),
    [t],
  );
  const rejectedContentRef = useRef<string | null>(null);

  const showContentLimitToast = useCallback(
    (content: string) => {
      if (rejectedContentRef.current === content) return false;
      rejectedContentRef.current = content;
      const { lineCount, characterCount } = getEditorContentLimit(content);
      toast.error(
        t("common.editorContentTooLarge", {
          lineCount,
          characterCount,
          maxLines: MAX_EDITOR_CONTENT_LINES,
          maxCharacters: MAX_EDITOR_CONTENT_CHARACTERS,
        }),
      );
      return true;
    },
    [t],
  );

  const openFind = useCallback(() => {
    if (isAgentLocked) {
      showLockedToast();
      return;
    }
    setFindReplaceMode("find");
  }, [isAgentLocked, showLockedToast]);

  const openReplace = useCallback(() => {
    if (isAgentLocked) {
      showLockedToast();
      return;
    }
    setFindReplaceMode("replace");
  }, [isAgentLocked, showLockedToast]);

  const flushScrollPosition = useCallback(() => {
    if (scrollPositionTimerRef.current) {
      clearTimeout(scrollPositionTimerRef.current);
      scrollPositionTimerRef.current = null;
    }
    const scrollPosition = containerRef.current?.scrollTop ?? latestScrollTopRef.current;
    latestScrollTopRef.current = scrollPosition;
    onScrollPositionChange?.(chapter.id, scrollPosition);
  }, [chapter.id, containerRef, onScrollPositionChange]);

  const handleEditorScroll = useCallback(() => {
    const scrollPosition = containerRef.current?.scrollTop;
    if (scrollPosition === undefined) return;

    latestScrollTopRef.current = scrollPosition;
    if (scrollPositionTimerRef.current) return;

    scrollPositionTimerRef.current = setTimeout(() => {
      scrollPositionTimerRef.current = null;
      onScrollPositionChange?.(chapter.id, latestScrollTopRef.current);
    }, 250);
  }, [chapter.id, containerRef, onScrollPositionChange]);

  useEffect(() => {
    return flushScrollPosition;
  }, [flushScrollPosition]);

  const persistDraft = useCallback(
    (draft: WritingDraft) => {
      latestDraftUpdatedAtRef.current = getNextWritingWorkingCopyTimestamp(
        latestDraftUpdatedAtRef.current,
      );
      persistWorkingCopy(draft, baseUpdatedAtRef.current, latestDraftUpdatedAtRef.current);
    },
    [persistWorkingCopy],
  );

  const updateDirtyState = useCallback(
    (nextTitle: string, nextHtmlContent: string) => {
      const nextDraft = createChapterEditorDraft({
        title: nextTitle,
        content: htmlToNewlines(nextHtmlContent),
      });
      const isDirty = isChapterEditorDraftDirty(lastSavedDraftRef.current, nextDraft);
      latestDraftRef.current = nextDraft;
      hasChangesRef.current = isDirty;
      setHasChanges(isDirty);
      if (isDirty) {
        setDirtyRevision((prev) => prev + 1);
        persistDraft(nextDraft);
      }
      return { draft: nextDraft, isDirty };
    },
    [persistDraft],
  );

  const syncDirtyStateFromEditor = useCallback(
    (editorInstance: { getHTML: () => string }) => {
      return updateDirtyState(titleRef.current, editorInstance.getHTML());
    },
    [updateDirtyState],
  );

  const editor = useEditor({
    extensions: createEditorExtensions({
      placeholder: t("writing.contentPlaceholder"),
      autoIndent: () => autoIndentRef.current,
      autoConvertPunctuation: () => autoConvertPunctuationRef.current,
      autoPairSymbols: () => autoPairSymbolsRef.current,
      shortcuts: {
        onFind: openFind,
        onReplace: openReplace,
        onSave: () => {
          if (isAgentLocked) {
            showLockedToast();
            return;
          }
          window.dispatchEvent(new Event(MANUAL_SAVE_EVENT));
        },
      },
    }),
    editable: !isAgentLocked,
    content: initialDraft.content ? newlinesToHtml(initialDraft.content) : "",
    onUpdate: ({ editor }) => {
      if (isAgentLocked) return;
      syncDirtyStateFromEditor(editor);
      setLineNumberDigits(getLineNumberDigits(editor.state.doc.childCount));
      setWordCount(wordsCount(editor.getText()));
    },
    onCreate: ({ editor }) => {
      setLineNumberDigits(getLineNumberDigits(editor.state.doc.childCount));
      setWordCount(wordsCount(editor.getText()));
    },
  });

  useEffect(() => {
    if (!editor) return;

    let restoreFrameId: number | null = null;
    const frameId = window.requestAnimationFrame(() => {
      restoreFrameId = window.requestAnimationFrame(() => {
        const container = containerRef.current;
        if (!container) return;

        const maxScrollTop = Math.max(0, container.scrollHeight - container.clientHeight);
        const restoredScrollTop = Math.min(initialScrollTopRef.current, maxScrollTop);
        container.scrollTop = restoredScrollTop;
        latestScrollTopRef.current = restoredScrollTop;
        if (restoredScrollTop !== initialScrollTopRef.current) {
          onScrollPositionChange?.(chapter.id, restoredScrollTop);
        }
      });
    });

    return () => {
      window.cancelAnimationFrame(frameId);
      if (restoreFrameId !== null) window.cancelAnimationFrame(restoreFrameId);
    };
  }, [chapter.id, containerRef, editor, onScrollPositionChange]);

  const handleSave = useCallback(
    async (reason: SaveReason = "manual", revision?: number): Promise<SaveResult> => {
      if (!editor) return { status: "unchanged" };
      if (isAgentLocked) {
        showLockedToast();
        return { status: "blocked", reason: t("writing.agentLockedChapterEdit") };
      }

      const draftToSave = latestDraftRef.current;
      const draftUpdatedAt = latestDraftUpdatedAtRef.current;
      const contentLimit = getEditorContentLimit(draftToSave.content);
      if (!contentLimit.isWithinLimit) {
        persistWorkingCopy(draftToSave, baseUpdatedAtRef.current, draftUpdatedAt);
        hasChangesRef.current = true;
        setHasChanges(true);
        showContentLimitToast(draftToSave.content);
        return {
          status: "blocked",
          reason: t("common.editorContentTooLarge", {
            lineCount: contentLimit.lineCount,
            characterCount: contentLimit.characterCount,
            maxLines: MAX_EDITOR_CONTENT_LINES,
            maxCharacters: MAX_EDITOR_CONTENT_CHARACTERS,
          }),
        };
      }
      rejectedContentRef.current = null;

      const isDirty = isChapterEditorDraftDirty(lastSavedDraftRef.current, draftToSave);
      if (!isDirty && !hasChangesRef.current) {
        return { status: "unchanged" };
      }

      const currentWordCount = wordsCount(draftToSave.content);
      const targetRevision = revision ?? dirtyRevision;

      setIsSaving(true);
      try {
        persistWorkingCopy(draftToSave, baseUpdatedAtRef.current, draftUpdatedAt);
        const updatedChapter = await updateMutation.mutateAsync({
          chapterId: chapter.id,
          data: {
            title: draftToSave.title,
            content: draftToSave.content,
            wordCount: currentWordCount,
          },
        });
        lastSavedDraftRef.current = createChapterEditorDraft({
          title: updatedChapter.title,
          content: updatedChapter.content,
        });
        baseUpdatedAtRef.current = updatedChapter.updatedAt;
        void clearWorkingCopy(draftToSave, draftUpdatedAt);

        const stillDirty = isChapterEditorDraftDirty(
          lastSavedDraftRef.current,
          latestDraftRef.current,
        );
        hasChangesRef.current = stillDirty;
        setHasChanges(stillDirty);

        onChapterUpdate?.(updatedChapter);

        if (reason === "manual") {
          toast.success(t("writing.saved"));
        }

        return { status: "saved", savedRevision: targetRevision };
      } catch (error) {
        syncDirtyStateFromEditor(editor);
        if (reason === "manual") {
          toast.error(t("common.saveFailed"));
        }
        return { status: "failed", error };
      } finally {
        setIsSaving(false);
      }
    },
    [
      chapter.id,
      clearWorkingCopy,
      dirtyRevision,
      editor,
      isAgentLocked,
      onChapterUpdate,
      persistWorkingCopy,
      showContentLimitToast,
      showLockedToast,
      syncDirtyStateFromEditor,
      t,
      updateMutation,
    ],
  );

  const autoSave = useAutoSave({
    documentKey: `chapter:${chapter.id}`,
    enabled: isAutoSaveReady && autoSaveEnabled && !isAgentLocked,
    delayMs: 3000,
    dirtyRevision,
    hasChanges,
    blockedReason: isAgentLocked ? t("writing.agentLockedChapterEdit") : null,
    save: (reason, rev) => handleSave(reason, rev),
  });

  const combinedIsSaving = isSaving || autoSave.isSaving;
  const saveStatus = combinedIsSaving ? "saving" : hasChanges ? "unsaved" : "saved";

  useEditorSession({
    documentKey: `chapter:${chapter.id}`,
    entityType: "chapter",
    entityId: chapter.id,
    title: title.trim() || t("writing.untitledChapter"),
    isDirty: hasChanges,
    isSaving: combinedIsSaving,
    getIsDirty: () => hasChangesRef.current,
    save: async () => {
      return autoSave.save("leave");
    },
    discard: async () => {
      autoSave.cancel();
      // 等待在途保存请求结束，再丢弃剩余修改，避免放行早于请求完成
      await autoSave.whenIdle();
      await workingCopy.discardWorkingCopy();
      hasChangesRef.current = false;
      setHasChanges(false);
    },
  });

  useEffect(() => {
    titleRef.current = title;
  }, [title]);

  useEffect(() => {
    const handleManualSave = () => {
      void autoSave.save("manual");
    };

    window.addEventListener(MANUAL_SAVE_EVENT, handleManualSave);
    return () => window.removeEventListener(MANUAL_SAVE_EVENT, handleManualSave);
  }, [autoSave]);

  useEffect(() => {
    if (!editor) return;
    editor.setEditable(!isAgentLocked);
  }, [editor, isAgentLocked]);

  useEffect(() => {
    return () => {
      if (hasChangesRef.current) {
        persistWorkingCopy(
          latestDraftRef.current,
          baseUpdatedAtRef.current,
          latestDraftUpdatedAtRef.current,
        );
      }
    };
  }, [persistWorkingCopy]);

  useEffect(() => {
    if (
      !editor ||
      hasChanges ||
      !isRemoteWritingEntityNewer(chapter.updatedAt, baseUpdatedAtRef.current)
    ) {
      return;
    }

    const nextTitle = chapter.title;
    const nextContent = chapter.content ? newlinesToHtml(chapter.content) : "";
    const currentContent = editor.getHTML();
    lastSavedDraftRef.current = createChapterEditorDraft({
      title: nextTitle,
      content: chapter.content,
    });
    latestDraftRef.current = lastSavedDraftRef.current;
    latestDraftUpdatedAtRef.current = new Date(chapter.updatedAt);
    baseUpdatedAtRef.current = chapter.updatedAt;

    if (title !== nextTitle) {
      titleRef.current = nextTitle;
      queueMicrotask(() => {
        setTitle(nextTitle);
        updateTabTitle(chapter.id, nextTitle);
      });
    }

    if (currentContent !== nextContent) {
      editor.commands.setContent(nextContent, { emitUpdate: false });
      setLineNumberDigits(getLineNumberDigits(editor.state.doc.childCount));
      queueMicrotask(() => {
        setWordCount(wordsCount(editor.getText()));
      });
    }
  }, [
    editor,
    chapter.title,
    chapter.content,
    chapter.id,
    chapter.updatedAt,
    hasChanges,
    title,
    updateTabTitle,
  ]);

  useHotkeys(
    "mod+s",
    (event) => {
      event.preventDefault();
      if (isAgentLocked) {
        showLockedToast();
        return;
      }
      void autoSave.save("manual");
    },
    { enableOnFormTags: true },
  );

  useHotkeys(
    "mod+f",
    (event) => {
      event.preventDefault();
      if (isAgentLocked) {
        showLockedToast();
        return;
      }
      setFindReplaceMode("find");
    },
    { enableOnFormTags: true },
  );

  useHotkeys(
    "mod+h",
    (event) => {
      event.preventDefault();
      if (isAgentLocked) {
        showLockedToast();
        return;
      }
      setFindReplaceMode("replace");
    },
    { enableOnFormTags: true },
  );

  const handleTitleChange = (newTitle: string) => {
    if (isAgentLocked) {
      showLockedToast();
      return;
    }
    setTitle(newTitle);
    titleRef.current = newTitle;
    if (editor) {
      updateDirtyState(newTitle, editor.getHTML());
    } else {
      const draft = createChapterEditorDraft({
        title: newTitle,
        content: latestDraftRef.current.content,
      });
      latestDraftRef.current = draft;
      const isDirty = draft.title !== lastSavedDraftRef.current.title;
      hasChangesRef.current = isDirty;
      setHasChanges(isDirty);
      if (isDirty) {
        setDirtyRevision((prev) => prev + 1);
        persistDraft(draft);
      }
    }
    updateTabTitle(chapter.id, newTitle);
  };

  const addSelectionToConversation = useCallback(() => {
    if (!editor || !onAddToConversation) return;

    const chapterLabel = chapter.title.trim() || t("writing.untitledChapter");
    const { from, to } = editor.state.selection;
    const selectedText =
      from === to ? "" : editor.state.doc.textBetween(from, to, "\n", "\n").trim();
    if (!selectedText) return;

    const textBeforeSelection = editor.state.doc.textBetween(0, from, "\n", "\n");
    const textBeforeSelectionEnd = editor.state.doc.textBetween(0, to, "\n", "\n");
    const startLine = textBeforeSelection.split("\n").length;
    const endLine = textBeforeSelectionEnd.split("\n").length;

    onAddToConversation(
      buildLineRangeMentionTag({
        chapterId: chapter.id,
        startLine,
        endLine,
        label: `${chapterLabel} L${startLine}-${endLine}`,
        snapshotText: selectedText,
      }),
    );

    editor.commands.setTextSelection(from);
    const domSelection = window.getSelection();
    if (domSelection?.anchorNode && editor.view.dom.contains(domSelection.anchorNode)) {
      domSelection.removeAllRanges();
    }
  }, [chapter.id, chapter.title, editor, onAddToConversation, t]);

  useEffect(() => {
    if (!editor) return;
    const reportSelection = () => {
      onSelectionChange?.(editor.state.selection.from !== editor.state.selection.to);
    };
    reportSelection();
    editor.on("selectionUpdate", reportSelection);
    return () => {
      editor.off("selectionUpdate", reportSelection);
      onSelectionChange?.(false);
    };
  }, [editor, onSelectionChange]);

  useEffect(() => {
    if (!addSelectionToConversationRef) return;
    addSelectionToConversationRef.current = addSelectionToConversation;
    return () => {
      addSelectionToConversationRef.current = null;
    };
  }, [addSelectionToConversation, addSelectionToConversationRef]);

  const editorExtraItems = useCallback(() => {
    if (!editor || !onAddToConversation) return [];

    const chapterLabel = chapter.title.trim() || t("writing.untitledChapter");
    const { from, to } = editor.state.selection;
    const selectedText =
      from === to ? "" : editor.state.doc.textBetween(from, to, "\n", "\n").trim();
    const hasSelection = selectedText.length > 0;

    return [
      {
        id: "addToConversation",
        label: hasSelection ? t("editor.addSelectedToConversation") : t("editor.addToConversation"),
        icon: AtSign,
        onClick: () => {
          if (!hasSelection) {
            onAddToConversation(
              buildChapterMentionTag({
                chapterId: chapter.id,
                label: chapterLabel,
              }),
            );
            return;
          }
          addSelectionToConversation();
        },
      },
    ];
  }, [addSelectionToConversation, chapter.id, chapter.title, editor, onAddToConversation, t]);

  const editorMaxWidth = 800;
  const lineNumberWidth = `max(1.5rem, calc(${lineNumberDigits}ch + 0.25rem))`;
  const lineNumberWidthStyle = showLineNumbers
    ? ({ "--editor-line-number-width": lineNumberWidth } as React.CSSProperties)
    : undefined;

  return (
    <Box
      style={{
        height: "100%",
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <EditorToolbar
        editor={editor}
        onSave={() => void autoSave.save("manual")}
        isSaving={saveStatus === "saving"}
        hasChanges={hasChanges}
        isAgentLocked={isAgentLocked}
        onLockedAction={showLockedToast}
        onOpenFind={openFind}
        onOpenReplace={openReplace}
        showChapterTools
      />

      <AnimatePresence>
        {findReplaceMode !== "closed" && editor && !isAgentLocked && (
          <FindReplacePanel
            key="find-replace-panel"
            editor={editor}
            showReplace={findReplaceMode === "replace"}
            onClose={() => setFindReplaceMode("closed")}
          />
        )}
      </AnimatePresence>

      <Box
        ref={containerRef}
        style={{ flex: 1, minHeight: 0, overflow: "auto" }}
        className={`tiptap-editor-wrapper${showLineNumbers ? " tiptap-editor-wrapper--line-numbers" : ""} ${scrollbarProps.className}`}
        onWheel={scrollbarProps.onWheel}
        onMouseMove={scrollbarProps.onMouseMove}
        onMouseLeave={scrollbarProps.onMouseLeave}
        onScroll={handleEditorScroll}
        onClick={isAgentLocked ? showLockedToast : undefined}
      >
        <Box
          className="chapter-editor-content"
          style={{
            maxWidth: editorMaxWidth,
            ...lineNumberWidthStyle,
          }}
        >
          <TitleInput
            value={title}
            onChange={handleTitleChange}
            onBlur={() => {
              if (hasChanges && !isAgentLocked && autoSaveEnabled) {
                void autoSave.save("auto");
              }
            }}
            disabled={isAgentLocked}
            onDisabledClick={showLockedToast}
          />
          <Box style={{ borderBottom: "1px solid var(--gray-a4)" }} />
          <Box
            py="5"
            ref={editorContentRef}
          >
            <EditorContent
              editor={editor}
              className={`tiptap-editor${showLineNumbers ? " tiptap-editor--line-numbers" : ""}`}
            />
          </Box>
        </Box>
      </Box>

      {!isAgentLocked && (
        <ContextMenu
          editor={editor}
          containerRef={editorContentRef}
          editorExtraItems={editorExtraItems}
        />
      )}

      <Flex
        px="6"
        py="3"
        justify="between"
        align="center"
        style={{
          borderTop: "1px solid var(--gray-a4)",
          background: "var(--theme-editor-bar-background)",
        }}
      >
        <Text
          size="1"
          color="gray"
        >
          {wordCount} {t("writing.words")}
        </Text>
        <Text
          size="1"
          color="gray"
        >
          {saveStatus === "saving" && t("writing.saving")}
          {saveStatus === "saved" && t("writing.saved")}
          {saveStatus === "unsaved" && t("writing.unsavedChanges")}
        </Text>
      </Flex>
    </Box>
  );
}

export function ChapterEditor({
  chapterId,
  scrollTop = 0,
  onChapterUpdate,
  onScrollPositionChange,
  onAddToConversation,
  isAgentLocked = false,
  onSelectionChange,
  addSelectionToConversationRef,
}: ChapterEditorProps) {
  const { t } = useTranslation();
  const { data } = useWritingEditorEntity({
    type: "chapter",
    entityId: chapterId,
    fetchEntity: fetchChapter,
  });

  if (!chapterId) {
    return (
      <Flex
        align="center"
        justify="center"
        style={{ flex: 1, minHeight: 0 }}
      >
        <Text
          color="gray"
          size="3"
        >
          {t("writing.selectChapter")}
        </Text>
      </Flex>
    );
  }

  if (shouldShowWritingEditorLoading(data)) {
    return (
      <Flex
        align="center"
        justify="center"
        style={{ flex: 1, minHeight: 0, height: "100%" }}
      >
        <Spinner size={18} />
      </Flex>
    );
  }

  return (
    <ChapterEditorWorkingCopy
      key={`${data.entity.id}:${data.draftUpdatedAt.getTime()}`}
      chapter={data.entity}
      scrollTop={scrollTop}
      initialDraft={data.draft}
      initialDraftUpdatedAt={data.draftUpdatedAt}
      onChapterUpdate={onChapterUpdate}
      onScrollPositionChange={onScrollPositionChange}
      onAddToConversation={onAddToConversation}
      isAgentLocked={isAgentLocked}
      onSelectionChange={onSelectionChange}
      addSelectionToConversationRef={addSelectionToConversationRef}
    />
  );
}

function ChapterEditorWorkingCopy({
  chapter,
  scrollTop,
  initialDraft,
  initialDraftUpdatedAt,
  onChapterUpdate,
  onScrollPositionChange,
  onAddToConversation,
  isAgentLocked,
  onSelectionChange,
  addSelectionToConversationRef,
}: Omit<ChapterEditorContentProps, "workingCopy">) {
  const workingCopy = useWritingWorkingCopy({
    type: "chapter",
    entityId: chapter.id,
  });

  return (
    <ChapterEditorContent
      key={`${chapter.id}:${initialDraftUpdatedAt.getTime()}`}
      chapter={chapter}
      scrollTop={scrollTop}
      initialDraft={initialDraft}
      initialDraftUpdatedAt={initialDraftUpdatedAt}
      workingCopy={workingCopy}
      onChapterUpdate={onChapterUpdate}
      onScrollPositionChange={onScrollPositionChange}
      onAddToConversation={onAddToConversation}
      isAgentLocked={isAgentLocked}
      onSelectionChange={onSelectionChange}
      addSelectionToConversationRef={addSelectionToConversationRef}
    />
  );
}
