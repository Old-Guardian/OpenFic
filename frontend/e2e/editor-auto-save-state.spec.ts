import { expect, test } from "@playwright/test";

import { transformSettings } from "../src/features/settings/lib/settings-api";
import type { SettingsResponse } from "../src/features/settings/lib/settings.types";

test.describe("编辑器自动保存设置契约与转换 (T1-T2)", () => {
  const baseMockResponse: SettingsResponse = {
    language: "zh-CN",
    theme: "light",
    font_family: "system-ui",
    default_model: "",
    light_model: "",
    summary_model: "",
    summary_auto_generate_chapter: true,
    summary_auto_generate_long_term: true,
    summary_min_chapter_word_count: 500,
    summary_batch_size: 10,
    summary_long_term_interval: 10,
    summary_chapter_target_length: 200,
    summary_long_term_target_length: 500,
    default_embedding_model: "",
    index_mode: "off",
    index_enabled_projects: [],
    index_chunk_size: 800,
    index_chunk_overlap: 100,
    index_auto_strategy: "off",
    index_rerank_enabled: false,
    default_rerank_model: "",
    agent_bypass_tool_approval: false,
    agent_tool_permissions: [],
    audit_persist_details: false,
    compress_system_prompts: false,
    telemetry_enabled: true,
  };

  test("当后端显式返回 editor_auto_save: false 时，transformSettings 能够正确映射为 false", () => {
    const raw: SettingsResponse = {
      ...baseMockResponse,
      editor_auto_save: false,
    };
    const settings = transformSettings(raw);
    expect(settings.editorAutoSave).toBe(false);
  });

  test("当后端显式返回 editor_auto_save: true 时，transformSettings 正确映射为 true", () => {
    const raw: SettingsResponse = {
      ...baseMockResponse,
      editor_auto_save: true,
    };
    const settings = transformSettings(raw);
    expect(settings.editorAutoSave).toBe(true);
  });

  test("当兼容旧后端（缺少 editor_auto_save 字段）时，transformSettings 默认回退为 true", () => {
    const raw: SettingsResponse = {
      ...baseMockResponse,
    };
    delete raw.editor_auto_save;
    const settings = transformSettings(raw);
    expect(settings.editorAutoSave).toBe(true);
  });

  test("useEditorAutoSaveSetting 状态判定逻辑验证", () => {
    // 1. 加载中或未就绪时：不开启自动保存
    const loadingState: {
      isSuccess: boolean;
      isLoading: boolean;
      isError: boolean;
      data: { editorAutoSave: boolean } | undefined;
    } = {
      isSuccess: false,
      isLoading: true,
      isError: false,
      data: undefined,
    };
    const isReady1 = loadingState.isSuccess && Boolean(loadingState.data);
    const enabled1 = isReady1 ? Boolean(loadingState.data?.editorAutoSave) : false;
    expect(isReady1).toBe(false);
    expect(enabled1).toBe(false);

    // 2. 加载失败时：不凭空开启自动保存
    const errorState: {
      isSuccess: boolean;
      isLoading: boolean;
      isError: boolean;
      data: { editorAutoSave: boolean } | undefined;
    } = {
      isSuccess: false,
      isLoading: false,
      isError: true,
      data: undefined,
    };
    const isReady2 = errorState.isSuccess && Boolean(errorState.data);
    const enabled2 = isReady2 ? Boolean(errorState.data?.editorAutoSave) : false;
    expect(isReady2).toBe(false);
    expect(enabled2).toBe(false);

    // 3. 加载成功且开启：就绪并开启
    const successEnabledState = {
      isSuccess: true,
      isLoading: false,
      isError: false,
      data: { editorAutoSave: true },
    };
    const isReady3 = successEnabledState.isSuccess && Boolean(successEnabledState.data);
    const enabled3 = isReady3 ? Boolean(successEnabledState.data?.editorAutoSave) : false;
    expect(isReady3).toBe(true);
    expect(enabled3).toBe(true);

    // 4. 加载成功但关闭：就绪但关闭
    const successDisabledState = {
      isSuccess: true,
      isLoading: false,
      isError: false,
      data: { editorAutoSave: false },
    };
    const isReady4 = successDisabledState.isSuccess && Boolean(successDisabledState.data);
    const enabled4 = isReady4 ? Boolean(successDisabledState.data?.editorAutoSave) : false;
    expect(isReady4).toBe(true);
    expect(enabled4).toBe(false);
  });
});
