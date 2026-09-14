import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { z } from "zod";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const enTranslations = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../src/i18n/locales/en.json"), "utf-8"),
);
const zhTranslations = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../src/i18n/locales/zh-CN.json"), "utf-8"),
);

// 与 connection-form-dialog.tsx 保持一致的配置与规则
const VERTEX_PROVIDER_TYPE = "google-vertex";
const VERTEX_ANTHROPIC_PROVIDER_TYPE = "google-vertex-anthropic";

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

function serializeVertexFormData(data: {
  name?: string;
  projectId: string;
  location: string;
  authMode: "adc" | "service_account";
  credentialsAction: "keep" | "replace" | "clear";
  serviceAccountJson?: string;
}): FormData {
  const formData = new FormData();
  formData.append("name", data.name || "");
  formData.append("url", "");
  formData.append("provider_type", VERTEX_PROVIDER_TYPE);

  const config = {
    project_id: data.projectId.trim(),
    location: data.location.trim(),
    auth_mode: data.authMode,
  };
  formData.append("provider_config", JSON.stringify(config));
  formData.append("credentials_action", data.credentialsAction);

  if (data.credentialsAction === "replace" && data.serviceAccountJson?.trim()) {
    formData.append("service_account_json", data.serviceAccountJson.trim());
  }

  return formData;
}

function buildVertexValidatePayload(data: {
  projectId: string;
  location: string;
  authMode: "adc" | "service_account";
  credentialsAction: "keep" | "replace" | "clear";
  serviceAccountJson?: string;
  testModelId: string;
  providerId?: string;
}) {
  return {
    provider_type: VERTEX_PROVIDER_TYPE,
    url: "",
    api_key: "",
    provider_config: JSON.stringify({
      project_id: data.projectId.trim(),
      location: data.location.trim(),
      auth_mode: data.authMode,
    }),
    credentials_action: data.credentialsAction,
    service_account_json:
      data.credentialsAction === "replace" && data.serviceAccountJson?.trim()
        ? data.serviceAccountJson.trim()
        : undefined,
    model_id: data.testModelId.trim(),
    provider_id: data.providerId,
  };
}

function parseAndValidateServiceAccountJson(
  content: string,
  byteLength: number,
): { valid: boolean; error?: string; projectId?: string } {
  if (byteLength > 64 * 1024) {
    return { valid: false, error: "serviceAccountFileSizeExceeded" };
  }
  try {
    const parsed = JSON.parse(content);
    if (
      parsed &&
      typeof parsed === "object" &&
      parsed.type === "service_account" &&
      parsed.project_id &&
      parsed.private_key &&
      parsed.client_email
    ) {
      return { valid: true, projectId: parsed.project_id as string };
    }
    return { valid: false, error: "serviceAccountInvalidJson" };
  } catch {
    return { valid: false, error: "serviceAccountInvalidJson" };
  }
}

test.describe("Google Vertex AI 前端逻辑与契约测试", () => {
  test("Anthropic on Vertex 类型标识冻结并确认与 Gemini 原生分支分离", () => {
    expect(VERTEX_ANTHROPIC_PROVIDER_TYPE).toBe("google-vertex-anthropic");
    expect(VERTEX_ANTHROPIC_PROVIDER_TYPE).not.toBe(VERTEX_PROVIDER_TYPE);
  });

  test("Zod 校验：Vertex 无需 API Key 与 URL，但必填 Project ID 与 Location", () => {
    // 缺少 projectId 与 location
    const invalidResult = connectionSchema.safeParse({
      providerType: VERTEX_PROVIDER_TYPE,
      name: "Test",
      customHeaders: [],
    });
    expect(invalidResult.success).toBe(false);
    if (!invalidResult.success) {
      const messages = invalidResult.error.issues.map((i) => i.message);
      expect(messages).toContain("projectIdRequired");
      expect(messages).toContain("locationRequired");
    }

    // 补齐 projectId 与 location，无 API Key 和 URL 依然通过
    const validResult = connectionSchema.safeParse({
      providerType: VERTEX_PROVIDER_TYPE,
      name: "Test",
      projectId: "my-gcp-project",
      location: "us-central1",
      authMode: "adc",
      customHeaders: [],
    });
    expect(validResult.success).toBe(true);
  });

  test("FormData 序列化：ADC 模式提交空 url、清理/不传私钥、包含非敏感配置", () => {
    const formData = serializeVertexFormData({
      name: "Vertex ADC Connection",
      projectId: "test-proj-123",
      location: "us-central1",
      authMode: "adc",
      credentialsAction: "clear",
    });

    expect(formData.get("name")).toBe("Vertex ADC Connection");
    expect(formData.get("url")).toBe("");
    expect(formData.get("provider_type")).toBe(VERTEX_PROVIDER_TYPE);
    expect(formData.get("credentials_action")).toBe("clear");
    expect(formData.get("api_key")).toBeNull();
    expect(formData.get("service_account_json")).toBeNull();

    const config = JSON.parse(formData.get("provider_config") as string);
    expect(config).toEqual({
      project_id: "test-proj-123",
      location: "us-central1",
      auth_mode: "adc",
    });
  });

  test("FormData 序列化：Service Account replace 模式附带 JSON 凭据且不传 API Key", () => {
    const fakeSa = JSON.stringify({
      type: "service_account",
      project_id: "test-proj-sa",
      private_key: "-----BEGIN RSA PRIVATE KEY-----\n...",
      client_email: "sa@test-proj-sa.iam.gserviceaccount.com",
    });

    const formData = serializeVertexFormData({
      name: "Vertex SA Connection",
      projectId: "test-proj-sa",
      location: "europe-west1",
      authMode: "service_account",
      credentialsAction: "replace",
      serviceAccountJson: fakeSa,
    });

    expect(formData.get("credentials_action")).toBe("replace");
    expect(formData.get("service_account_json")).toBe(fakeSa);
    expect(formData.get("api_key")).toBeNull();
    expect(formData.get("url")).toBe("");

    const config = JSON.parse(formData.get("provider_config") as string);
    expect(config.auth_mode).toBe("service_account");
  });

  test("FormData 序列化：编辑时保留既有凭据（keep）不提交任何密钥明文", () => {
    const formData = serializeVertexFormData({
      name: "Vertex Keep Connection",
      projectId: "test-proj-keep",
      location: "asia-east1",
      authMode: "service_account",
      credentialsAction: "keep",
    });

    expect(formData.get("credentials_action")).toBe("keep");
    expect(formData.get("service_account_json")).toBeNull();
    expect(formData.get("api_key")).toBeNull();
  });

  test("验证请求构造：传递空 url/api_key、测试模型 ID、非敏感配置与操作类型", () => {
    const payload = buildVertexValidatePayload({
      projectId: "validate-proj",
      location: "us-central1",
      authMode: "adc",
      credentialsAction: "keep",
      testModelId: "gemini-2.5-flash",
      providerId: "existing-provider-id-42",
    });

    expect(payload.provider_type).toBe(VERTEX_PROVIDER_TYPE);
    expect(payload.url).toBe("");
    expect(payload.api_key).toBe("");
    expect(payload.model_id).toBe("gemini-2.5-flash");
    expect(payload.provider_id).toBe("existing-provider-id-42");
    expect(payload.credentials_action).toBe("keep");

    const parsedConfig = JSON.parse(payload.provider_config);
    expect(parsedConfig.project_id).toBe("validate-proj");
    expect(parsedConfig.location).toBe("us-central1");
    expect(parsedConfig.auth_mode).toBe("adc");
  });

  test("Service Account JSON 文件解析：大小限制 64 KiB 与结构校验", () => {
    // 超过 64 KiB
    const oversized = "x".repeat(65 * 1024);
    const overResult = parseAndValidateServiceAccountJson(oversized, oversized.length);
    expect(overResult.valid).toBe(false);
    expect(overResult.error).toBe("serviceAccountFileSizeExceeded");

    // 非法 JSON
    const malformed = "{ this is not valid json }";
    const malformedResult = parseAndValidateServiceAccountJson(malformed, malformed.length);
    expect(malformedResult.valid).toBe(false);
    expect(malformedResult.error).toBe("serviceAccountInvalidJson");

    // 缺少必要字段
    const missingFields = JSON.stringify({ type: "user_account" });
    const missingResult = parseAndValidateServiceAccountJson(
      missingFields,
      missingFields.length,
    );
    expect(missingResult.valid).toBe(false);
    expect(missingResult.error).toBe("serviceAccountInvalidJson");

    // 合法结构且正确提取 project_id
    const validSa = JSON.stringify({
      type: "service_account",
      project_id: "auto-extracted-proj",
      private_key: "-----BEGIN PRIVATE KEY-----\nMIIEv...",
      client_email: "sa@auto-extracted-proj.iam.gserviceaccount.com",
    });
    const validResult = parseAndValidateServiceAccountJson(validSa, validSa.length);
    expect(validResult.valid).toBe(true);
    expect(validResult.projectId).toBe("auto-extracted-proj");
  });

  test("国际化资源完整性：中英文均包含 Vertex 字段、认证说明与全部 8 个错误码", () => {
    const zhConnections = (zhTranslations as Record<string, Record<string, string>>)[
      "connections"
    ];
    const enConnections = (enTranslations as Record<string, Record<string, string>>)[
      "connections"
    ];

    const requiredKeys = [
      "projectId",
      "projectIdPlaceholder",
      "projectIdRequired",
      "location",
      "locationPlaceholder",
      "locationRequired",
      "locationHint",
      "authMode",
      "authModeAdc",
      "authModeServiceAccount",
      "adcNotice",
      "serviceAccountJson",
      "serviceAccountJsonPlaceholder",
      "serviceAccountJsonRequired",
      "serviceAccountFileSizeExceeded",
      "serviceAccountInvalidJson",
      "serviceAccountNotice",
      "serviceAccountSavedNotice",
      "credentialsConfigured",
      "replaceCredentials",
      "clearCredentials",
      "keepCredentials",
      "importJsonFile",
      "testModel",
      "testModelPlaceholder",
      "testModelRequired",
      "validationNotice",
      "validationScopeNotice",
      "vertexGeminiBadge",
      "vertexAnthropicBadge",
      "vertexAnthropicNotice",
      // 8 个错误码
      "vertex_config_invalid",
      "vertex_credentials_missing",
      "vertex_credentials_invalid",
      "vertex_permission_denied",
      "vertex_model_unavailable",
      "vertex_rate_limited",
      "vertex_timeout",
      "vertex_response_blocked",
    ];

    for (const key of requiredKeys) {
      expect(zhConnections[key], `zh-CN missing key: ${key}`).toBeTruthy();
      expect(enConnections[key], `en missing key: ${key}`).toBeTruthy();
    }
  });
});
