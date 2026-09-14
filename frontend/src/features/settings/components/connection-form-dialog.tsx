import { zodResolver } from "@hookform/resolvers/zod";
import {
  Badge,
  Box,
  Button,
  Callout,
  Dialog,
  Flex,
  SegmentedControl,
  Text,
  TextArea,
  TextField,
} from "@radix-ui/themes";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, Check, Component, FileUp, Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFieldArray, useForm, Controller, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { ProviderIdSelect, Spinner } from "@/components";
import type { ModelProvider, ModelProviderCatalogProvider } from "@/lib/model.types";

import { fetchModelProviderCatalogModels, validateProvider } from "../lib/model-api";
import { ProviderIcon } from "../lib/provider-icons";
import { getProviderUrl, isCustomProviderType } from "../lib/provider-utils";

import "./connection-form-dialog.css";

const VERTEX_PROVIDER_TYPE = "google-vertex";
const VERTEX_ANTHROPIC_PROVIDER_TYPE = "google-vertex-anthropic";

const VERTEX_LOCATION_SUGGESTIONS = [
  "us-central1",
  "us-east4",
  "us-west1",
  "europe-west1",
  "asia-east1",
  "global",
];

const DEFAULT_VERTEX_MODELS = [
  "gemini-2.5-flash",
  "gemini-2.5-pro",
  "gemini-1.5-flash",
  "gemini-1.5-pro",
];

function requiresProviderUrl(
  providerType: string,
  catalogProviders?: ModelProviderCatalogProvider[],
): boolean {
  if (providerType === VERTEX_PROVIDER_TYPE) {
    return false;
  }
  return Boolean(providerType) && !getProviderUrl(providerType, catalogProviders);
}

const connectionSchema = z
  .object({
    name: z.string().optional(),
    url: z.string().optional(),
    apiKey: z.string().optional(),
    providerType: z.string().min(1, "providerTypeRequired"),
    customHeaders: z.array(
      z.object({
        key: z.string(),
        value: z.string(),
      }),
    ),
    // Vertex 专属字段
    projectId: z.string().optional(),
    location: z.string().optional(),
    authMode: z.enum(["adc", "service_account"]).optional(),
    serviceAccountJson: z.string().optional(),
    credentialsAction: z.enum(["keep", "replace", "clear"]).optional(),
    testModelId: z.string().optional(),
  })
  .superRefine((data, ctx) => {
    if (data.providerType === VERTEX_PROVIDER_TYPE) {
      if (!data.projectId || !data.projectId.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "projectIdRequired",
          path: ["projectId"],
        });
      }
      if (!data.location || !data.location.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "locationRequired",
          path: ["location"],
        });
      }
    }
  });

type ConnectionFormData = z.infer<typeof connectionSchema>;

const MASKED_CUSTOM_HEADER_VALUE = "••••••••";

function serializeCustomHeaders(headers: ConnectionFormData["customHeaders"]) {
  return headers
    .filter((header) => header.key.trim())
    .map((header) => ({
      key: header.key,
      value: header.value === MASKED_CUSTOM_HEADER_VALUE ? "" : header.value,
    }));
}

interface ConnectionFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connection?: ModelProvider;
  catalogProviders?: ModelProviderCatalogProvider[];
  isCatalogLoading?: boolean;
  onSubmit: (data: FormData) => Promise<void>;
  isSubmitting: boolean;
  isAgentSettingsLocked: boolean;
}

export function ConnectionFormDialog({
  open,
  onOpenChange,
  connection,
  catalogProviders,
  isCatalogLoading = false,
  onSubmit,
  isSubmitting,
  isAgentSettingsLocked,
}: ConnectionFormDialogProps) {
  const { t } = useTranslation();
  const isEditing = !!connection;

  const [validationStatus, setValidationStatus] = useState<
    "idle" | "validating" | "success" | "error"
  >("idle");
  const [validationMessage, setValidationMessage] = useState<string | null>(null);
  const [validationErrorCode, setValidationErrorCode] = useState<string | null>(null);
  const [validationScope, setValidationScope] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const initialFormValues = useMemo<ConnectionFormData>(() => {
    if (connection) {
      const isVertex = connection.providerType === VERTEX_PROVIDER_TYPE;
      const vertexConfig = connection.providerConfig || {};
      return {
        name: connection.name || "",
        url: connection.url || "",
        apiKey: "",
        providerType: connection.providerType || "",
        customHeaders: (connection.customHeaderNames || []).map((key) => ({
          key,
          value: MASKED_CUSTOM_HEADER_VALUE,
        })),
        projectId: (vertexConfig.project_id as string) || "",
        location: (vertexConfig.location as string) || (isVertex ? "us-central1" : ""),
        authMode:
          (vertexConfig.auth_mode as string) === "service_account" ? "service_account" : "adc",
        serviceAccountJson: "",
        credentialsAction: isVertex && connection.hasCredentials ? "keep" : "replace",
        testModelId: "gemini-2.5-flash",
      };
    }
    return {
      name: "",
      url: "",
      apiKey: "",
      providerType: "",
      customHeaders: [],
      projectId: "",
      location: "us-central1",
      authMode: "adc",
      serviceAccountJson: "",
      credentialsAction: "replace",
      testModelId: "gemini-2.5-flash",
    };
  }, [connection]);

  const {
    control,
    handleSubmit,
    formState: { errors },
    getValues,
    reset,
    setValue,
  } = useForm<ConnectionFormData>({
    resolver: zodResolver(connectionSchema),
    values: initialFormValues,
  });

  const {
    fields: customHeaderFields,
    append,
    remove,
  } = useFieldArray({
    control,
    name: "customHeaders",
  });

  const providerType = useWatch({ control, name: "providerType" });
  const url = useWatch({ control, name: "url" });
  const apiKey = useWatch({ control, name: "apiKey" });
  const projectId = useWatch({ control, name: "projectId" });
  const location = useWatch({ control, name: "location" });
  const authMode = useWatch({ control, name: "authMode" });
  const serviceAccountJson = useWatch({ control, name: "serviceAccountJson" });
  const credentialsAction = useWatch({ control, name: "credentialsAction" });
  const testModelId = useWatch({ control, name: "testModelId" });

  const isVertex = providerType === VERTEX_PROVIDER_TYPE;
  const isVertexAnthropic = providerType === VERTEX_ANTHROPIC_PROVIDER_TYPE;

  // 查询 Vertex 目录模型列表
  const { data: vertexCatalogData } = useQuery({
    queryKey: ["model-provider-catalog", "models", VERTEX_PROVIDER_TYPE, "llm"],
    queryFn: () => fetchModelProviderCatalogModels(VERTEX_PROVIDER_TYPE, "llm"),
    enabled: open && isVertex,
  });

  const availableVertexModels = useMemo(() => {
    const catalogList = (vertexCatalogData?.models || []).map((m) => m.id);
    const combined = [...new Set([...catalogList, ...DEFAULT_VERTEX_MODELS])];
    return combined;
  }, [vertexCatalogData]);

  const selectedCatalogProvider = useMemo(
    () => catalogProviders?.find((provider) => provider.providerType === providerType),
    [catalogProviders, providerType],
  );
  const providerIconPath = selectedCatalogProvider?.iconPath || connection?.iconPath;

  // 切换到非 Vertex 且需要用户提供地址的提供商时，只清空一次 URL
  useEffect(() => {
    if (!providerType || isVertex) return;

    if (requiresProviderUrl(providerType, catalogProviders)) {
      if (!isEditing || connection?.providerType !== providerType) {
        setValue("url", "");
      }
    }
  }, [providerType, isVertex, catalogProviders, setValue, isEditing, connection]);

  // 当非 Vertex 提供商类型改变时，自动设置固定 URL
  useEffect(() => {
    if (!providerType || isVertex || requiresProviderUrl(providerType, catalogProviders)) {
      return;
    }

    const fixedUrl = getProviderUrl(providerType, catalogProviders);
    if (fixedUrl) {
      if (!isEditing) {
        setValue("url", fixedUrl);
      } else {
        const currentUrl = url;
        const oldFixedUrl = connection?.providerType
          ? getProviderUrl(connection.providerType, catalogProviders)
          : null;

        if (!currentUrl || currentUrl.trim() === "" || currentUrl === oldFixedUrl) {
          setValue("url", fixedUrl);
        }
      }
    }
  }, [providerType, isVertex, setValue, isEditing, url, connection, catalogProviders]);

  // 当切换到 Vertex 时，补充默认建议区域及测试模型
  useEffect(() => {
    if (isVertex) {
      if (!location) {
        setValue("location", "us-central1");
      }
      if (!authMode) {
        setValue("authMode", "adc");
      }
      if (!testModelId) {
        setValue("testModelId", "gemini-2.5-flash");
      }
    }
  }, [isVertex, location, authMode, testModelId, setValue]);

  // 草稿发生变化时，使上一次验证状态失效
  const prevDraftRef = useRef({
    projectId,
    location,
    authMode,
    serviceAccountJson,
    credentialsAction,
    testModelId,
    apiKey,
    url,
    providerType,
  });

  useEffect(() => {
    const prev = prevDraftRef.current;
    if (
      prev.projectId !== projectId ||
      prev.location !== location ||
      prev.authMode !== authMode ||
      prev.serviceAccountJson !== serviceAccountJson ||
      prev.credentialsAction !== credentialsAction ||
      prev.testModelId !== testModelId ||
      prev.apiKey !== apiKey ||
      prev.url !== url ||
      prev.providerType !== providerType
    ) {
      prevDraftRef.current = {
        projectId,
        location,
        authMode,
        serviceAccountJson,
        credentialsAction,
        testModelId,
        apiKey,
        url,
        providerType,
      };
      if (validationStatus !== "idle") {
        setValidationStatus("idle");
        setValidationMessage(null);
        setValidationErrorCode(null);
        setValidationScope(null);
      }
    }
  }, [
    projectId,
    location,
    authMode,
    serviceAccountJson,
    credentialsAction,
    testModelId,
    apiKey,
    url,
    providerType,
    validationStatus,
  ]);

  // 处理 Service Account JSON 文件导入
  const handleFileUpload = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) return;

      setFileError(null);
      if (file.size > 64 * 1024) {
        setFileError(t("connections.serviceAccountFileSizeExceeded"));
        event.target.value = "";
        return;
      }

      const reader = new FileReader();
      reader.onload = (e) => {
        const text = e.target?.result;
        if (typeof text !== "string") return;

        try {
          const parsed = JSON.parse(text);
          if (
            parsed &&
            typeof parsed === "object" &&
            parsed.type === "service_account" &&
            parsed.project_id &&
            parsed.private_key &&
            parsed.client_email
          ) {
            setValue("serviceAccountJson", text, { shouldValidate: true });
            setValue("credentialsAction", "replace");
            setFileError(null);

            const currentProjectId = getValues("projectId");
            if (!currentProjectId || !currentProjectId.trim()) {
              setValue("projectId", parsed.project_id as string, { shouldValidate: true });
            }
          } else {
            setFileError(t("connections.serviceAccountInvalidJson"));
          }
        } catch {
          setFileError(t("connections.serviceAccountInvalidJson"));
        }
      };
      reader.readAsText(file, "utf-8");
      event.target.value = "";
    },
    [getValues, setValue, t],
  );

  // 验证连接
  const handleValidate = useCallback(async () => {
    const formData = getValues();

    if (!formData.providerType) {
      return;
    }

    // Vertex 验证分支
    if (formData.providerType === VERTEX_PROVIDER_TYPE) {
      if (
        !formData.projectId?.trim() ||
        !formData.location?.trim() ||
        !formData.testModelId?.trim()
      ) {
        setValidationStatus("error");
        setValidationMessage(t("connections.vertex_config_invalid"));
        return;
      }

      if (formData.authMode === "service_account") {
        if (isEditing && formData.credentialsAction === "keep" && !connection?.hasCredentials) {
          setValidationStatus("error");
          setValidationMessage(t("connections.vertex_credentials_missing"));
          return;
        }
        if (formData.credentialsAction === "replace" && !formData.serviceAccountJson?.trim()) {
          setValidationStatus("error");
          setValidationMessage(t("connections.serviceAccountJsonRequired"));
          return;
        }
        if (!isEditing && !formData.serviceAccountJson?.trim()) {
          setValidationStatus("error");
          setValidationMessage(t("connections.serviceAccountJsonRequired"));
          return;
        }
      }

      setValidationStatus("validating");
      setValidationMessage(null);
      setValidationErrorCode(null);
      setValidationScope(null);

      try {
        const configPayload = {
          project_id: formData.projectId.trim(),
          location: formData.location.trim(),
          auth_mode: formData.authMode || "adc",
        };
        const action =
          formData.credentialsAction ||
          (formData.authMode === "service_account" ? "replace" : "keep");
        const result = await validateProvider({
          provider_type: VERTEX_PROVIDER_TYPE,
          url: "",
          api_key: "",
          provider_config: JSON.stringify(configPayload),
          credentials_action: action,
          service_account_json:
            action === "replace" && formData.serviceAccountJson?.trim()
              ? formData.serviceAccountJson.trim()
              : undefined,
          model_id: formData.testModelId.trim(),
          provider_id: isEditing ? connection?.id : undefined,
        });

        if (result.success) {
          setValidationStatus("success");
          setValidationScope(result.validation_scope || null);
          setValidationMessage(result.message || null);
        } else {
          setValidationStatus("error");
          setValidationErrorCode(result.error_code || null);
          setValidationMessage(result.message || null);
        }
      } catch (err: unknown) {
        setValidationStatus("error");
        const msg = err instanceof Error ? err.message : String(err);
        setValidationMessage(msg);
      }
      return;
    }

    // 标准提供商验证分支
    let validateUrl = formData.url;
    if (!validateUrl || validateUrl.trim() === "") {
      const fixedUrl = getProviderUrl(formData.providerType, catalogProviders);
      if (fixedUrl) {
        validateUrl = fixedUrl;
      } else {
        setValidationStatus("error");
        return;
      }
    }

    if (!isEditing && !formData.apiKey) {
      setValidationStatus("error");
      return;
    }

    setValidationStatus("validating");
    setValidationMessage(null);
    setValidationErrorCode(null);

    try {
      const result = await validateProvider({
        provider_type: formData.providerType,
        url: validateUrl,
        api_key: formData.apiKey || "",
        custom_headers: isCustomProviderType(formData.providerType)
          ? serializeCustomHeaders(formData.customHeaders)
          : [],
      });

      if (result.success) {
        setValidationStatus("success");
      } else {
        setValidationStatus("error");
      }
    } catch {
      setValidationStatus("error");
    }
  }, [catalogProviders, connection, getValues, isEditing, t]);

  // 提交表单
  const onFormSubmit = useCallback(
    async (data: ConnectionFormData) => {
      // Vertex 连接提交
      if (data.providerType === VERTEX_PROVIDER_TYPE) {
        if (!data.projectId?.trim() || !data.location?.trim()) {
          return;
        }
        if (data.authMode === "service_account") {
          if (!isEditing && !data.serviceAccountJson?.trim()) {
            return;
          }
          if (
            isEditing &&
            data.credentialsAction === "replace" &&
            !data.serviceAccountJson?.trim()
          ) {
            return;
          }
          if (isEditing && !connection?.hasCredentials && data.credentialsAction === "keep") {
            return;
          }
        }

        const formData = new FormData();
        formData.append("name", data.name || "");
        formData.append("url", "");
        formData.append("provider_type", VERTEX_PROVIDER_TYPE);

        const config = {
          project_id: data.projectId.trim(),
          location: data.location.trim(),
          auth_mode: data.authMode || "adc",
        };
        formData.append("provider_config", JSON.stringify(config));

        const action =
          data.credentialsAction || (data.authMode === "service_account" ? "replace" : "keep");
        formData.append("credentials_action", action);

        if (action === "replace" && data.serviceAccountJson?.trim()) {
          formData.append("service_account_json", data.serviceAccountJson.trim());
        }

        await onSubmit(formData);
        reset();
        setValidationStatus("idle");
        setValidationMessage(null);
        setValidationErrorCode(null);
        setValidationScope(null);
        setFileError(null);
        return;
      }

      // 标准提供商提交
      if (!isEditing && !data.apiKey?.trim()) {
        return;
      }

      const formData = new FormData();
      formData.append("name", data.name || "");

      let finalUrl = data.url;
      if (!finalUrl || finalUrl.trim() === "") {
        const fixedUrl = getProviderUrl(data.providerType, catalogProviders);
        if (fixedUrl) {
          finalUrl = fixedUrl;
        }
      }

      if (!finalUrl) {
        setValidationStatus("error");
        return;
      }

      formData.append("url", finalUrl);
      formData.append("provider_type", data.providerType);

      if (data.apiKey) {
        formData.append("api_key", data.apiKey);
      }

      if (isCustomProviderType(data.providerType)) {
        formData.append(
          "custom_headers",
          JSON.stringify(serializeCustomHeaders(data.customHeaders)),
        );
      }

      await onSubmit(formData);
      reset();
      setValidationStatus("idle");
    },
    [catalogProviders, connection, isEditing, onSubmit, reset],
  );

  const handleOpenChange = useCallback(
    (newOpen: boolean) => {
      if (!newOpen) {
        setValidationStatus("idle");
        setValidationMessage(null);
        setValidationErrorCode(null);
        setValidationScope(null);
        setFileError(null);
        reset();
      }
      onOpenChange(newOpen);
    },
    [onOpenChange, reset],
  );

  const canValidate = useMemo(() => {
    if (!providerType) return false;
    if (isVertex) {
      if (!projectId || !projectId.trim()) return false;
      if (!location || !location.trim()) return false;
      if (!testModelId || !testModelId.trim()) return false;
      if (authMode === "service_account") {
        if (isEditing && credentialsAction === "keep") {
          if (!connection?.hasCredentials) return false;
        } else if (credentialsAction === "replace") {
          if (!serviceAccountJson || !serviceAccountJson.trim()) return false;
        } else if (credentialsAction === "clear") {
          return false;
        } else {
          if (!serviceAccountJson || !serviceAccountJson.trim()) return false;
        }
      }
      return true;
    }
    if (requiresProviderUrl(providerType, catalogProviders) && (!url || !url.trim())) return false;
    if (!isEditing && !apiKey) return false;
    return true;
  }, [
    providerType,
    isVertex,
    projectId,
    location,
    testModelId,
    authMode,
    isEditing,
    credentialsAction,
    connection,
    serviceAccountJson,
    catalogProviders,
    url,
    apiKey,
  ]);

  const canSubmit = useMemo(() => {
    if (isAgentSettingsLocked || isSubmitting) return false;
    if (isVertex) {
      if (!projectId || !projectId.trim()) return false;
      if (!location || !location.trim()) return false;
      if (authMode === "service_account") {
        if (isEditing && credentialsAction === "keep") {
          if (!connection?.hasCredentials) return false;
        } else if (credentialsAction === "replace") {
          if (!serviceAccountJson || !serviceAccountJson.trim()) return false;
        } else if (credentialsAction === "clear") {
          return false;
        } else {
          if (!serviceAccountJson || !serviceAccountJson.trim()) return false;
        }
      }
      return true;
    }
    if (!isEditing && !apiKey?.trim()) return false;
    return true;
  }, [
    isAgentSettingsLocked,
    isSubmitting,
    isVertex,
    projectId,
    location,
    authMode,
    isEditing,
    credentialsAction,
    connection,
    serviceAccountJson,
    apiKey,
  ]);

  return (
    <Dialog.Root
      open={open}
      onOpenChange={handleOpenChange}
    >
      <Dialog.Content maxWidth="520px">
        <Dialog.Title>
          {isEditing ? t("connections.editConnection") : t("connections.createConnection")}
        </Dialog.Title>

        <form onSubmit={handleSubmit(onFormSubmit)}>
          <Flex
            direction="column"
            gap="4"
            mt="4"
          >
            {/* 第一行：目录图标和基本信息 */}
            <Flex
              gap="3"
              align="center"
            >
              <Box
                style={{
                  width: 80,
                  height: 80,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  borderRadius: "var(--radius-2)",
                  background: "var(--gray-a3)",
                }}
              >
                {providerIconPath ? (
                  <ProviderIcon
                    iconPath={providerIconPath}
                    size={40}
                  />
                ) : isCustomProviderType(providerType) ? (
                  <Component
                    size={40}
                    aria-hidden="true"
                  />
                ) : null}
              </Box>

              {/* 右侧：备注名称和提供商类型 */}
              <Flex
                direction="column"
                gap="3"
                style={{ flex: 1 }}
              >
                {/* 备注名称 */}
                <Flex
                  direction="column"
                  gap="2"
                >
                  <Text
                    size="2"
                    weight="medium"
                    color="gray"
                  >
                    {t("connections.name")}
                  </Text>
                  <Controller
                    name="name"
                    control={control}
                    render={({ field }) => (
                      <TextField.Root
                        {...field}
                        placeholder={t("connections.namePlaceholder")}
                        disabled={isAgentSettingsLocked}
                      />
                    )}
                  />
                </Flex>

                {/* 提供商类型 */}
                <Flex
                  direction="column"
                  gap="2"
                >
                  <Text
                    size="2"
                    weight="medium"
                    color="gray"
                  >
                    {t("connections.providerType")}{" "}
                    <Text
                      color="red"
                      style={{ display: "inline" }}
                    >
                      *
                    </Text>
                  </Text>
                  <Controller
                    name="providerType"
                    control={control}
                    render={({ field }) => (
                      <ProviderIdSelect
                        value={field.value}
                        onChange={field.onChange}
                        providers={catalogProviders ?? []}
                        placeholder={t("connections.providerTypePlaceholder")}
                        disabled={isAgentSettingsLocked || isEditing}
                      />
                    )}
                  />
                  {errors.providerType && (
                    <Text
                      size="1"
                      color="red"
                    >
                      {t(`connections.${errors.providerType.message}`)}
                    </Text>
                  )}
                  {!isEditing && isCatalogLoading && (
                    <Text
                      size="1"
                      color="gray"
                    >
                      {t("connections.catalogLoading")}
                    </Text>
                  )}
                </Flex>
              </Flex>
            </Flex>

            {/* Anthropic on Vertex 尚未原生支持提示 */}
            {isVertexAnthropic && (
              <Callout.Root
                color="amber"
                size="1"
              >
                <Callout.Icon>
                  <AlertCircle size={16} />
                </Callout.Icon>
                <Callout.Text size="2">{t("connections.vertexAnthropicNotice")}</Callout.Text>
              </Callout.Root>
            )}

            {/* Vertex 专属字段 */}
            {isVertex ? (
              <>
                {/* Project ID */}
                <Flex
                  direction="column"
                  gap="2"
                >
                  <Text
                    size="2"
                    weight="medium"
                    color="gray"
                  >
                    {t("connections.projectId")}{" "}
                    <Text
                      color="red"
                      style={{ display: "inline" }}
                    >
                      *
                    </Text>
                  </Text>
                  <Controller
                    name="projectId"
                    control={control}
                    render={({ field }) => (
                      <TextField.Root
                        {...field}
                        placeholder={t("connections.projectIdPlaceholder")}
                        disabled={isAgentSettingsLocked}
                      />
                    )}
                  />
                  {errors.projectId && (
                    <Text
                      size="1"
                      color="red"
                    >
                      {t(`connections.${errors.projectId.message}`)}
                    </Text>
                  )}
                </Flex>

                {/* Location */}
                <Flex
                  direction="column"
                  gap="2"
                >
                  <Text
                    size="2"
                    weight="medium"
                    color="gray"
                  >
                    {t("connections.location")}{" "}
                    <Text
                      color="red"
                      style={{ display: "inline" }}
                    >
                      *
                    </Text>
                  </Text>
                  <Controller
                    name="location"
                    control={control}
                    render={({ field }) => (
                      <TextField.Root
                        {...field}
                        placeholder={t("connections.locationPlaceholder")}
                        disabled={isAgentSettingsLocked}
                      />
                    )}
                  />
                  <Flex
                    gap="1"
                    wrap="wrap"
                    align="center"
                  >
                    {VERTEX_LOCATION_SUGGESTIONS.map((loc) => (
                      <Button
                        key={loc}
                        type="button"
                        variant={location === loc ? "solid" : "soft"}
                        size="1"
                        color="gray"
                        onClick={() => setValue("location", loc, { shouldValidate: true })}
                        disabled={isAgentSettingsLocked}
                        style={{ cursor: "pointer" }}
                      >
                        {loc}
                      </Button>
                    ))}
                  </Flex>
                  <Text
                    size="1"
                    color="gray"
                  >
                    {t("connections.locationHint")}
                  </Text>
                  {errors.location && (
                    <Text
                      size="1"
                      color="red"
                    >
                      {t(`connections.${errors.location.message}`)}
                    </Text>
                  )}
                </Flex>

                {/* Auth Mode */}
                <Flex
                  direction="column"
                  gap="2"
                >
                  <Text
                    size="2"
                    weight="medium"
                    color="gray"
                  >
                    {t("connections.authMode")}{" "}
                    <Text
                      color="red"
                      style={{ display: "inline" }}
                    >
                      *
                    </Text>
                  </Text>
                  <Controller
                    name="authMode"
                    control={control}
                    render={({ field }) => (
                      <SegmentedControl.Root
                        value={field.value || "adc"}
                        onValueChange={(val) => {
                          field.onChange(val);
                          if (val === "adc") {
                            setValue("credentialsAction", "clear");
                          } else {
                            if (isEditing && connection?.hasCredentials) {
                              setValue("credentialsAction", "keep");
                            } else {
                              setValue("credentialsAction", "replace");
                            }
                          }
                        }}
                        disabled={isAgentSettingsLocked}
                      >
                        <SegmentedControl.Item value="adc">
                          {t("connections.authModeAdc")}
                        </SegmentedControl.Item>
                        <SegmentedControl.Item value="service_account">
                          {t("connections.authModeServiceAccount")}
                        </SegmentedControl.Item>
                      </SegmentedControl.Root>
                    )}
                  />
                </Flex>

                {/* ADC 说明 */}
                {authMode === "adc" && (
                  <Callout.Root
                    color="blue"
                    size="1"
                  >
                    <Callout.Icon>
                      <ShieldCheck size={16} />
                    </Callout.Icon>
                    <Callout.Text size="2">{t("connections.adcNotice")}</Callout.Text>
                  </Callout.Root>
                )}

                {/* Service Account 凭据区 */}
                {authMode === "service_account" && (
                  <>
                    {isEditing && connection?.hasCredentials && credentialsAction === "keep" ? (
                      <Box
                        style={{
                          padding: "var(--space-3)",
                          borderRadius: "var(--radius-2)",
                          background: "var(--gray-a3)",
                          border: "1px solid var(--gray-a5)",
                        }}
                      >
                        <Flex
                          justify="between"
                          align="center"
                        >
                          <Flex
                            align="center"
                            gap="2"
                          >
                            <ShieldCheck
                              size={20}
                              color="var(--green-9)"
                            />
                            <Flex
                              direction="column"
                              gap="1"
                            >
                              <Flex
                                align="center"
                                gap="2"
                              >
                                <Text
                                  size="2"
                                  weight="medium"
                                >
                                  {t("connections.serviceAccountJson")}
                                </Text>
                                <Badge
                                  size="1"
                                  color="green"
                                  variant="soft"
                                >
                                  {t("connections.credentialsConfigured")}
                                </Badge>
                              </Flex>
                              <Text
                                size="1"
                                color="gray"
                              >
                                {t("connections.serviceAccountSavedNotice")}
                              </Text>
                            </Flex>
                          </Flex>
                          <Flex gap="2">
                            <Button
                              type="button"
                              variant="soft"
                              size="1"
                              color="gray"
                              onClick={() => setValue("credentialsAction", "replace")}
                              disabled={isAgentSettingsLocked}
                            >
                              {t("connections.replaceCredentials")}
                            </Button>
                            <Button
                              type="button"
                              variant="soft"
                              size="1"
                              color="red"
                              onClick={() => setValue("credentialsAction", "clear")}
                              disabled={isAgentSettingsLocked}
                            >
                              {t("connections.clearCredentials")}
                            </Button>
                          </Flex>
                        </Flex>
                      </Box>
                    ) : credentialsAction === "clear" ? (
                      <Box
                        style={{
                          padding: "var(--space-3)",
                          borderRadius: "var(--radius-2)",
                          background: "var(--red-a2)",
                          border: "1px solid var(--red-a4)",
                        }}
                      >
                        <Flex
                          justify="between"
                          align="center"
                        >
                          <Text
                            size="2"
                            color="red"
                          >
                            {t("connections.clearCredentials")} (
                            {t("connections.serviceAccountSavedNotice")})
                          </Text>
                          {isEditing && connection?.hasCredentials && (
                            <Button
                              type="button"
                              variant="soft"
                              size="1"
                              color="gray"
                              onClick={() => setValue("credentialsAction", "keep")}
                              disabled={isAgentSettingsLocked}
                            >
                              {t("connections.keepCredentials")}
                            </Button>
                          )}
                        </Flex>
                      </Box>
                    ) : (
                      <Flex
                        direction="column"
                        gap="2"
                      >
                        <Flex
                          justify="between"
                          align="center"
                        >
                          <Text
                            size="2"
                            weight="medium"
                            color="gray"
                          >
                            {t("connections.serviceAccountJson")}{" "}
                            <Text
                              color="red"
                              style={{ display: "inline" }}
                            >
                              *
                            </Text>
                          </Text>
                          <Flex
                            gap="2"
                            align="center"
                          >
                            <input
                              type="file"
                              ref={fileInputRef}
                              accept=".json,application/json"
                              style={{ display: "none" }}
                              onChange={handleFileUpload}
                            />
                            <Button
                              type="button"
                              variant="soft"
                              size="1"
                              onClick={() => fileInputRef.current?.click()}
                              disabled={isAgentSettingsLocked}
                            >
                              <FileUp size={14} />
                              {t("connections.importJsonFile")}
                            </Button>
                            {isEditing && connection?.hasCredentials && (
                              <Button
                                type="button"
                                variant="ghost"
                                size="1"
                                color="gray"
                                onClick={() => {
                                  setValue("credentialsAction", "keep");
                                  setValue("serviceAccountJson", "");
                                  setFileError(null);
                                }}
                                disabled={isAgentSettingsLocked}
                              >
                                {t("connections.cancelReplace")}
                              </Button>
                            )}
                          </Flex>
                        </Flex>
                        <Controller
                          name="serviceAccountJson"
                          control={control}
                          render={({ field }) => (
                            <TextArea
                              {...field}
                              rows={4}
                              placeholder={t("connections.serviceAccountJsonPlaceholder")}
                              disabled={isAgentSettingsLocked}
                              className="connection-form-dialog__textarea"
                            />
                          )}
                        />
                        {fileError && (
                          <Text
                            size="1"
                            color="red"
                          >
                            {fileError}
                          </Text>
                        )}
                        <Text
                          size="1"
                          color="gray"
                        >
                          {t("connections.serviceAccountNotice")}
                        </Text>
                      </Flex>
                    )}
                  </>
                )}

                {/* Vertex 验证用测试模型 */}
                <Box
                  style={{
                    padding: "var(--space-3)",
                    borderRadius: "var(--radius-2)",
                    background: "var(--gray-a2)",
                    border: "1px solid var(--gray-a4)",
                  }}
                >
                  <Flex
                    direction="column"
                    gap="2"
                  >
                    <Text
                      size="2"
                      weight="medium"
                      color="gray"
                    >
                      {t("connections.testModel")}{" "}
                      <Text
                        color="red"
                        style={{ display: "inline" }}
                      >
                        *
                      </Text>
                    </Text>
                    <Controller
                      name="testModelId"
                      control={control}
                      render={({ field }) => (
                        <TextField.Root
                          {...field}
                          placeholder={t("connections.testModelPlaceholder")}
                          disabled={isAgentSettingsLocked}
                        />
                      )}
                    />
                    <Flex
                      gap="1"
                      wrap="wrap"
                      align="center"
                    >
                      {availableVertexModels.slice(0, 4).map((model) => (
                        <Button
                          key={model}
                          type="button"
                          variant={testModelId === model ? "solid" : "soft"}
                          size="1"
                          color="gray"
                          onClick={() => setValue("testModelId", model, { shouldValidate: true })}
                          disabled={isAgentSettingsLocked}
                          style={{ cursor: "pointer" }}
                        >
                          {model}
                        </Button>
                      ))}
                    </Flex>
                    <Text
                      size="1"
                      color="gray"
                    >
                      {t("connections.validationNotice")}
                    </Text>
                  </Flex>
                </Box>
              </>
            ) : (
              /* 非 Vertex 传统提供商字段 */
              <>
                {requiresProviderUrl(providerType, catalogProviders) && (
                  <Flex
                    direction="column"
                    gap="2"
                  >
                    <Text
                      size="2"
                      weight="medium"
                      color="gray"
                    >
                      {t("connections.url")}{" "}
                      <Text
                        color="red"
                        style={{ display: "inline" }}
                      >
                        *
                      </Text>
                    </Text>
                    <Controller
                      name="url"
                      control={control}
                      render={({ field }) => (
                        <TextField.Root
                          {...field}
                          placeholder={t("connections.urlPlaceholder")}
                          disabled={isAgentSettingsLocked}
                        />
                      )}
                    />
                    {errors.url && (
                      <Text
                        size="1"
                        color="red"
                      >
                        {t(`connections.${errors.url.message}`)}
                      </Text>
                    )}
                  </Flex>
                )}

                {/* API Key */}
                <Flex
                  direction="column"
                  gap="2"
                >
                  <Text
                    size="2"
                    weight="medium"
                    color="gray"
                  >
                    {t("connections.apiKey")}
                    {!isEditing && (
                      <Text
                        color="red"
                        style={{ display: "inline" }}
                      >
                        {" "}
                        *
                      </Text>
                    )}
                  </Text>
                  {isEditing ? (
                    <Box>
                      <TextField.Root
                        value={apiKey || ""}
                        onChange={(e) => {
                          const form = getValues();
                          reset({
                            ...form,
                            apiKey: e.target.value,
                          });
                        }}
                        type="password"
                        placeholder={
                          apiKey ? t("connections.apiKeyPlaceholderEdit") : "••••••••••••••••"
                        }
                        disabled={isAgentSettingsLocked}
                      />
                      <Text
                        size="1"
                        color="gray"
                        mt="1"
                      >
                        {t("connections.apiKeyEditHint")}
                      </Text>
                    </Box>
                  ) : (
                    <Controller
                      name="apiKey"
                      control={control}
                      render={({ field }) => (
                        <TextField.Root
                          {...field}
                          type="password"
                          placeholder={t("connections.apiKeyPlaceholder")}
                          disabled={isAgentSettingsLocked}
                        />
                      )}
                    />
                  )}
                  {errors.apiKey && (
                    <Text
                      size="1"
                      color="red"
                    >
                      {t(`connections.${errors.apiKey.message}`)}
                    </Text>
                  )}
                </Flex>

                {isCustomProviderType(providerType) && (
                  <Flex
                    direction="column"
                    gap="2"
                  >
                    <Flex
                      align="center"
                      justify="between"
                    >
                      <Text
                        size="2"
                        weight="medium"
                        color="gray"
                      >
                        {t("connections.customHeaders")}
                      </Text>
                      <Button
                        type="button"
                        variant="ghost"
                        size="2"
                        className="connection-form-dialog__header-action"
                        aria-label={t("connections.addCustomHeader")}
                        onClick={() => append({ key: "", value: "" })}
                        disabled={isAgentSettingsLocked}
                      >
                        <Plus size={16} />
                      </Button>
                    </Flex>
                    <Box className="connection-form-dialog__headers-list">
                      <Flex
                        direction="column"
                        gap="2"
                      >
                        {customHeaderFields.map((field, index) => (
                          <Flex
                            key={field.id}
                            className="connection-form-dialog__header-row"
                            align="center"
                            gap="2"
                          >
                            <Controller
                              name={`customHeaders.${index}.key`}
                              control={control}
                              render={({ field: inputField }) => (
                                <TextField.Root
                                  {...inputField}
                                  className="connection-form-dialog__header-input"
                                  placeholder={t("connections.customHeaderKeyPlaceholder")}
                                  disabled={isAgentSettingsLocked}
                                />
                              )}
                            />
                            <Controller
                              name={`customHeaders.${index}.value`}
                              control={control}
                              render={({ field: inputField }) => (
                                <TextField.Root
                                  {...inputField}
                                  className="connection-form-dialog__header-input"
                                  placeholder={t("connections.customHeaderValuePlaceholder")}
                                  disabled={isAgentSettingsLocked}
                                />
                              )}
                            />
                            <Button
                              type="button"
                              variant="ghost"
                              size="2"
                              className="connection-form-dialog__header-action connection-form-dialog__header-action--delete"
                              aria-label={t("connections.removeCustomHeader")}
                              onClick={() => remove(index)}
                              disabled={isAgentSettingsLocked}
                            >
                              <Trash2 size={16} />
                            </Button>
                          </Flex>
                        ))}
                      </Flex>
                    </Box>
                  </Flex>
                )}
              </>
            )}

            {/* 验证反馈横幅 */}
            {validationStatus === "success" && (
              <Callout.Root
                color="green"
                size="1"
              >
                <Callout.Icon>
                  <Check size={16} />
                </Callout.Icon>
                <Callout.Text size="2">
                  {validationScope === "model_invocation"
                    ? t("connections.validationScopeNotice")
                    : validationMessage || t("connections.validateSuccess")}
                </Callout.Text>
              </Callout.Root>
            )}
            {validationStatus === "error" && (
              <Callout.Root
                color="red"
                size="1"
              >
                <Callout.Icon>
                  <AlertCircle size={16} />
                </Callout.Icon>
                <Callout.Text size="2">
                  {validationErrorCode &&
                  t(`connections.${validationErrorCode}`, { defaultValue: "" })
                    ? t(`connections.${validationErrorCode}`)
                    : validationMessage || t("connections.validateFailed")}
                </Callout.Text>
              </Callout.Root>
            )}

            {/* 操作按钮 */}
            <Flex
              gap="3"
              mt="2"
              justify="between"
            >
              <Button
                type="button"
                variant="soft"
                onClick={handleValidate}
                disabled={
                  isAgentSettingsLocked || !canValidate || validationStatus === "validating"
                }
                style={{
                  backgroundColor:
                    validationStatus === "success"
                      ? "var(--green-a3)"
                      : validationStatus === "error"
                        ? "var(--red-a3)"
                        : undefined,
                  color:
                    validationStatus === "success"
                      ? "var(--green-11)"
                      : validationStatus === "error"
                        ? "var(--red-11)"
                        : undefined,
                }}
              >
                {validationStatus === "validating" ? <Spinner size={18} /> : null}
                {validationStatus === "success" && <Check size={16} />}
                {validationStatus === "error" && <X size={16} />}
                {validationStatus === "validating"
                  ? t("connections.validating")
                  : validationStatus === "success"
                    ? t("connections.validateSuccess")
                    : validationStatus === "error"
                      ? t("connections.validateFailed")
                      : t("connections.validate")}
              </Button>

              <Flex gap="3">
                <Dialog.Close>
                  <Button
                    type="button"
                    variant="soft"
                    color="gray"
                    disabled={isSubmitting}
                  >
                    {t("common.cancel")}
                  </Button>
                </Dialog.Close>
                <Button
                  type="submit"
                  disabled={!canSubmit}
                >
                  {isSubmitting ? <Spinner size={18} /> : null}
                  {isEditing ? t("common.save") : t("common.create")}
                </Button>
              </Flex>
            </Flex>
          </Flex>
        </form>
      </Dialog.Content>
    </Dialog.Root>
  );
}
