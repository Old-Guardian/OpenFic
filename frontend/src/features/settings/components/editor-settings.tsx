import { Box, Button, Flex, Switch, Text, Tooltip } from "@radix-ui/themes";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Info } from "lucide-react";
import { useRef } from "react";
import { useTranslation } from "react-i18next";

import { Spinner, toast } from "@/components";

import { fetchSettings, updateSettings } from "../lib/settings-api";
import type { Settings, SettingsUpdateRequest } from "../lib/settings.types";

export function EditorSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const mutationSequenceRef = useRef(0);

  const {
    data: settings,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["settings"],
    queryFn: fetchSettings,
  });

  const updateMutation = useMutation({
    mutationFn: updateSettings,
    onMutate: async (patch: SettingsUpdateRequest) => {
      const seq = ++mutationSequenceRef.current;
      await queryClient.cancelQueries({ queryKey: ["settings"] });
      const previousSettings = queryClient.getQueryData<Settings>(["settings"]);

      if (previousSettings) {
        queryClient.setQueryData<Settings>(["settings"], {
          ...previousSettings,
          ...(patch.editor_auto_save !== undefined ? { editorAutoSave: patch.editor_auto_save } : {}),
          ...(patch.editor_auto_indent !== undefined ? { editorAutoIndent: patch.editor_auto_indent } : {}),
          ...(patch.editor_auto_convert_punctuation !== undefined
            ? { editorAutoConvertPunctuation: patch.editor_auto_convert_punctuation }
            : {}),
          ...(patch.editor_auto_pair_symbols !== undefined
            ? { editorAutoPairSymbols: patch.editor_auto_pair_symbols }
            : {}),
          ...(patch.editor_show_line_numbers !== undefined
            ? { editorShowLineNumbers: patch.editor_show_line_numbers }
            : {}),
        });
      }

      return { previousSettings, seq };
    },
    onSuccess: (nextSettings, variables, context) => {
      if (context?.seq === mutationSequenceRef.current) {
        queryClient.setQueryData(["settings"], nextSettings);
      } else {
        queryClient.setQueryData<Settings>(["settings"], (current) => {
          if (!current) return nextSettings;
          const merged = { ...current };
          if (variables.editor_auto_save !== undefined) {
            merged.editorAutoSave = nextSettings.editorAutoSave;
          }
          if (variables.editor_auto_indent !== undefined) {
            merged.editorAutoIndent = nextSettings.editorAutoIndent;
          }
          if (variables.editor_auto_convert_punctuation !== undefined) {
            merged.editorAutoConvertPunctuation = nextSettings.editorAutoConvertPunctuation;
          }
          if (variables.editor_auto_pair_symbols !== undefined) {
            merged.editorAutoPairSymbols = nextSettings.editorAutoPairSymbols;
          }
          if (variables.editor_show_line_numbers !== undefined) {
            merged.editorShowLineNumbers = nextSettings.editorShowLineNumbers;
          }
          return merged;
        });
      }
      toast.success(t("settings.saved"));
    },
    onError: (_error, variables, context) => {
      const prev = context?.previousSettings;
      if (prev) {
        if (context?.seq === mutationSequenceRef.current) {
          queryClient.setQueryData(["settings"], prev);
        } else {
          queryClient.setQueryData<Settings>(["settings"], (current) => {
            if (!current) return prev;
            const rolledBack = { ...current };
            if (variables.editor_auto_save !== undefined) {
              rolledBack.editorAutoSave = prev.editorAutoSave;
            }
            if (variables.editor_auto_indent !== undefined) {
              rolledBack.editorAutoIndent = prev.editorAutoIndent;
            }
            if (variables.editor_auto_convert_punctuation !== undefined) {
              rolledBack.editorAutoConvertPunctuation = prev.editorAutoConvertPunctuation;
            }
            if (variables.editor_auto_pair_symbols !== undefined) {
              rolledBack.editorAutoPairSymbols = prev.editorAutoPairSymbols;
            }
            if (variables.editor_show_line_numbers !== undefined) {
              rolledBack.editorShowLineNumbers = prev.editorShowLineNumbers;
            }
            return rolledBack;
          });
        }
      }
      toast.error(t("settings.saveFailed"));
    },
  });

  if (isLoading) {
    return (
      <Flex
        align="center"
        justify="center"
        style={{ height: "100%" }}
      >
        <Spinner size={18} />
      </Flex>
    );
  }

  if (isError || !settings) {
    return (
      <Flex
        direction="column"
        align="center"
        justify="center"
        gap="3"
        style={{ height: "100%", padding: "24px" }}
      >
        <Text
          color="red"
          size="2"
        >
          {t("common.error")}
        </Text>
        <Button
          size="2"
          variant="soft"
          onClick={() => {
            void refetch();
          }}
        >
          {t("common.retry")}
        </Button>
      </Flex>
    );
  }

  return (
    <Box>
      <Flex
        direction="column"
        gap="5"
      >
        <Flex
          align="center"
          justify="between"
          gap="4"
        >
          <Flex
            direction="column"
            gap="1"
          >
            <Text
              size="2"
              weight="medium"
            >
              {t("settings.editorAutoSave")}
            </Text>
            <Text
              size="1"
              color="gray"
            >
              {t("settings.editorAutoSaveHint")}
            </Text>
          </Flex>
          <Switch
            checked={settings.editorAutoSave}
            disabled={updateMutation.isPending}
            aria-label={t("settings.editorAutoSave")}
            onCheckedChange={(checked) => {
              updateMutation.mutate({ editor_auto_save: checked });
            }}
          />
        </Flex>

        <Flex
          align="center"
          justify="between"
          gap="4"
        >
          <Flex
            direction="column"
            gap="1"
          >
            <Flex
              align="center"
              gap="1"
            >
              <Text
                size="2"
                weight="medium"
              >
                {t("settings.editorAutoIndent")}
              </Text>
              <Tooltip content={t("settings.editorAutoIndentTooltip")}>
                <button
                  type="button"
                  className="advanced-settings-info-button"
                  aria-label={t("settings.editorAutoIndentTooltipLabel")}
                >
                  <Info size={14} />
                </button>
              </Tooltip>
            </Flex>
            <Text
              size="1"
              color="gray"
            >
              {t("settings.editorAutoIndentHint")}
            </Text>
          </Flex>
          <Switch
            checked={settings.editorAutoIndent}
            aria-label={t("settings.editorAutoIndent")}
            onCheckedChange={(checked) => {
              updateMutation.mutate({ editor_auto_indent: checked });
            }}
          />
        </Flex>

        <Flex
          align="center"
          justify="between"
          gap="4"
        >
          <Flex
            direction="column"
            gap="1"
          >
            <Flex
              align="center"
              gap="1"
            >
              <Text
                size="2"
                weight="medium"
              >
                {t("settings.editorAutoConvertPunctuation")}
              </Text>
              <Tooltip content={t("settings.editorAutoConvertPunctuationTooltipSymbols")}>
                <button
                  type="button"
                  className="advanced-settings-info-button"
                  aria-label={t("settings.editorAutoConvertPunctuationTooltipLabel")}
                >
                  <Info size={14} />
                </button>
              </Tooltip>
            </Flex>
            <Text
              size="1"
              color="gray"
            >
              {t("settings.editorAutoConvertPunctuationHint")}
            </Text>
          </Flex>
          <Switch
            checked={settings.editorAutoConvertPunctuation}
            aria-label={t("settings.editorAutoConvertPunctuation")}
            onCheckedChange={(checked) => {
              updateMutation.mutate({ editor_auto_convert_punctuation: checked });
            }}
          />
        </Flex>

        <Flex
          align="center"
          justify="between"
          gap="4"
        >
          <Flex
            direction="column"
            gap="1"
          >
            <Flex
              align="center"
              gap="1"
            >
              <Text
                size="2"
                weight="medium"
              >
                {t("settings.editorAutoPairSymbols")}
              </Text>
              <Tooltip content={t("settings.editorAutoPairSymbolsTooltipSymbols")}>
                <button
                  type="button"
                  className="advanced-settings-info-button"
                  aria-label={t("settings.editorAutoPairSymbolsTooltipLabel")}
                >
                  <Info size={14} />
                </button>
              </Tooltip>
            </Flex>
            <Text
              size="1"
              color="gray"
            >
              {t("settings.editorAutoPairSymbolsHint")}
            </Text>
          </Flex>
          <Switch
            checked={settings.editorAutoPairSymbols}
            aria-label={t("settings.editorAutoPairSymbols")}
            onCheckedChange={(checked) => {
              updateMutation.mutate({ editor_auto_pair_symbols: checked });
            }}
          />
        </Flex>

        <Flex
          align="center"
          justify="between"
          gap="4"
        >
          <Flex
            direction="column"
            gap="1"
          >
            <Text
              size="2"
              weight="medium"
            >
              {t("settings.editorShowLineNumbers")}
            </Text>
            <Text
              size="1"
              color="gray"
            >
              {t("settings.editorShowLineNumbersHint")}
            </Text>
          </Flex>
          <Switch
            checked={settings.editorShowLineNumbers}
            aria-label={t("settings.editorShowLineNumbers")}
            onCheckedChange={(checked) => {
              updateMutation.mutate({ editor_show_line_numbers: checked });
            }}
          />
        </Flex>
      </Flex>
    </Box>
  );
}
