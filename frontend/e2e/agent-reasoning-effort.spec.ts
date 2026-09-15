import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";

import type {
  AgentDefinitionResponse,
  AgentReasoningEffort,
} from "../src/features/settings/lib/agent-definitions.types";

const EXPECTED_AGENT_REASONING_EFFORT_OPTIONS: readonly AgentReasoningEffort[] = [
  "inherit",
  "off",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
] as const;
const EXPECTED_DEFAULT_AGENT_REASONING_EFFORT: AgentReasoningEffort = "inherit";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const enTranslations = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../src/i18n/locales/en.json"), "utf-8"),
);
const zhTranslations = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../src/i18n/locales/zh-CN.json"), "utf-8"),
);

const MOCK_MODELS = [
  {
    id: "model-reasoning-supported",
    model_id: "model-reasoning-supported",
    name: "Gemini 2.5 Flash",
    task_type: "llm",
    context_length: 128000,
    provider_id: "provider-1",
    input_price: 0,
    output_price: 0,
    cache_read_price: 0,
    cache_write_price: 0,
  },
  {
    id: "model-reasoning-unsupported",
    model_id: "model-reasoning-unsupported",
    name: "Basic Chat Model",
    task_type: "llm",
    context_length: 128000,
    provider_id: "provider-1",
    input_price: 0,
    output_price: 0,
    cache_read_price: 0,
    cache_write_price: 0,
  },
];

const MOCK_PROVIDERS = [
  {
    id: "provider-1",
    name: "Test Provider",
    url: "https://api.test.com",
    provider_type: "test-provider",
    supported_task_types: ["llm"],
    custom_header_names: [],
    icon_path: null,
    is_builtin: false,
    catalog_match: {
      catalog_provider_type: "test-provider",
      display_name: "Test Provider",
      default_url: null,
      api: null,
      icon_path: null,
      models_dev_provider_id: null,
      matched_via: "provider_type",
    },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
];

function createMockDefinitions(overrides: Partial<AgentDefinitionResponse>[] = []): AgentDefinitionResponse[] {
  const base: AgentDefinitionResponse[] = [
    {
      key: "build",
      display_name: "Build",
      description: "主智能体构建助手",
      kind: "primary",
      prompt_agent_name: "build",
      model_id: "model-reasoning-supported",
      reasoning_effort: "inherit",
      enabled_tool_categories: ["orchestration"],
      enabled_skills: [],
      metadata: {},
      enabled: true,
      source: "builtin",
      color: "#f59e0b",
      icon: "hammer",
      delegatable_agents: ["writer"],
    },
    {
      key: "writer",
      display_name: "Writer",
      description: "专业文本创作子智能体",
      kind: "subagent",
      prompt_agent_name: "writer",
      model_id: "model-reasoning-supported",
      reasoning_effort: "inherit",
      enabled_tool_categories: [],
      enabled_skills: [],
      metadata: {},
      enabled: true,
      source: "builtin",
      color: null,
      icon: null,
      delegatable_agents: [],
    },
  ];

  return base.map((def) => {
    const override = overrides.find((item) => item.key === def.key);
    return override ? { ...def, ...override } : def;
  });
}

async function setupAgentMockRoutes(
  page: Page,
  options?: {
    isLocked?: boolean;
    definitions?: AgentDefinitionResponse[];
    onUpdate?: (body: unknown) => void;
    onCreate?: (body: unknown) => void;
    onReset?: () => void;
  },
) {
  const definitions = options?.definitions ?? createMockDefinitions();

  // 注入 Socket 立即就绪 Mock，防止 main.tsx 在 30 秒握手超时前阻塞在全局 Loading
  await page.addInitScript(() => {
    const fakeSocket: any = {
      connected: true,
      active: true,
      io: {
        engine: { transport: { name: "websocket" } },
        on: () => fakeSocket.io,
        off: () => fakeSocket.io,
      },
      on: () => fakeSocket,
      off: () => fakeSocket,
      once: () => fakeSocket,
      emit: () => fakeSocket,
      connect: () => fakeSocket,
      disconnect: () => fakeSocket,
    };

    let currentUrl: string | undefined = undefined;
    (window as any).__openficSocketClientState = {
      get socket() {
        return fakeSocket;
      },
      set socket(_) {},
      get socketUrl() {
        return currentUrl;
      },
      set socketUrl(val: string | undefined) {
        currentUrl = val;
      },
      connectPromise: Promise.resolve(fakeSocket),
      connectionStartedAt: null,
      lastConnectionError: null,
      lastConnectionHttpStatus: undefined,
      lastConnectionTransport: "websocket",
      connectionStatus: "connected",
      statusListeners: new Set(),
      statusBoundSocket: fakeSocket,
    };
  });

  await page.route("**/socket.io/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/plain",
      body: "ok",
    });
  });

  await page.route("**/runtime-config.json", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ backendBaseUrl: "" }),
    });
  });

  await page.route("**/api/v1/auth/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ enabled: false, authenticated: true }),
    });
  });

  await page.route("**/api/v1/auth/preferences", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        theme: "light",
        language: "zh-CN",
        base_font_size: 14,
        editor_font_size: 16,
      }),
    });
  });

  await page.route("**/api/v1/health", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok" }),
    });
  });

  await page.route("**/api/v1/settings/agent-session-lock", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ is_locked: options?.isLocked ?? false }),
    });
  });

  await page.route("**/api/v1/settings", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          language: "zh-CN",
          theme: "light",
          default_model: "model-reasoning-supported",
          light_model: "model-reasoning-supported",
          default_embedding_model: "",
          index_mode: "auto",
          index_enabled_projects: [],
          index_chunk_size: 500,
          index_chunk_overlap: 50,
          index_auto_strategy: "balanced",
          index_rerank_enabled: false,
          default_rerank_model: "",
          agent_bypass_tool_approval: false,
          agent_tool_permissions: [],
          audit_persist_details: false,
          compress_system_prompts: false,
          telemetry_enabled: false,
        }),
      });
    } else {
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
  });

  await page.route("**/api/v1/agent-definitions", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ definitions }),
      });
    } else if (route.request().method() === "POST") {
      const data = route.request().postDataJSON();
      options?.onCreate?.(data);
      const newDef: AgentDefinitionResponse = {
        key: data.key,
        display_name: data.display_name,
        description: data.description,
        kind: data.kind,
        prompt_agent_name: data.prompt_agent_name,
        model_id: data.model_id,
        reasoning_effort: data.reasoning_effort ?? "inherit",
        enabled_tool_categories: data.enabled_tool_categories ?? [],
        enabled_skills: data.enabled_skills ?? [],
        metadata: data.metadata ?? {},
        enabled: true,
        source: "custom",
        color: data.color ?? null,
        icon: data.icon ?? null,
        delegatable_agents: data.delegatable_agents ?? [],
      };
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(newDef),
      });
    } else {
      await route.continue();
    }
  });

  await page.route("**/api/v1/agent-definitions/**/reset", async (route) => {
    if (route.request().method() === "POST") {
      options?.onReset?.();
      const resetDef: AgentDefinitionResponse = {
        key: "writer",
        display_name: "Writer",
        description: "专业文本创作子智能体",
        kind: "subagent",
        prompt_agent_name: "writer",
        model_id: "model-reasoning-supported",
        reasoning_effort: "inherit",
        enabled_tool_categories: [],
        enabled_skills: [],
        metadata: {},
        enabled: true,
        source: "builtin",
        delegatable_agents: [],
      };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(resetDef),
      });
      return;
    }
    await route.continue();
  });

  await page.route("**/api/v1/agent-definitions/*", async (route) => {
    if (route.request().method() === "PUT") {
      const data = route.request().postDataJSON();
      options?.onUpdate?.(data);
      const updatedDef: AgentDefinitionResponse = {
        key: "writer",
        display_name: data.display_name ?? "Writer",
        description: data.description ?? "",
        kind: data.kind ?? "subagent",
        prompt_agent_name: "writer",
        model_id: data.model_id ?? "model-reasoning-supported",
        reasoning_effort: data.reasoning_effort ?? "inherit",
        enabled_tool_categories: data.enabled_tool_categories ?? [],
        enabled_skills: data.enabled_skills ?? [],
        metadata: data.metadata ?? {},
        enabled: true,
        source: "builtin",
        delegatable_agents: [],
      };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(updatedDef),
      });
      return;
    }

    await route.continue();
  });

  await page.route("**/api/v1/agent-tools/categories", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ categories: [] }),
    });
  });

  await page.route("**/api/v1/skills*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], total: 0 }),
    });
  });

  await page.route("**/api/v1/models*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_MODELS),
    });
  });

  await page.route("**/api/v1/model-providers*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_PROVIDERS),
    });
  });

  await page.route("**/api/v1/model-provider-catalog/providers/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        provider: {
          provider_type: "test-provider",
          display_name: "Test Provider",
          supported_task_types: ["llm"],
        },
        task_type: "llm",
        models: [
          {
            model_id: "model-reasoning-supported",
            display_name: "Gemini 2.5 Flash",
            task_type: "llm",
            metadata: {
              reasoning: true,
            },
          },
          {
            model_id: "model-reasoning-unsupported",
            display_name: "Basic Chat Model",
            task_type: "llm",
            metadata: {
              reasoning: false,
            },
          },
        ],
      }),
    });
  });

  await page.route("**/api/v1/projects*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    });
  });
}

async function openAgentSettings(page: Page) {
  await page.goto("/");
  // 等待加载中界面消失
  await expect(page.locator(".global-loading-container, .global-loading-fallback")).toBeHidden({
    timeout: 15000,
  });

  // 打开侧边栏设置按钮
  const settingsBtn = page.locator('button[aria-label="设置"], button[aria-label="Settings"]').first();
  await expect(settingsBtn).toBeVisible({ timeout: 15000 });
  await settingsBtn.click();

  // 切换到智能体类目
  const agentsNav = page.locator(".settings-sidebar-item").filter({ hasText: /智能体|Agents/ });
  await expect(agentsNav).toBeVisible({ timeout: 10000 });
  await agentsNav.click();
}

test.describe("智能体思考强度配置：契约与类型完备性", () => {
  test("7 档策略取值与默认值常量定义严格对齐技术方案", () => {
    const typesSource = fs.readFileSync(
      path.resolve(__dirname, "../src/features/settings/lib/agent-definitions.types.ts"),
      "utf-8",
    );
    expect(EXPECTED_DEFAULT_AGENT_REASONING_EFFORT).toBe("inherit");
    expect(EXPECTED_AGENT_REASONING_EFFORT_OPTIONS).toEqual([
      "inherit",
      "off",
      "low",
      "medium",
      "high",
      "xhigh",
      "max",
    ]);

    expect(typesSource).toContain('export const DEFAULT_AGENT_REASONING_EFFORT: AgentReasoningEffort = "inherit";');
    expect(typesSource).toContain("export const AGENT_REASONING_EFFORT_OPTIONS");
    for (const effort of EXPECTED_AGENT_REASONING_EFFORT_OPTIONS) {
      expect(typesSource).toContain(`"${effort}"`);
    }
  });

  test("中英文国际化字典包含思考强度标签、全部 7 档选项与说明提示", () => {
    const requiredKeys = [
      "agentsReasoningEffort",
      "agentsReasoningInherit",
      "agentsReasoningOff",
      "agentsReasoningLow",
      "agentsReasoningMedium",
      "agentsReasoningHigh",
      "agentsReasoningXHigh",
      "agentsReasoningMax",
      "agentsReasoningInheritPrimaryNotice",
      "agentsReasoningInheritSubagentNotice",
      "agentsReasoningNotSupportedNotice",
      "agentsReasoningUnknownNotice",
    ];

    for (const key of requiredKeys) {
      expect(zhTranslations.settings[key], `zh-CN missing key: ${key}`).toBeTruthy();
      expect(enTranslations.settings[key], `en missing key: ${key}`).toBeTruthy();
    }

    // 检查中文文案精确匹配规范
    expect(zhTranslations.settings.agentsReasoningEffort).toBe("思考强度");
    expect(zhTranslations.settings.agentsReasoningInherit).toBe("跟随会话 / 父智能体");
    expect(zhTranslations.settings.agentsReasoningOff).toBe("关闭");
    expect(zhTranslations.settings.agentsReasoningInheritPrimaryNotice).toBe("跟随当前会话的思考强度。");
    expect(zhTranslations.settings.agentsReasoningInheritSubagentNotice).toBe(
      "跟随调度它的主智能体当前实际使用的思考强度。",
    );
    expect(zhTranslations.settings.agentsReasoningNotSupportedNotice).toBe(
      "当前模型未标记为支持思考强度，实际调用可能忽略或拒绝此配置",
    );
    expect(zhTranslations.settings.agentsReasoningUnknownNotice).toBe(
      "模型能力未知，请以提供商实际支持为准",
    );
  });
});

test.describe("智能体思考强度配置：UI 与设置页面联调", () => {
  test("1. 打开智能体设置，选中 Writer，确认默认显示跟随说明，且主/子显示不同继承提示", async ({ page }) => {
    await setupAgentMockRoutes(page);
    await openAgentSettings(page);

    // 选中 Writer
    const writerItem = page.locator(".agent-definition-item").filter({ hasText: "Writer" });
    await expect(writerItem).toBeVisible();
    await writerItem.click();

    // 思考强度控件位于表单内，且默认显示“跟随会话 / 父智能体”
    const reasoningTrigger = page.locator(".agent-definition-reasoning-effort-trigger");
    await expect(reasoningTrigger).toBeVisible();
    await expect(reasoningTrigger).toHaveText("跟随会话 / 父智能体");

    // 子智能体显示跟随调度它的主智能体说明
    const subagentNotice = page.locator(".agent-definition-reasoning-inherit-notice");
    await expect(subagentNotice).toHaveText("跟随调度它的主智能体当前实际使用的思考强度。");

    // 切换选中 Build（主智能体）
    const buildItem = page.locator(".agent-definition-item").filter({ hasText: "Build" });
    await expect(buildItem).toBeVisible();
    await buildItem.click();

    // 主智能体显示跟随当前会话说明
    const primaryNotice = page.locator(".agent-definition-reasoning-inherit-notice");
    await expect(primaryNotice).toHaveText("跟随当前会话的思考强度。");
  });

  test("2. 修改为 High，确认保存请求包含 reasoning_effort: 'high'", async ({ page }) => {
    let capturedUpdateBody: any = null;
    await setupAgentMockRoutes(page, {
      onUpdate: (body) => {
        capturedUpdateBody = body;
      },
    });
    await openAgentSettings(page);

    const writerItem = page.locator(".agent-definition-item").filter({ hasText: "Writer" });
    await writerItem.click();

    const reasoningTrigger = page.locator(".agent-definition-reasoning-effort-trigger");
    await reasoningTrigger.click();

    // 选择“高”档位（使用精确匹配排除“超高”）
    const highOption = page.getByRole("option", { name: "高", exact: true });
    await expect(highOption).toBeVisible();
    await highOption.click();

    // 保存按钮启用并点击
    const saveButton = page.locator(".agent-definition-form").getByRole("button", { name: "保存" });
    await expect(saveButton).toBeEnabled();
    await saveButton.click();

    await expect(() => {
      expect(capturedUpdateBody).toBeTruthy();
      expect(capturedUpdateBody.reasoning_effort).toBe("high");
    }).toPass();
  });

  test("3. 刷新并用模拟 API 返回 High，确认正确回显", async ({ page }) => {
    const definitionsWithHigh = createMockDefinitions([
      { key: "writer", reasoning_effort: "high" },
    ]);
    await setupAgentMockRoutes(page, { definitions: definitionsWithHigh });
    await openAgentSettings(page);

    const writerItem = page.locator(".agent-definition-item").filter({ hasText: "Writer" });
    await writerItem.click();

    const reasoningTrigger = page.locator(".agent-definition-reasoning-effort-trigger");
    await expect(reasoningTrigger).toHaveText("高");
  });

  test("4. 改回跟随，确认提交的是 'inherit' 字符串而不是 null 或缺失", async ({ page }) => {
    let capturedUpdateBody: any = null;
    const definitionsWithHigh = createMockDefinitions([
      { key: "writer", reasoning_effort: "high" },
    ]);
    await setupAgentMockRoutes(page, {
      definitions: definitionsWithHigh,
      onUpdate: (body) => {
        capturedUpdateBody = body;
      },
    });
    await openAgentSettings(page);

    const writerItem = page.locator(".agent-definition-item").filter({ hasText: "Writer" });
    await writerItem.click();

    const reasoningTrigger = page.locator(".agent-definition-reasoning-effort-trigger");
    await reasoningTrigger.click();

    // 改回“跟随会话 / 父智能体”
    const inheritOption = page.getByRole("option", { name: "跟随会话 / 父智能体" });
    await expect(inheritOption).toBeVisible();
    await inheritOption.click();

    const saveButton = page.locator(".agent-definition-form").getByRole("button", { name: "保存" });
    await expect(saveButton).toBeEnabled();
    await saveButton.click();

    await expect(() => {
      expect(capturedUpdateBody).toBeTruthy();
      expect(capturedUpdateBody.reasoning_effort).toBe("inherit");
    }).toPass();
  });

  test("5. 复制 Writer，确认创建请求保留源智能体的 reasoning_effort", async ({ page }) => {
    let capturedCreateBody: any = null;
    const definitionsWithHigh = createMockDefinitions([
      { key: "writer", reasoning_effort: "high" },
    ]);
    await setupAgentMockRoutes(page, {
      definitions: definitionsWithHigh,
      onCreate: (body) => {
        capturedCreateBody = body;
      },
    });
    await openAgentSettings(page);

    // 打开菜单
    const writerItem = page.locator(".agent-definition-item").filter({ hasText: "Writer" });
    const menuButton = writerItem.locator(".agent-definition-item-menu");
    await menuButton.click();

    // 点击复制
    const copyAction = page.getByText("复制").last();
    await expect(copyAction).toBeVisible();
    await copyAction.click();

    // 确认复制弹窗
    const dialogConfirmBtn = page.getByRole("dialog").getByRole("button", { name: "复制" });
    await expect(dialogConfirmBtn).toBeVisible();
    await dialogConfirmBtn.click();

    await expect(() => {
      expect(capturedCreateBody).toBeTruthy();
      expect(capturedCreateBody.reasoning_effort).toBe("high");
    }).toPass();
  });

  test("6. 重置内置智能体，确认 UI 正确使用响应中的 inherit", async ({ page }) => {
    let resetCalled = false;
    const definitionsWithHigh = createMockDefinitions([
      { key: "writer", reasoning_effort: "high" },
    ]);
    await setupAgentMockRoutes(page, {
      definitions: definitionsWithHigh,
      onReset: () => {
        resetCalled = true;
      },
    });
    await openAgentSettings(page);

    const writerItem = page.locator(".agent-definition-item").filter({ hasText: "Writer" });
    const menuButton = writerItem.locator(".agent-definition-item-menu");
    await menuButton.click();

    // 点击重置
    const resetAction = page.getByText("重置").last();
    await expect(resetAction).toBeVisible();
    await resetAction.click();

    // 确认重置弹窗（AlertDialog）
    const dialogResetBtn = page
      .getByRole("alertdialog")
      .getByRole("button", { name: "重置" });
    await expect(dialogResetBtn).toBeVisible();
    await dialogResetBtn.click();

    await expect(() => expect(resetCalled).toBe(true)).toPass();

    // 回显更新为跟随
    const reasoningTrigger = page.locator(".agent-definition-reasoning-effort-trigger");
    await expect(reasoningTrigger).toHaveText("跟随会话 / 父智能体");
  });

  test("7. 模型 reasoning=false 时显示不支持提示，但不阻止保存，选 off 时不展示警告", async ({ page }) => {
    let capturedUpdateBody: any = null;
    await setupAgentMockRoutes(page, {
      onUpdate: (body) => {
        capturedUpdateBody = body;
      },
    });
    await openAgentSettings(page);

    const writerItem = page.locator(".agent-definition-item").filter({ hasText: "Writer" });
    await writerItem.click();

    // 切换模型至无 reasoning 能力的 Basic Chat Model
    const modelSelectTrigger = page.locator(".agent-definition-model-trigger");
    await expect(modelSelectTrigger).toBeEnabled();
    await modelSelectTrigger.click();

    const unsupportedOption = page.locator(".rt-PopoverContent").getByText("Basic Chat Model").first();
    await expect(unsupportedOption).toBeVisible();
    await unsupportedOption.click();

    // 检查显示能力不支持提示
    const notice = page.locator(".agent-definition-reasoning-capability-notice");
    await expect(notice).toBeVisible();
    await expect(notice).toHaveText("当前模型未标记为支持思考强度，实际调用可能忽略或拒绝此配置");

    // 改为 off，警告应消失
    const reasoningTrigger = page.locator(".agent-definition-reasoning-effort-trigger");
    await reasoningTrigger.click();
    const offOption = page.getByRole("option", { name: "关闭" });
    await expect(offOption).toBeVisible();
    await offOption.click();
    await expect(notice).toBeHidden();

    // 保存按钮不被禁用，允许继续保存
    const saveButton = page.locator(".agent-definition-form").getByRole("button", { name: "保存" });
    await expect(saveButton).toBeEnabled();
    await saveButton.click();

    await expect(() => {
      expect(capturedUpdateBody).toBeTruthy();
      expect(capturedUpdateBody.model_id).toBe("model-reasoning-unsupported");
      expect(capturedUpdateBody.reasoning_effort).toBe("off");
    }).toPass();
  });

  test("8. 设置锁开启时下拉框与操作禁用", async ({ page }) => {
    await setupAgentMockRoutes(page, { isLocked: true });
    await openAgentSettings(page);

    const writerItem = page.locator(".agent-definition-item").filter({ hasText: "Writer" });
    await writerItem.click();

    // 验证思考强度下拉框禁用
    const reasoningTrigger = page.locator(".agent-definition-reasoning-effort-trigger");
    await expect(reasoningTrigger).toBeDisabled();

    // 保存按钮禁用
    const saveButton = page.locator(".agent-definition-form").getByRole("button", { name: "保存" });
    await expect(saveButton).toBeDisabled();
  });
});
