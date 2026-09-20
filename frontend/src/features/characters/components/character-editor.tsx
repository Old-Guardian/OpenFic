import { Flex, Text } from "@radix-ui/themes";
import type { Editor } from "@tiptap/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { AliasInput, MarkdownEditor, Spinner } from "@/components";
import { toast } from "@/components/toast";
import { useEditorSession } from "@/features/editor-session";
import { useEditorAutoSaveSetting } from "@/features/settings/hooks/use-editor-auto-save-setting";
import { useAutoSave, type SaveReason, type SaveResult } from "@/hooks/use-auto-save";
import type { Character } from "@/lib/character.types";
import {
  getEditorContentLimit,
  MAX_EDITOR_CONTENT_CHARACTERS,
  MAX_EDITOR_CONTENT_LINES,
} from "@/lib/editor-content-limits";
import { countTokens } from "@/lib/tiktoken-utils";

const AUTO_SAVE_DELAY = 1500;

interface CharacterEditorProps {
  character: Character | null;
  isSaving?: boolean;
  isLoading?: boolean;
  isAgentLocked?: boolean;
  onSave: (data: { name: string; description: string; aliases: string[] }) => Promise<void> | void;
}

export function CharacterEditor({
  character,
  isSaving = false,
  isLoading = false,
  isAgentLocked = false,
  onSave,
}: CharacterEditorProps) {
  const { t } = useTranslation();
  const { enabled: autoSaveEnabled, isReady: isAutoSaveReady } = useEditorAutoSaveSetting();

  const [name, setName] = useState(character?.name ?? "");
  const [description, setDescription] = useState(character?.description ?? "");
  const [aliases, setAliases] = useState<string[]>(character?.aliases ?? []);
  const [tokenCount, setTokenCount] = useState(countTokens(character?.description ?? ""));
  const [hasChanges, setHasChanges] = useState(false);
  const [dirtyRevision, setDirtyRevision] = useState(0);
  const [isLocalSaving, setIsLocalSaving] = useState(false);

  const editorRef = useRef<Editor | null>(null);
  const latestValueRef = useRef({
    name: character?.name ?? "",
    description: character?.description ?? "",
    aliases: character?.aliases ?? [],
  });
  const lastSavedBaselineRef = useRef({
    name: character?.name ?? "",
    description: character?.description ?? "",
    aliases: character?.aliases ?? [],
  });
  const hasChangesRef = useRef(false);
  const isSavingRef = useRef(false);
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

  const handleSave = useCallback(
    async (reason: SaveReason = "manual", revision?: number): Promise<SaveResult> => {
      if (!character) return { status: "unchanged" };
      if (isAgentLocked) {
        return { status: "blocked", reason: "Agent is running" };
      }

      const nextName = latestValueRef.current.name.trim();
      if (!nextName) {
        return { status: "blocked", reason: t("characters.nameRequired", "角色名称不能为空") };
      }

      const currentDescription = latestValueRef.current.description;
      const contentLimit = getEditorContentLimit(currentDescription);
      if (!contentLimit.isWithinLimit) {
        showContentLimitToast(currentDescription);
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

      if (!hasChangesRef.current) {
        return { status: "unchanged" };
      }

      const targetRevision = revision ?? dirtyRevision;
      const nextAliases = latestValueRef.current.aliases;

      setIsLocalSaving(true);
      isSavingRef.current = true;
      try {
        await onSave({ name: nextName, description: currentDescription, aliases: nextAliases });
        lastSavedBaselineRef.current = {
          name: nextName,
          description: currentDescription,
          aliases: nextAliases,
        };

        const stillDirty =
          latestValueRef.current.name.trim() !== nextName ||
          latestValueRef.current.description !== currentDescription ||
          JSON.stringify(latestValueRef.current.aliases) !== JSON.stringify(nextAliases);

        hasChangesRef.current = stillDirty;
        setHasChanges(stillDirty);

        if (reason === "manual") {
          toast.success(t("common.saveSuccess"));
        }

        return { status: "saved", savedRevision: targetRevision };
      } catch (error) {
        hasChangesRef.current = true;
        setHasChanges(true);
        return { status: "failed", error };
      } finally {
        setIsLocalSaving(false);
        isSavingRef.current = false;
      }
    },
    [character, dirtyRevision, isAgentLocked, onSave, showContentLimitToast, t],
  );

  const autoSave = useAutoSave({
    documentKey: character ? `character:${character.id}` : "",
    enabled: isAutoSaveReady && autoSaveEnabled && !isAgentLocked,
    delayMs: AUTO_SAVE_DELAY,
    dirtyRevision,
    hasChanges,
    blockedReason: isAgentLocked ? "Agent is running" : null,
    save: (reason, rev) => handleSave(reason, rev),
  });

  const combinedIsSaving = isSaving || isLocalSaving || autoSave.isSaving;

  useEditorSession({
    documentKey: character ? `character:${character.id}` : "",
    entityType: "character",
    entityId: character?.id ?? "",
    title: name.trim() || character?.name || t("characters.untitledCharacter"),
    isDirty: hasChanges,
    isSaving: combinedIsSaving,
    getIsDirty: () => hasChangesRef.current,
    save: async () => autoSave.save("leave"),
    discard: async () => {
      autoSave.cancel();
      if (character) {
        const baseline = lastSavedBaselineRef.current;
        setName(baseline.name);
        setDescription(baseline.description);
        setAliases(baseline.aliases);
        setTokenCount(countTokens(baseline.description));
        latestValueRef.current = {
          name: baseline.name,
          description: baseline.description,
          aliases: baseline.aliases,
        };
        editorRef.current?.commands.setContent(baseline.description);
      }
      hasChangesRef.current = false;
      setHasChanges(false);
    },
  });

  const handleTitleChange = useCallback((value: string) => {
    setName(value);
    latestValueRef.current.name = value;
    hasChangesRef.current = true;
    setHasChanges(true);
    setDirtyRevision((r) => r + 1);
  }, []);

  const handleAliasesChange = useCallback((nextAliases: string[]) => {
    setAliases(nextAliases);
    latestValueRef.current.aliases = nextAliases;
    hasChangesRef.current = true;
    setHasChanges(true);
    setDirtyRevision((r) => r + 1);
  }, []);

  const handleContentChange = useCallback((value: string) => {
    setDescription(value);
    setTokenCount(countTokens(value));
    latestValueRef.current.description = value;
    hasChangesRef.current = true;
    setHasChanges(true);
    setDirtyRevision((r) => r + 1);
  }, []);

  useEffect(() => {
    if (!character) return;
    if (hasChangesRef.current) return;

    const hasSameContent =
      latestValueRef.current.name === character.name &&
      latestValueRef.current.description === character.description &&
      JSON.stringify(latestValueRef.current.aliases) === JSON.stringify(character.aliases ?? []);
    if (hasSameContent) return;

    setName(character.name);
    setDescription(character.description);
    setAliases(character.aliases ?? []);
    setTokenCount(countTokens(character.description));
    latestValueRef.current = {
      name: character.name,
      description: character.description,
      aliases: character.aliases ?? [],
    };
    lastSavedBaselineRef.current = {
      name: character.name,
      description: character.description,
      aliases: character.aliases ?? [],
    };
    hasChangesRef.current = false;
    setHasChanges(false);
  }, [character]);

  if (isLoading) {
    return (
      <Flex
        className="characters-editor-empty"
        align="center"
        justify="center"
      >
        <Spinner size={18} />
      </Flex>
    );
  }

  if (!character) {
    return (
      <Flex
        className="characters-editor-empty"
        direction="column"
        align="center"
        justify="center"
      >
        <Text
          size="3"
          weight="medium"
        >
          {t("characters.selectCharacter")}
        </Text>
        <Text
          size="2"
          color="gray"
        >
          {t("characters.selectCharacterHint")}
        </Text>
      </Flex>
    );
  }

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
      content={description}
      onContentChange={handleContentChange}
      onSave={() => void autoSave.save("manual")}
      isSaving={combinedIsSaving}
      hasChanges={hasChanges}
      placeholder={t("characters.descriptionPlaceholder")}
      titlePlaceholder={t("characters.namePlaceholder")}
      wordCount={tokenCount}
      wordCountLabel={t("characters.tokenCount")}
      editorRef={editorRef}
      isLocked={isAgentLocked}
    />
  );
}
