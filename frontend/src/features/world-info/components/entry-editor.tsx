/**
 * Entry Editor Component
 *
 * 世界书条目编辑器，基于项目 Markdown 编辑器，支持自动保存。
 * 注意：父组件应使用 key={entry.id} 来确保 entry 变化时组件重新挂载。
 */

import { useQueryClient } from "@tanstack/react-query";
import type { Editor } from "@tiptap/react";
import { useState, useCallback, useRef, useEffect } from "react";
import { useTranslation } from "react-i18next";

import { AliasInput, MarkdownEditor } from "@/components";
import { toast } from "@/components/toast";
import { useEditorSession } from "@/features/editor-session";
import { useEditorAutoSaveSetting } from "@/features/settings/hooks/use-editor-auto-save-setting";
import { useAutoSave, saveOnTitleBlur, type SaveReason, type SaveResult } from "@/hooks/use-auto-save";
import { updateWorldInfoEntry } from "@/lib/api-client";
import {
  getEditorContentLimit,
  MAX_EDITOR_CONTENT_CHARACTERS,
  MAX_EDITOR_CONTENT_LINES,
} from "@/lib/editor-content-limits";
import { countTokens } from "@/lib/tiktoken-utils";
import type {
  WorldInfoEntry,
  WorldInfoEntryBrief,
  WorldInfoEntryBriefListResponse,
} from "@/lib/world-info.types";

import { resolveRemoteEntryEditorState } from "./entry-editor-state";

interface EntryEditorProps {
  /** 条目数据 */
  entry: WorldInfoEntry;
  /** 世界书 ID（用于刷新缓存） */
  worldInfoId: string;
  /** 同一世界书中的条目列表，用于名称唯一性校验 */
  entries: WorldInfoEntryBrief[];
  /** 滚动到指定行（1-based） */
  scrollToLine?: number | null;
  /** 滚动完成后回调 */
  onScrollComplete?: () => void;
  /** Agent 运行时锁定编辑 */
  isAgentLocked?: boolean;
}

/** 自动保存防抖延迟（毫秒） */
const AUTO_SAVE_DELAY = 1500;

export function EntryEditor({
  entry,
  worldInfoId,
  entries,
  scrollToLine,
  onScrollComplete,
  isAgentLocked = false,
}: EntryEditorProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { enabled: autoSaveEnabled, isReady: isAutoSaveReady } = useEditorAutoSaveSetting();

  const [name, setName] = useState(entry.name);
  const [aliases, setAliases] = useState<string[]>(entry.aliases ?? []);
  const [tokenCount, setTokenCount] = useState<number>(entry.tokenCount || 0);
  const [hasChanges, setHasChanges] = useState(false);
  const [dirtyRevision, setDirtyRevision] = useState(0);
  const [isSaving, setIsSaving] = useState(false);

  const savedContentRef = useRef(entry.content);
  const savedNameRef = useRef(entry.name);
  const savedAliasesRef = useRef<string[]>(entry.aliases ?? []);
  const lastSavedBaselineRef = useRef({
    name: entry.name,
    content: entry.content,
    aliases: entry.aliases ?? [],
  });
  const hasChangesRef = useRef(false);
  const isSavingRef = useRef(false);
  const editorRef = useRef<Editor | null>(null);
  const scrolledRef = useRef(false);
  const rejectedContentRef = useRef<string | null>(null);

  const showContentLimitToast = useCallback(
    (content: string) => {
      if (rejectedContentRef.current === content) return;
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
    },
    [t],
  );

  const updateCaches = useCallback(
    (updated: WorldInfoEntry) => {
      queryClient.setQueryData(["world-info-entry-detail", entry.id], updated);
      queryClient.setQueryData(
        ["world-info-entries", worldInfoId],
        (old: WorldInfoEntryBriefListResponse | undefined) => {
          if (!old) return old;
          return {
            ...old,
            items: old.items.map((item) =>
              item.id === updated.id
                ? {
                    ...item,
                    name: updated.name,
                    tokenCount: updated.tokenCount,
                    aliases: updated.aliases ?? [],
                  }
                : item,
            ),
          };
        },
      );
    },
    [entry.id, queryClient, worldInfoId],
  );

  const handleSave = useCallback(
    async (reason: SaveReason = "manual", revision?: number): Promise<SaveResult> => {
      if (isAgentLocked) {
        return { status: "blocked", reason: "Agent is running" };
      }

      const content = savedContentRef.current;
      const newName = savedNameRef.current.trim();
      const nextAliases = savedAliasesRef.current;

      if (!newName) {
        toast.error(t("worldInfo.entryNameRequired", "条目名称不能为空"));
        return { status: "blocked", reason: "Name required" };
      }

      const contentLimit = getEditorContentLimit(content);
      if (!contentLimit.isWithinLimit) {
        showContentLimitToast(content);
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

      const hasDuplicateName = entries.some(
        (item) => item.id !== entry.id && item.name === newName,
      );
      if (hasDuplicateName) {
        toast.error(t("worldInfo.duplicateEntryName"));
        return { status: "blocked", reason: t("worldInfo.duplicateEntryName") };
      }

      if (!hasChangesRef.current) {
        return { status: "unchanged" };
      }

      const targetRevision = revision ?? dirtyRevision;
      const newTokenCount = countTokens(content);
      setTokenCount(newTokenCount);

      isSavingRef.current = true;
      setIsSaving(true);

      try {
        const updated = await updateWorldInfoEntry(entry.id, {
          name: newName,
          content,
          tokenCount: newTokenCount,
          aliases: nextAliases,
        });
        updateCaches(updated);
        lastSavedBaselineRef.current = {
          name: updated.name,
          content: updated.content,
          aliases: updated.aliases ?? [],
        };

        const stillDirty =
          savedNameRef.current.trim() !== updated.name ||
          savedContentRef.current !== updated.content ||
          JSON.stringify(savedAliasesRef.current) !== JSON.stringify(updated.aliases ?? []);

        hasChangesRef.current = stillDirty;
        setHasChanges(stillDirty);

        if (reason === "manual") {
          toast.success(t("common.saveSuccess"));
        }

        return { status: "saved", savedRevision: targetRevision };
      } catch (error) {
        hasChangesRef.current = true;
        setHasChanges(true);
        const detail = (error as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail;
        if (reason === "manual" || reason === "leave") {
          toast.error(detail || t("common.saveFailed"));
        }
        return { status: "failed", error };
      } finally {
        isSavingRef.current = false;
        setIsSaving(false);
      }
    },
    [dirtyRevision, entries, entry.id, isAgentLocked, showContentLimitToast, t, updateCaches],
  );

  const autoSave = useAutoSave({
    documentKey: `world-info:${entry.id}`,
    enabled: isAutoSaveReady && autoSaveEnabled && !isAgentLocked,
    delayMs: AUTO_SAVE_DELAY,
    dirtyRevision,
    hasChanges,
    blockedReason: isAgentLocked ? "Agent is running" : null,
    save: (reason, rev) => handleSave(reason, rev),
  });

  const combinedIsSaving = isSaving || autoSave.isSaving;

  useEditorSession({
    documentKey: `world-info:${entry.id}`,
    entityType: "world-info",
    entityId: entry.id,
    title: name.trim() || entry.name || t("worldInfo.untitledEntry", "未命名条目"),
    isDirty: hasChanges,
    isSaving: combinedIsSaving,
    getIsDirty: () => hasChangesRef.current,
    save: async () => autoSave.save("leave"),
    discard: async () => {
      autoSave.cancel();
      // 等待在途保存请求结束，再恢复基线，避免放行早于请求完成
      await autoSave.whenIdle();
      const baseline = lastSavedBaselineRef.current;
      savedNameRef.current = baseline.name;
      savedContentRef.current = baseline.content;
      savedAliasesRef.current = baseline.aliases;
      setName(baseline.name);
      setAliases(baseline.aliases);
      setTokenCount(countTokens(baseline.content));
      editorRef.current?.commands.setContent(baseline.content, {
        contentType: "markdown",
        emitUpdate: false,
      });
      hasChangesRef.current = false;
      setHasChanges(false);
    },
  });

  const handleTitleChange = useCallback((newName: string) => {
    setName(newName);
    savedNameRef.current = newName;
    hasChangesRef.current = true;
    setHasChanges(true);
    setDirtyRevision((r) => r + 1);
  }, []);

  const handleAliasesChange = useCallback((nextAliases: string[]) => {
    setAliases(nextAliases);
    savedAliasesRef.current = nextAliases;
    hasChangesRef.current = true;
    setHasChanges(true);
    setDirtyRevision((r) => r + 1);
  }, []);

  const handleContentChange = useCallback((markdown: string) => {
    savedContentRef.current = markdown;
    setTokenCount(countTokens(markdown));
    hasChangesRef.current = true;
    setHasChanges(true);
    setDirtyRevision((r) => r + 1);
  }, []);

  useEffect(() => {
    const nextState = resolveRemoteEntryEditorState(
      {
        name: savedNameRef.current,
        content: savedContentRef.current,
        tokenCount,
        aliases: savedAliasesRef.current,
      },
      {
        name: entry.name,
        content: entry.content,
        tokenCount: entry.tokenCount || 0,
        aliases: entry.aliases ?? [],
      },
      hasChangesRef.current,
    );
    if (hasChangesRef.current) return;

    savedNameRef.current = nextState.name;
    savedContentRef.current = nextState.content;
    savedAliasesRef.current = nextState.aliases;
    lastSavedBaselineRef.current = {
      name: nextState.name,
      content: nextState.content,
      aliases: nextState.aliases,
    };
    setName(nextState.name);
    setTokenCount(nextState.tokenCount);
    setAliases(nextState.aliases);
  }, [entry.aliases, entry.content, entry.name, entry.tokenCount, tokenCount]);

  useEffect(() => {
    if (scrollToLine == null || scrollToLine < 1 || scrolledRef.current) return;
    const editor = editorRef.current;
    if (!editor || editor.isDestroyed) return;

    const timer = setTimeout(() => {
      if (editor.isDestroyed) return;
      try {
        const totalLines = editor.state.doc.content.size;
        const lineHeight = 24;
        const targetPos = Math.min((scrollToLine - 1) * lineHeight, totalLines);
        const resolvedPos = editor.state.doc.resolve(targetPos);
        const node = editor.view.domAtPos(resolvedPos.pos);
        if (node.node) {
          const el =
            node.node.nodeType === Node.TEXT_NODE
              ? node.node.parentElement
              : (node.node as HTMLElement);
          el?.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      } finally {
        scrolledRef.current = true;
        onScrollComplete?.();
      }
    }, 200);

    return () => clearTimeout(timer);
  }, [scrollToLine, onScrollComplete]);

  useEffect(() => {
    scrolledRef.current = false;
  }, [entry.id]);

  return (
    <MarkdownEditor
      title={name}
      onTitleChange={handleTitleChange}
      belowTitle={
        <AliasInput
          aliases={aliases}
          onChange={handleAliasesChange}
          entityName={name}
          disabled={isAgentLocked}
        />
      }
      content={entry.content}
      onContentChange={handleContentChange}
      onSave={() => void autoSave.save("manual")}
      onTitleBlurSave={() => saveOnTitleBlur(autoSaveEnabled, autoSave.save)}
      isSaving={combinedIsSaving}
      hasChanges={hasChanges}
      placeholder={t("worldInfo.contentPlaceholder")}
      titlePlaceholder={t("worldInfo.entryNamePlaceholder")}
      wordCount={tokenCount}
      wordCountLabel={t("worldInfo.tokenCount")}
      editorRef={editorRef}
      isLocked={isAgentLocked}
    />
  );
}
