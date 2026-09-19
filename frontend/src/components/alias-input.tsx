import { Badge, Box, Button, Flex, Text, TextField } from "@radix-ui/themes";
import { Plus, Tag, X } from "lucide-react";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

const DEFAULT_MAX_ALIASES = 20;
const DEFAULT_MAX_ALIAS_CHARS = 100;

export interface AliasInputProps {
  aliases: string[];
  onChange: (aliases: string[]) => void;
  entityName: string;
  disabled?: boolean;
  maxAliases?: number;
  maxAliasLength?: number;
}

function normalizeForComparison(text: string): string {
  return text.trim().toLowerCase();
}

export function AliasInput({
  aliases,
  onChange,
  entityName,
  disabled = false,
  maxAliases = DEFAULT_MAX_ALIASES,
  maxAliasLength = DEFAULT_MAX_ALIAS_CHARS,
}: AliasInputProps) {
  const { t } = useTranslation();
  const [inputValue, setInputValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  const validateAndAdd = useCallback(
    (rawText: string) => {
      const text = rawText.trim();
      if (!text) {
        setError(t("aliases.empty"));
        return false;
      }

      if (text.length > maxAliasLength) {
        setError(t("aliases.tooLong", { max: maxAliasLength }));
        return false;
      }

      if (aliases.length >= maxAliases) {
        setError(t("aliases.maxReached", { max: maxAliases }));
        return false;
      }

      const normalized = normalizeForComparison(text);
      const normalizedEntityName = normalizeForComparison(entityName);

      if (normalized === normalizedEntityName) {
        setError(t("aliases.sameAsName"));
        return false;
      }

      const exists = aliases.some((existing) => normalizeForComparison(existing) === normalized);
      if (exists) {
        setError(t("aliases.duplicate"));
        return false;
      }

      setError(null);
      onChange([...aliases, text]);
      setInputValue("");
      return true;
    },
    [aliases, entityName, maxAliasLength, maxAliases, onChange, t],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (disabled) return;

      if (e.key === "Enter" || e.key === "," || e.key === "，") {
        e.preventDefault();
        validateAndAdd(inputValue);
      } else if (e.key === "Backspace" && inputValue === "" && aliases.length > 0) {
        e.preventDefault();
        onChange(aliases.slice(0, -1));
        setError(null);
      }
    },
    [aliases, disabled, inputValue, onChange, validateAndAdd],
  );

  const handleRemove = useCallback(
    (indexToRemove: number) => {
      if (disabled) return;
      onChange(aliases.filter((_, idx) => idx !== indexToRemove));
      setError(null);
    },
    [aliases, disabled, onChange],
  );

  const handleClearAll = useCallback(() => {
    if (disabled || aliases.length === 0) return;
    onChange([]);
    setError(null);
  }, [aliases.length, disabled, onChange]);

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setInputValue(e.target.value);
    setError(null);
  }, []);

  return (
    <Box
      py="2"
      className="alias-input-container"
    >
      <Flex
        direction="column"
        gap="2"
      >
        <Flex
          align="center"
          justify="between"
        >
          <Flex
            align="center"
            gap="2"
          >
            <Tag
              size={14}
              style={{ color: "var(--gray-9)" }}
            />
            <Text
              size="2"
              weight="medium"
              color="gray"
            >
              {t("aliases.label")}
            </Text>
            <Text
              size="1"
              color={aliases.length >= maxAliases ? "amber" : "gray"}
            >
              {`(${aliases.length}/${maxAliases})`}
            </Text>
          </Flex>

          {aliases.length > 0 && !disabled && (
            <Button
              type="button"
              variant="ghost"
              color="gray"
              size="1"
              onClick={handleClearAll}
              style={{ cursor: "pointer" }}
            >
              <Text size="1">{t("aliases.clearAll")}</Text>
            </Button>
          )}
        </Flex>

        {aliases.length > 0 && (
          <Flex
            wrap="wrap"
            gap="2"
            align="center"
          >
            {aliases.map((alias, index) => (
              <Badge
                key={`${alias}-${index}`}
                size="2"
                variant="surface"
                color="gray"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  paddingRight: disabled ? undefined : 4,
                }}
              >
                <span>{alias}</span>
                {!disabled && (
                  <button
                    type="button"
                    aria-label={t("aliases.remove", { alias })}
                    onClick={(e) => {
                      e.stopPropagation();
                      handleRemove(index);
                    }}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                      border: "none",
                      background: "transparent",
                      color: "inherit",
                      cursor: "pointer",
                      padding: 0,
                      borderRadius: "50%",
                      opacity: 0.7,
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.opacity = "1";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.opacity = "0.7";
                    }}
                  >
                    <X size={12} />
                  </button>
                )}
              </Badge>
            ))}
          </Flex>
        )}

        {!disabled && (
          <Flex
            gap="2"
            align="center"
          >
            <Box style={{ flex: 1, maxWidth: 360 }}>
              <TextField.Root
                size="1"
                value={inputValue}
                onChange={handleInputChange}
                onKeyDown={handleKeyDown}
                placeholder={
                  aliases.length >= maxAliases
                    ? t("aliases.maxReached", { max: maxAliases })
                    : t("aliases.placeholder")
                }
                disabled={disabled || aliases.length >= maxAliases}
              />
            </Box>
            <Button
              type="button"
              size="1"
              variant="soft"
              disabled={disabled || !inputValue.trim() || aliases.length >= maxAliases}
              onClick={() => validateAndAdd(inputValue)}
              style={{ cursor: "pointer" }}
            >
              <Plus size={14} />
              {t("aliases.add")}
            </Button>
          </Flex>
        )}

        {error && (
          <Text
            size="1"
            color="red"
            weight="medium"
          >
            {error}
          </Text>
        )}
      </Flex>
    </Box>
  );
}
