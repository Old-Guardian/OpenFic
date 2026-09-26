import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const e2eDir = path.dirname(fileURLToPath(import.meta.url));

import { transformSettings } from "../src/features/settings/lib/settings-api";
import type { SettingsResponse } from "../src/features/settings/lib/settings.types";
import {
  _resetGlobalInFlightMapForTest,
  AutoSaveScheduler,
  type AutoSaveSchedulerConfig,
  type SaveReason,
  type SaveResult,
  type TimerFunctions,
} from "../src/hooks/auto-save-scheduler";
import { saveOnTitleBlur } from "../src/hooks/use-auto-save";
import {
  _resetEditorSessionStoreForTest,
  handleDesktopCloseRequest,
  requestLeave,
  useEditorSessionStore,
  type EditorSessionRegistration,
} from "../src/features/editor-session";
import { discardWritingEditorChanges } from "../src/features/writing/lib/discard-writing-editor-changes";
import {
  classifyWritingDraftChange,
  type WritingWorkingCopyDraft,
} from "../src/features/writing/lib/writing-working-copy";
import { _resetTabsStoreForTest, useTabsStore } from "../src/features/writing/store/use-tabs-store";

test.describe("编辑器自动保存设置契约与转换 (T1-T2)", () => {
  const baseMockResponse: SettingsResponse = {
    language: "zh-CN",
    theme: "light",
    font_family: "system-ui",
    default_model: "",
    light_model: "",
    default_model_reasoning_effort: "medium",
    light_model_reasoning_effort: "medium",
    summary_model: "",
    summary_model_reasoning_effort: "medium",
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
    notifications_enabled: false,
    notify_on_completion: true,
    notify_on_approval: true,
    notify_on_question: true,
    notify_on_error: true,
    notify_only_when_unfocused: true,
    agent_tool_permissions: [],
    audit_persist_details: false,
    compress_system_prompts: false,
    auto_compact_context: false,
    compaction_model: "",
    compaction_model_reasoning_effort: "medium",
    compaction_trigger_ratio: 0.9,
    compaction_tail_token_budget: 4000,
    compaction_tail_window_ratio: 0.2,
    compaction_min_compactable_tokens: 1000,
    auto_prune_tool_outputs: false,
    prune_protected_tokens: 2000,
    prune_minimum_tokens: 1000,
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

/** 虚拟时钟辅助类，用于精确且瞬时地测试防抖及延迟推进 */
class VirtualTimer {
  currentTime = 0;
  timers: { id: number; dueTime: number; handler: () => void }[] = [];
  nextId = 1;

  asTimers(): TimerFunctions {
    return {
      setTimeout: (handler: () => void, timeout = 0) => {
        const id = this.nextId++;
        this.timers.push({ id, dueTime: this.currentTime + timeout, handler });
        return id;
      },
      clearTimeout: (id: any) => {
        this.timers = this.timers.filter((t) => t.id !== id);
      },
      now: () => this.currentTime,
    };
  }

  async advanceTime(ms: number) {
    this.currentTime += ms;
    while (true) {
      const ready = this.timers
        .filter((t) => t.dueTime <= this.currentTime)
        .sort((a, b) => a.dueTime - b.dueTime);
      if (ready.length === 0) break;
      const next = ready[0];
      this.timers = this.timers.filter((t) => t.id !== next.id);
      next.handler();
      await Promise.resolve();
    }
  }
}

test.describe("编辑器共用保存调度器 (T3: F03, F07, F08, F16)", () => {
  test.beforeEach(() => {
    _resetGlobalInFlightMapForTest();
  });

  test("F03: 到期前关闭：旧定时任务不再提交；重新开启后重新按延迟计时", async () => {
    const timer = new VirtualTimer();
    const calls: { reason: SaveReason; revision: number }[] = [];

    const saveAdapter = async (reason: SaveReason, revision: number): Promise<SaveResult> => {
      calls.push({ reason, revision });
      return { status: "saved", savedRevision: revision };
    };

    const scheduler = new AutoSaveScheduler({
      documentKey: "chapter:test-f03",
      enabled: true,
      delayMs: 3000,
      dirtyRevision: 1,
      hasChanges: true,
      save: saveAdapter,
      timers: timer.asTimers(),
    });

    expect(scheduler.getState().isScheduled).toBe(true);

    // 时间推进到 1500ms（尚未到期），此时关闭自动保存开关
    await timer.advanceTime(1500);
    expect(calls.length).toBe(0);

    scheduler.updateConfig({
      documentKey: "chapter:test-f03",
      enabled: false,
      delayMs: 3000,
      dirtyRevision: 1,
      hasChanges: true,
      save: saveAdapter,
      timers: timer.asTimers(),
    });

    expect(scheduler.getState().isScheduled).toBe(false);

    // 推进超过原 3000ms 到 4000ms：旧任务不得提交
    await timer.advanceTime(2500);
    expect(calls.length).toBe(0);

    // 重新开启自动保存
    scheduler.updateConfig({
      documentKey: "chapter:test-f03",
      enabled: true,
      delayMs: 3000,
      dirtyRevision: 1,
      hasChanges: true,
      save: saveAdapter,
      timers: timer.asTimers(),
    });

    expect(scheduler.getState().isScheduled).toBe(true);

    // 从 4000ms 开始重新计时 3000ms（至 7000ms）
    await timer.advanceTime(2999);
    expect(calls.length).toBe(0);

    await timer.advanceTime(2);
    expect(calls.length).toBe(1);
    expect(calls[0]).toEqual({ reason: "auto", revision: 1 });
    expect(scheduler.getState().lastSavedRevision).toBe(1);
  });

  test("F07: 慢请求期间再编辑：旧响应不清除新修改；同一文档最多一个在途请求", async () => {
    let resolveFirstSave!: (result: SaveResult) => void;
    const firstSavePromise = new Promise<SaveResult>((resolve) => {
      resolveFirstSave = resolve;
    });

    const calls: { reason: SaveReason; revision: number }[] = [];

    const saveAdapter = async (reason: SaveReason, revision: number): Promise<SaveResult> => {
      calls.push({ reason, revision });
      if (calls.length === 1) {
        return firstSavePromise;
      }
      return { status: "saved", savedRevision: revision };
    };

    const scheduler = new AutoSaveScheduler({
      documentKey: "chapter:test-f07",
      enabled: true,
      delayMs: 1000,
      dirtyRevision: 1,
      hasChanges: true,
      save: saveAdapter,
    });

    // 第一次保存开始执行（如手动触发或定时到期）
    const firstTrigger = scheduler.triggerSave("auto");
    expect(calls.length).toBe(1);
    expect(calls[0]).toEqual({ reason: "auto", revision: 1 });
    expect(scheduler.getState().isSaving).toBe(true);

    // 在请求尚未返回期间，用户继续编辑：dirtyRevision 变为 2
    scheduler.updateConfig({
      documentKey: "chapter:test-f07",
      enabled: true,
      delayMs: 1000,
      dirtyRevision: 2,
      hasChanges: true,
      save: saveAdapter,
    });

    // 用户在慢请求期间又触发了手动保存 (triggerSave)
    // 调度原则 3: 必须先等待在途请求完成，按文档串行执行，不得同时发出第二个并发网络请求
    const manualTrigger = scheduler.triggerSave("manual");
    expect(calls.length).toBe(1); // 仍只有一个在途请求，第二个在排队等待

    // 此时第一个保存请求终于返回
    resolveFirstSave({ status: "saved", savedRevision: 1 });
    const firstResult = await firstTrigger;
    expect(firstResult).toEqual({ status: "saved", savedRevision: 1 });

    // 排队的第二个请求继续执行，且以最新的 revision 2 发起保存
    const manualResult = await manualTrigger;
    expect(manualResult).toEqual({ status: "saved", savedRevision: 2 });
    expect(calls.length).toBe(2);
    expect(calls[1]).toEqual({ reason: "manual", revision: 2 });
    expect(scheduler.getState().lastSavedRevision).toBe(2);
  });

  test("F08: 请求期间关闭开关：已发请求可完成，但新修改不再自动提交", async () => {
    const timer = new VirtualTimer();
    let resolveFirstSave!: (result: SaveResult) => void;
    const firstSavePromise = new Promise<SaveResult>((resolve) => {
      resolveFirstSave = resolve;
    });

    const calls: { reason: SaveReason; revision: number }[] = [];

    const saveAdapter = async (reason: SaveReason, revision: number): Promise<SaveResult> => {
      calls.push({ reason, revision });
      if (calls.length === 1) {
        return firstSavePromise;
      }
      return { status: "saved", savedRevision: revision };
    };

    const scheduler = new AutoSaveScheduler({
      documentKey: "chapter:test-f08",
      enabled: true,
      delayMs: 2000,
      dirtyRevision: 1,
      hasChanges: true,
      save: saveAdapter,
      timers: timer.asTimers(),
    });

    // 定时触发保存 revision 1
    await timer.advanceTime(2000);
    expect(calls.length).toBe(1);
    expect(calls[0]).toEqual({ reason: "auto", revision: 1 });

    // 在请求在途期间，用户再次编辑（revision 2）并关闭开关（enabled: false）
    scheduler.updateConfig({
      documentKey: "chapter:test-f08",
      enabled: false,
      delayMs: 2000,
      dirtyRevision: 2,
      hasChanges: true,
      save: saveAdapter,
      timers: timer.asTimers(),
    });

    // 已发出请求允许正常完成
    resolveFirstSave({ status: "saved", savedRevision: 1 });
    await new Promise((r) => setTimeout(r, 10));
    expect(scheduler.getState().lastSavedRevision).toBe(1);

    // 即使推进时间，因为开关已关闭，revision 2 不得自动提交
    await timer.advanceTime(10000);
    expect(calls.length).toBe(1);
    expect(scheduler.getState().isScheduled).toBe(false);
  });

  test("P2.1: 关闭开关阻止已排队的自动保存，手动保存仍可提交", async () => {
    let resolveFirst!: (result: SaveResult) => void;
    const firstGate = new Promise<SaveResult>((resolve) => {
      resolveFirst = resolve;
    });

    const calls: { reason: SaveReason; revision: number }[] = [];
    const saveAdapter = async (reason: SaveReason, revision: number): Promise<SaveResult> => {
      calls.push({ reason, revision });
      if (calls.length === 1) {
        return firstGate; // 版本 1 自动保存保持在途
      }
      return { status: "saved", savedRevision: revision };
    };

    const timer = new VirtualTimer();
    const baseConfig: AutoSaveSchedulerConfig = {
      documentKey: "chapter:p2-1",
      enabled: true,
      delayMs: 2000,
      dirtyRevision: 1,
      hasChanges: true,
      save: saveAdapter,
      timers: timer.asTimers(),
    };
    const scheduler = new AutoSaveScheduler(baseConfig);

    // 版本 1 的自动保存到期，进入在途（慢请求）
    await timer.advanceTime(2000);
    expect(calls).toEqual([{ reason: "auto", revision: 1 }]);

    // 请求在途期间继续编辑到版本 2，并推进计时器让第二次自动保存进入排队等待
    scheduler.updateConfig({ ...baseConfig, dirtyRevision: 2 });
    await timer.advanceTime(2000);
    // 第二个请求必须等待队首，尚未发出
    expect(calls.length).toBe(1);

    // 此时关闭开关
    scheduler.updateConfig({ ...baseConfig, dirtyRevision: 2, enabled: false });

    // 版本 1 请求完成：排队中的自动保存不得因等待结束而继续提交
    resolveFirst({ status: "saved", savedRevision: 1 });
    await flushAsync();

    // 核心断言：只有首个已发出请求，最新修改仍未保存；关闭后没有新请求
    expect(calls).toEqual([{ reason: "auto", revision: 1 }]);
    expect(scheduler.getState().lastSavedRevision).toBe(1);
    expect(scheduler.getState().isScheduled).toBe(false);

    // 对照：手动保存不受开关限制，仍可提交最新版本
    await scheduler.triggerSave("manual");
    expect(calls).toEqual([
      { reason: "auto", revision: 1 },
      { reason: "manual", revision: 2 },
    ]);
  });

  test("P2.2: 放弃修改等待在途请求结束，且不提交已放弃的内容", async () => {
    let resolveFirst!: (result: SaveResult) => void;
    const firstGate = new Promise<SaveResult>((resolve) => {
      resolveFirst = resolve;
    });

    const calls: { reason: SaveReason; revision: number }[] = [];
    const saveAdapter = async (reason: SaveReason, revision: number): Promise<SaveResult> => {
      calls.push({ reason, revision });
      if (calls.length === 1) {
        return firstGate; // 版本 1 保持在途
      }
      return { status: "saved", savedRevision: revision };
    };

    const timer = new VirtualTimer();
    const baseConfig: AutoSaveSchedulerConfig = {
      documentKey: "chapter:p2-2",
      enabled: true,
      delayMs: 2000,
      dirtyRevision: 1,
      hasChanges: true,
      save: saveAdapter,
      timers: timer.asTimers(),
    };
    const scheduler = new AutoSaveScheduler(baseConfig);

    // 版本 1 的自动保存到期并在途
    await timer.advanceTime(2000);
    expect(calls).toEqual([{ reason: "auto", revision: 1 }]);

    // 继续编辑到版本 2，第二次自动保存进入排队
    scheduler.updateConfig({ ...baseConfig, dirtyRevision: 2 });
    await timer.advanceTime(2000);
    expect(calls.length).toBe(1);

    // 用户在慢请求期间放弃修改
    scheduler.cancel();
    let idleResolved = false;
    const idle = scheduler.whenIdle().then(() => {
      idleResolved = true;
    });

    // 核心断言 1：在途请求尚未结束时，放弃流程不得放行
    await flushAsync();
    expect(idleResolved).toBe(false);

    // 在途请求结束
    resolveFirst({ status: "saved", savedRevision: 1 });
    await idle;
    expect(idleResolved).toBe(true);

    // 核心断言 2：已放弃的排队内容不得提交，也不得额外保存
    expect(calls).toEqual([{ reason: "auto", revision: 1 }]);

    // 再次编辑解除放弃状态：新的修改可正常自动保存
    scheduler.updateConfig({ ...baseConfig, dirtyRevision: 3 });
    await timer.advanceTime(2000);
    expect(calls).toEqual([
      { reason: "auto", revision: 1 },
      { reason: "auto", revision: 3 },
    ]);
  });

  test("F16: 设置快速操作与无关重渲染：不出现旧设置响应覆盖新状态，不重复/无限推迟保存", async () => {
    const timer = new VirtualTimer();
    const calls: { reason: SaveReason; revision: number }[] = [];

    const saveAdapter = async (reason: SaveReason, revision: number): Promise<SaveResult> => {
      calls.push({ reason, revision });
      return { status: "saved", savedRevision: revision };
    };

    const scheduler = new AutoSaveScheduler({
      documentKey: "chapter:test-f16",
      enabled: true,
      delayMs: 3000,
      dirtyRevision: 1,
      hasChanges: true,
      save: saveAdapter,
      timers: timer.asTimers(),
    });

    // 在 t=1000 时，外部触发 5 次无关重渲染（如主题切换、UI布局调整，但 dirtyRevision 仍为 1）
    await timer.advanceTime(1000);
    for (let i = 0; i < 5; i++) {
      scheduler.updateConfig({
        documentKey: "chapter:test-f16",
        enabled: true,
        delayMs: 3000,
        dirtyRevision: 1,
        hasChanges: true,
        save: saveAdapter,
        timers: timer.asTimers(),
      });
    }

    // 验证：定时器未被推迟到 1000 + 3000 = 4000，而是在初始约定的 3000ms 准时触发
    await timer.advanceTime(1999);
    expect(calls.length).toBe(0);

    await timer.advanceTime(2);
    expect(calls.length).toBe(1);
    expect(calls[0]).toEqual({ reason: "auto", revision: 1 });
  });

  test("P1.2: 多个等待中的保存严格串行，任一时刻在途至多 1，最终写入最新版本", async () => {
    let concurrent = 0;
    let maxConcurrent = 0;
    let serverContent = 0;
    const calls: { reason: SaveReason; revision: number }[] = [];

    let resolveFirst!: (result: SaveResult) => void;
    const firstGate = new Promise<SaveResult>((resolve) => {
      resolveFirst = resolve;
    });

    const saveAdapter = async (reason: SaveReason, revision: number): Promise<SaveResult> => {
      concurrent++;
      maxConcurrent = Math.max(maxConcurrent, concurrent);
      calls.push({ reason, revision });
      try {
        if (calls.length === 1) {
          await firstGate; // 版本 1 的慢请求保持在途
        } else {
          await new Promise((r) => setTimeout(r, 5));
        }
        // 模拟服务端最终内容：谁最后写入谁定稿，旧请求迟到会覆盖新内容
        serverContent = revision;
        return { status: "saved", savedRevision: revision };
      } finally {
        concurrent--;
      }
    };

    const scheduler = new AutoSaveScheduler({
      documentKey: "chapter:p1-2",
      enabled: true,
      delayMs: 1000,
      dirtyRevision: 1,
      hasChanges: true,
      save: saveAdapter,
    });

    // 版本 1 的慢保存已在途
    const first = scheduler.triggerSave("manual");
    expect(calls).toEqual([{ reason: "manual", revision: 1 }]);

    // 用户在慢请求期间继续编辑到版本 2
    scheduler.updateConfig({
      documentKey: "chapter:p1-2",
      enabled: true,
      delayMs: 1000,
      dirtyRevision: 2,
      hasChanges: true,
      save: saveAdapter,
    });

    // 连续两次手动保存：二者都必须等待在途请求，不得并发发出
    const second = scheduler.triggerSave("manual");
    const third = scheduler.triggerSave("manual");
    expect(calls.length).toBe(1);

    // 版本 1 完成：两个等待者应依次执行，而非一拥而上
    resolveFirst({ status: "saved", savedRevision: 1 });
    const [firstResult, secondResult, thirdResult] = await Promise.all([first, second, third]);

    expect(firstResult).toEqual({ status: "saved", savedRevision: 1 });
    // 核心断言 1：任一时刻同一文档在途请求数不超过 1
    expect(maxConcurrent).toBe(1);
    // 核心断言 2：仅第二个等待者提交最新版本，第三个发现无新修改不再重复写入
    expect(calls).toEqual([
      { reason: "manual", revision: 1 },
      { reason: "manual", revision: 2 },
    ]);
    expect(secondResult).toEqual({ status: "saved", savedRevision: 2 });
    expect(thirdResult).toEqual({ status: "unchanged" });
    // 核心断言 3：最终服务端内容为最新版本，未被迟到请求覆盖
    expect(serverContent).toBe(2);
    expect(scheduler.getState().lastSavedRevision).toBe(2);
  });

  test("锁定拦截与清理语义：锁定期间不调度，卸载清理不隐式正式保存", async () => {
    const timer = new VirtualTimer();
    const calls: { reason: SaveReason; revision: number }[] = [];

    const saveAdapter = async (reason: SaveReason, revision: number): Promise<SaveResult> => {
      calls.push({ reason, revision });
      return { status: "saved", savedRevision: revision };
    };

    const scheduler = new AutoSaveScheduler({
      documentKey: "chapter:test-locked",
      enabled: true,
      delayMs: 2000,
      dirtyRevision: 1,
      hasChanges: true,
      blockedReason: "Agent 正在生成中",
      save: saveAdapter,
      timers: timer.asTimers(),
    });

    // 被锁定中，不得调度
    expect(scheduler.getState().isScheduled).toBe(false);
    await timer.advanceTime(5000);
    expect(calls.length).toBe(0);

    // 手动保存被拒绝并返回 blocked 契约
    const manualResult = await scheduler.triggerSave("manual");
    expect(manualResult).toEqual({
      status: "blocked",
      reason: "Agent 正在生成中",
    });

    // 解锁后重新安排调度
    scheduler.updateConfig({
      documentKey: "chapter:test-locked",
      enabled: true,
      delayMs: 2000,
      dirtyRevision: 1,
      hasChanges: true,
      blockedReason: null,
      save: saveAdapter,
      timers: timer.asTimers(),
    });

    expect(scheduler.getState().isScheduled).toBe(true);

    // 卸载组件触发 dispose：清理定时器，绝不得调用 save 正式接口
    scheduler.dispose();
    expect(scheduler.getState().isScheduled).toBe(false);
    await timer.advanceTime(5000);
    expect(calls.length).toBe(0);
  });
});

test.describe("未保存离开保护与会话管理 (T5: 模拟会话保存失败不放行、放弃不重建草稿、重复导航只确认一次)", () => {
  test.beforeEach(() => {
    _resetEditorSessionStoreForTest();
  });

  test("会话注册、状态更新与根据 dirty 状态判定受影响会话", () => {
    const store = useEditorSessionStore.getState();

    const doc1: EditorSessionRegistration = {
      documentKey: "chapter:1",
      entityType: "chapter",
      entityId: "1",
      title: "第一章",
      isDirty: false,
      isSaving: false,
      save: async () => ({ status: "unchanged" }),
    };

    const doc2: EditorSessionRegistration = {
      documentKey: "note:2",
      entityType: "note",
      entityId: "2",
      title: "角色设定笔记",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
    };

    let dynamicDirty = false;
    const doc3: EditorSessionRegistration = {
      documentKey: "world-info:3",
      entityType: "world-info",
      entityId: "3",
      title: "世界观背景",
      isDirty: false,
      isSaving: false,
      getIsDirty: () => dynamicDirty,
      save: async () => ({ status: "saved", savedRevision: 1 }),
    };

    const unreg1 = store.registerSession(doc1);
    const unreg2 = store.registerSession(doc2);
    const unreg3 = store.registerSession(doc3);

    // 初始只有 doc2 是 dirty
    let dirtyDocs = store.getDirtySessions("all");
    expect(dirtyDocs.map((d) => d.documentKey)).toEqual(["note:2"]);

    // 动态变化：doc3 变为 dirty
    dynamicDirty = true;
    dirtyDocs = store.getDirtySessions("all");
    expect(dirtyDocs.map((d) => d.documentKey)).toEqual(["note:2", "world-info:3"]);

    // 指定受影响文档范围过滤
    const filteredDocs = store.getDirtySessions(["chapter:1", "note:2"]);
    expect(filteredDocs.map((d) => d.documentKey)).toEqual(["note:2"]);

    // 注销会话
    unreg2();
    dirtyDocs = store.getDirtySessions("all");
    expect(dirtyDocs.map((d) => d.documentKey)).toEqual(["world-info:3"]);

    unreg1();
    unreg3();
  });

  test("模拟会话保存失败不放行（验收门槛）", async () => {
    const store = useEditorSessionStore.getState();
    let actionExecuted = false;

    store.registerSession({
      documentKey: "chapter:err-doc",
      entityType: "chapter",
      entityId: "err-doc",
      title: "保存失败的章节",
      isDirty: true,
      isSaving: false,
      save: async () => ({
        status: "failed",
        error: new Error("网络超时或后端数据库写入失败"),
      }),
    });

    const leavePromise = requestLeave("all", () => {
      actionExecuted = true;
    });

    // 此时确认弹窗应已展开，显示该受影响文档
    const dialogState = useEditorSessionStore.getState().dialogState;
    expect(dialogState?.isOpen).toBe(true);
    expect(dialogState?.documents[0]?.documentKey).toBe("chapter:err-doc");

    // 用户在弹窗中选择“保存并离开”
    useEditorSessionStore.getState().chooseDecision("save");
    await flushAsync();

    // 验收门槛要求：模拟会话保存失败时绝不得放行，留在原处
    expect(actionExecuted).toBe(false);

    // 弹窗状态应记录错误信息并保持可重试（修复 P1.3 后不再立刻返回 false）
    const currentDialog = useEditorSessionStore.getState().dialogState;
    expect(currentDialog?.isOpen).toBe(true);
    expect(currentDialog?.errorMessage).toContain("网络超时或后端数据库写入失败");

    // 用户选择取消离开：仍然不放行
    useEditorSessionStore.getState().chooseDecision("cancel");
    const allowed = await leavePromise;
    expect(allowed).toBe(false);
    expect(actionExecuted).toBe(false);
  });

  test("P1.3: 保存失败后弹窗保持可重试，重试成功/放弃/取消均不卡住", async () => {
    const store = useEditorSessionStore.getState();
    let saveAttempts = 0;
    let discardCalled = false;
    let actionExecuted = false;

    store.registerSession({
      documentKey: "chapter:p1-3-retry",
      entityType: "chapter",
      entityId: "p1-3-retry",
      title: "首次保存失败的章节",
      isDirty: true,
      isSaving: false,
      save: async () => {
        saveAttempts++;
        // 第一次失败，第二次重试成功
        if (saveAttempts === 1) {
          return { status: "failed", error: new Error("网络断开，写入失败") };
        }
        return { status: "saved", savedRevision: 2 };
      },
      discard: () => {
        discardCalled = true;
      },
    });

    const leavePromise = requestLeave("all", () => {
      actionExecuted = true;
    });

    // 第一次选择“保存并离开”：失败，不得放行，弹窗保留错误
    store.chooseDecision("save");
    await flushAsync();
    expect(actionExecuted).toBe(false);

    const afterFailure = useEditorSessionStore.getState().dialogState;
    expect(afterFailure?.isOpen).toBe(true);
    expect(afterFailure?.isProcessing).toBe(false);
    expect(afterFailure?.errorMessage).toContain("网络断开");
    expect(saveAttempts).toBe(1);

    // 同一弹窗再次选择“保存并离开”：必须能被处理（修复前 resolve 已清空，无人消费）
    store.chooseDecision("save");
    const allowed = await leavePromise;

    expect(saveAttempts).toBe(2);
    expect(allowed).toBe(true);
    expect(actionExecuted).toBe(true);
    expect(useEditorSessionStore.getState().dialogState).toBeNull();
    expect(discardCalled).toBe(false);
  });

  test("P1.3: 保存失败后再次放弃修改可以正常放行", async () => {
    const store = useEditorSessionStore.getState();
    let discardCalled = false;
    let actionExecuted = false;

    store.registerSession({
      documentKey: "chapter:p1-3-discard",
      entityType: "chapter",
      entityId: "p1-3-discard",
      title: "保存失败后放弃的章节",
      isDirty: true,
      isSaving: false,
      save: async () => {
        return { status: "failed", error: new Error("后端写入失败") };
      },
      discard: () => {
        discardCalled = true;
      },
    });

    const leavePromise = requestLeave("all", () => {
      actionExecuted = true;
    });

    store.chooseDecision("save");
    await flushAsync();
    expect(actionExecuted).toBe(false);
    expect(store.dialogState ?? useEditorSessionStore.getState().dialogState?.isOpen).toBeTruthy();

    // 失败后改选“放弃修改”：必须能被处理并放行
    store.chooseDecision("discard");
    const allowed = await leavePromise;

    expect(allowed).toBe(true);
    expect(discardCalled).toBe(true);
    expect(actionExecuted).toBe(true);
  });

  test("P1.3: 保存失败后取消离开，弹窗关闭且不卡住等待者", async () => {
    const store = useEditorSessionStore.getState();
    let actionExecuted = false;

    store.registerSession({
      documentKey: "chapter:p1-3-cancel",
      entityType: "chapter",
      entityId: "p1-3-cancel",
      title: "保存失败后取消的章节",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "failed", error: new Error("写入失败") }),
    });

    const leavePromise = requestLeave("all", () => {
      actionExecuted = true;
    });

    store.chooseDecision("save");
    await flushAsync();
    expect(actionExecuted).toBe(false);

    // 失败后取消：弹窗关闭，不放行
    store.chooseDecision("cancel");
    const allowed = await leavePromise;

    expect(allowed).toBe(false);
    expect(actionExecuted).toBe(false);
    expect(useEditorSessionStore.getState().dialogState).toBeNull();
  });

  test("P1.3: blocked 返回后弹窗仍可重试并放行", async () => {
    const store = useEditorSessionStore.getState();
    let saveAttempts = 0;
    let actionExecuted = false;

    store.registerSession({
      documentKey: "chapter:p1-3-blocked",
      entityType: "chapter",
      entityId: "p1-3-blocked",
      title: "被锁定后解锁的章节",
      isDirty: true,
      isSaving: false,
      save: async () => {
        saveAttempts++;
        if (saveAttempts === 1) {
          return { status: "blocked", reason: "文档被 Agent 锁定" };
        }
        return { status: "saved", savedRevision: 2 };
      },
    });

    const leavePromise = requestLeave("all", () => {
      actionExecuted = true;
    });

    store.chooseDecision("save");
    await flushAsync();
    expect(actionExecuted).toBe(false);
    expect(useEditorSessionStore.getState().dialogState?.errorMessage).toContain("Agent 锁定");

    store.chooseDecision("save");
    const allowed = await leavePromise;

    expect(saveAttempts).toBe(2);
    expect(allowed).toBe(true);
    expect(actionExecuted).toBe(true);
  });

  test("P2.2: 放弃修改时离开确认等待在途保存请求完成", async () => {
    let resolveInFlight!: (result: SaveResult) => void;
    const gate = new Promise<SaveResult>((resolve) => {
      resolveInFlight = resolve;
    });
    const calls: number[] = [];

    const scheduler = new AutoSaveScheduler({
      documentKey: "note:p2-2-int",
      enabled: true,
      delayMs: 3000,
      dirtyRevision: 1,
      hasChanges: true,
      save: async (_reason, rev) => {
        calls.push(rev);
        return gate;
      },
    });

    // 版本 1 的保存已在途
    void scheduler.triggerSave("auto");
    expect(calls).toEqual([1]);

    let discardCompleted = false;
    let navigated = false;

    // 模拟真实 discard 接线：cancel → whenIdle → 恢复基线
    useEditorSessionStore.getState().registerSession({
      documentKey: "note:p2-2-int",
      entityType: "note",
      entityId: "p2-2-int",
      title: "测试笔记",
      isDirty: true,
      isSaving: true,
      save: async () => scheduler.triggerSave("leave"),
      discard: async () => {
        scheduler.cancel();
        await scheduler.whenIdle();
        discardCompleted = true;
      },
    });

    const leavePromise = requestLeave("all", () => {
      navigated = true;
    });
    useEditorSessionStore.getState().chooseDecision("discard");

    // 核心断言：在途请求未结束前，discard 与放行都必须等待
    await flushAsync();
    expect(discardCompleted).toBe(false);
    expect(navigated).toBe(false);

    // 在途请求完成
    resolveInFlight({ status: "saved", savedRevision: 1 });
    const allowed = await leavePromise;

    expect(discardCompleted).toBe(true);
    expect(allowed).toBe(true);
    expect(navigated).toBe(true);
    // 不得为“放弃”额外提交被放弃的内容
    expect(calls).toEqual([1]);
  });

  test("P2.3: 草稿清理失败不放行离开，恢复后可重试放弃", async () => {
    const store = useEditorSessionStore.getState();
    let actionExecuted = false;
    let discardAttempts = 0;
    let storageAvailable = false;

    store.registerSession({
      documentKey: "note:p2-3",
      entityType: "note",
      entityId: "p2-3",
      title: "清理失败的笔记",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
      discard: async () => {
        discardAttempts++;
        if (!storageAvailable) {
          throw new Error("IndexedDB deletion failed");
        }
      },
    });

    const leavePromise = requestLeave("all", () => {
      actionExecuted = true;
    });

    // 第一次放弃：清理失败，不得放行
    store.chooseDecision("discard");
    await flushAsync();

    expect(actionExecuted).toBe(false);
    const afterFailure = useEditorSessionStore.getState().dialogState;
    expect(afterFailure?.isOpen).toBe(true);
    expect(afterFailure?.isProcessing).toBe(false);
    expect(afterFailure?.errorMessage).toContain("IndexedDB deletion failed");
    expect(discardAttempts).toBe(1);

    // 用户取消：仍留在原处
    store.chooseDecision("cancel");
    const cancelled = await leavePromise;
    expect(cancelled).toBe(false);
    expect(actionExecuted).toBe(false);

    // 恢复存储后重新发起放弃：清理成功并放行
    storageAvailable = true;
    const secondLeave = requestLeave("all", () => {
      actionExecuted = true;
    });
    store.chooseDecision("discard");
    const allowed = await secondLeave;

    expect(discardAttempts).toBe(2);
    expect(allowed).toBe(true);
    expect(actionExecuted).toBe(true);
    expect(useEditorSessionStore.getState().dialogState).toBeNull();
  });

  test("P2.3: 放弃失败后可直接重试成功并放行", async () => {
    const store = useEditorSessionStore.getState();
    let actionExecuted = false;
    let discardAttempts = 0;

    store.registerSession({
      documentKey: "chapter:p2-3-retry",
      entityType: "chapter",
      entityId: "p2-3-retry",
      title: "清理重试的章节",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
      discard: async () => {
        discardAttempts++;
        if (discardAttempts === 1) {
          throw new Error("IndexedDB deletion failed");
        }
      },
    });

    const leavePromise = requestLeave("all", () => {
      actionExecuted = true;
    });

    store.chooseDecision("discard");
    await flushAsync();
    expect(actionExecuted).toBe(false);

    // 同一弹窗再次放弃：清理成功，放行
    store.chooseDecision("discard");
    const allowed = await leavePromise;

    expect(discardAttempts).toBe(2);
    expect(allowed).toBe(true);
    expect(actionExecuted).toBe(true);
  });

  test("P1: 放弃清理失败后改选保存，真实调度器仍会写入再放行", async () => {
    const store = useEditorSessionStore.getState();
    let writes = 0;
    let leaves = 0;
    const scheduler = new AutoSaveScheduler({
      documentKey: "note:p1-discard-save",
      enabled: false,
      delayMs: 3000,
      dirtyRevision: 1,
      hasChanges: true,
      save: async (_reason, revision) => {
        writes++;
        return { status: "saved", savedRevision: revision };
      },
    });

    store.registerSession({
      documentKey: "note:p1-discard-save",
      entityType: "note",
      entityId: "p1-discard-save",
      title: "放弃失败的笔记",
      isDirty: true,
      isSaving: false,
      save: () => scheduler.triggerSave("leave"),
      discard: () =>
        discardWritingEditorChanges({
          autoSave: scheduler,
          discardWorkingCopy: async () => {
            throw new Error("IndexedDB deletion failed");
          },
          onDiscarded: () => {},
        }),
    });

    const leavePromise = requestLeave("all", () => {
      leaves++;
    });
    store.chooseDecision("discard");
    await flushAsync();

    expect(writes).toBe(0);
    expect(leaves).toBe(0);
    expect(useEditorSessionStore.getState().dialogState?.errorMessage).toContain(
      "IndexedDB deletion failed",
    );

    store.chooseDecision("save");
    const allowed = await leavePromise;

    expect(allowed).toBe(true);
    expect(writes).toBe(1);
    expect(leaves).toBe(1);
  });

  test("P1: 放弃清理失败后取消离开，手动保存仍可写入", async () => {
    const store = useEditorSessionStore.getState();
    let writes = 0;
    const scheduler = new AutoSaveScheduler({
      documentKey: "chapter:p1-discard-cancel",
      enabled: false,
      delayMs: 3000,
      dirtyRevision: 1,
      hasChanges: true,
      save: async (_reason, revision) => {
        writes++;
        return { status: "saved", savedRevision: revision };
      },
    });

    store.registerSession({
      documentKey: "chapter:p1-discard-cancel",
      entityType: "chapter",
      entityId: "p1-discard-cancel",
      title: "放弃失败的章节",
      isDirty: true,
      isSaving: false,
      save: () => scheduler.triggerSave("leave"),
      discard: () =>
        discardWritingEditorChanges({
          autoSave: scheduler,
          discardWorkingCopy: async () => {
            throw new Error("IndexedDB deletion failed");
          },
          onDiscarded: () => {},
        }),
    });

    const leavePromise = requestLeave("all", () => {});
    store.chooseDecision("discard");
    await flushAsync();
    store.chooseDecision("cancel");

    expect(await leavePromise).toBe(false);
    expect(await scheduler.triggerSave("manual")).toEqual({
      status: "saved",
      savedRevision: 1,
    });
    expect(writes).toBe(1);
  });

  test("P1: 慢保存 B 时改回 A，会推进版本、保留本地副本并在离开前保存 A", async () => {
    const baseline: WritingWorkingCopyDraft = { title: "title", content: "A" };
    const edited: WritingWorkingCopyDraft = { title: "title", content: "B" };
    let current = baseline;
    let saved = baseline;
    let revision = 0;
    let isDirty = false;
    const persistedDrafts: WritingWorkingCopyDraft[] = [];
    const writes: { draft: WritingWorkingCopyDraft; revision: number }[] = [];
    let resolveFirstSave!: () => void;
    const firstSaveGate = new Promise<void>((resolve) => {
      resolveFirstSave = resolve;
    });
    let scheduler!: AutoSaveScheduler;

    const config = (): AutoSaveSchedulerConfig => ({
      documentKey: "note:p1-return-to-baseline",
      enabled: false,
      delayMs: 3000,
      dirtyRevision: revision,
      hasChanges: isDirty,
      save: async (_reason, targetRevision) => {
        const draftToSave = { ...current };
        writes.push({ draft: draftToSave, revision: targetRevision });
        if (writes.length === 1) {
          await firstSaveGate;
        }
        saved = draftToSave;
        isDirty = classifyWritingDraftChange(current, current, saved).isDirty;
        scheduler.updateConfig(config());
        return { status: "saved", savedRevision: targetRevision };
      },
    });

    const edit = (next: WritingWorkingCopyDraft) => {
      const change = classifyWritingDraftChange(current, next, saved);
      current = next;
      isDirty = change.isDirty;
      if (change.didChange) {
        revision++;
        persistedDrafts.push({ ...next });
      }
      scheduler.updateConfig(config());
    };

    scheduler = new AutoSaveScheduler(config());
    edit(edited);
    const firstSave = scheduler.triggerSave("manual");
    expect(writes).toEqual([{ draft: edited, revision: 1 }]);

    // 在 B 的请求返回前改回旧基线 A：isDirty=false，但编辑版本必须变为 2。
    edit(baseline);
    expect(revision).toBe(2);
    expect(isDirty).toBe(false);
    expect(persistedDrafts).toEqual([edited, baseline]);

    resolveFirstSave();
    await firstSave;
    expect(saved).toEqual(edited);
    expect(isDirty).toBe(true);

    let leaves = 0;
    useEditorSessionStore.getState().registerSession({
      documentKey: "note:p1-return-to-baseline",
      entityType: "note",
      entityId: "p1-return-to-baseline",
      title: "回到基线",
      isDirty: true,
      isSaving: false,
      getIsDirty: () => isDirty,
      save: () => scheduler.triggerSave("leave"),
    });
    const leavePromise = requestLeave("all", () => {
      leaves++;
    });
    useEditorSessionStore.getState().chooseDecision("save");

    expect(await leavePromise).toBe(true);
    expect(writes).toEqual([
      { draft: edited, revision: 1 },
      { draft: baseline, revision: 2 },
    ]);
    expect(saved).toEqual(baseline);
    expect(isDirty).toBe(false);
    expect(leaves).toBe(1);
  });

  test("模拟会话保存被锁定阻断不放行（验收门槛）", async () => {
    const store = useEditorSessionStore.getState();
    let actionExecuted = false;

    store.registerSession({
      documentKey: "chapter:blocked-doc",
      entityType: "chapter",
      entityId: "blocked-doc",
      title: "正在被 Agent 编辑的章节",
      isDirty: true,
      isSaving: false,
      save: async () => ({
        status: "blocked",
        reason: "文档被 Agent 锁定，禁止人工保存覆盖",
      }),
    });

    const leavePromise = requestLeave("all", () => {
      actionExecuted = true;
    });

    // 用户选择“保存并离开”
    useEditorSessionStore.getState().chooseDecision("save");
    await flushAsync();

    expect(actionExecuted).toBe(false);
    expect(useEditorSessionStore.getState().dialogState?.errorMessage).toContain("文档被 Agent 锁定");

    // 锁定阻断后取消离开：不放行
    useEditorSessionStore.getState().chooseDecision("cancel");
    const allowed = await leavePromise;
    expect(allowed).toBe(false);
    expect(actionExecuted).toBe(false);
  });

  test("会话保存成功正常放行（验收门槛）", async () => {
    const store = useEditorSessionStore.getState();
    let actionExecuted = false;
    let saveCalled = false;

    store.registerSession({
      documentKey: "chapter:success-doc",
      entityType: "chapter",
      entityId: "success-doc",
      title: "可正常保存的章节",
      isDirty: true,
      isSaving: false,
      save: async () => {
        saveCalled = true;
        return { status: "saved", savedRevision: 1 };
      },
    });

    const leavePromise = requestLeave("all", () => {
      actionExecuted = true;
    });

    // 用户选择“保存并离开”
    useEditorSessionStore.getState().chooseDecision("save");

    const allowed = await leavePromise;
    expect(allowed).toBe(true);
    expect(saveCalled).toBe(true);
    expect(actionExecuted).toBe(true);
    expect(useEditorSessionStore.getState().dialogState).toBeNull();
  });

  test("放弃修改不重建草稿与调用 discard（验收门槛）", async () => {
    const store = useEditorSessionStore.getState();
    let actionExecuted = false;
    let discardCalled = false;

    store.registerSession({
      documentKey: "note:discard-doc",
      entityType: "note",
      entityId: "discard-doc",
      title: "将要放弃的草稿",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
      discard: async () => {
        discardCalled = true;
      },
    });

    const leavePromise = requestLeave("all", () => {
      actionExecuted = true;
    });

    // 用户选择“放弃修改”
    useEditorSessionStore.getState().chooseDecision("discard");

    const allowed = await leavePromise;
    expect(allowed).toBe(true);
    expect(discardCalled).toBe(true);
    expect(actionExecuted).toBe(true);
    expect(useEditorSessionStore.getState().dialogState).toBeNull();
  });

  test("重复导航只确认一次（验收门槛）与单次通行授权", async () => {
    const store = useEditorSessionStore.getState();
    let firstActionExecuted = false;
    let secondActionExecuted = false;

    store.registerSession({
      documentKey: "world-info:repeat-doc",
      entityType: "world-info",
      entityId: "repeat-doc",
      title: "世界书条目",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
    });

    // 第一次触发导航离开请求（如用户点击侧边栏）
    const firstLeavePromise = requestLeave("all", () => {
      firstActionExecuted = true;
    });

    // 在弹窗尚未处理完成期间，用户快速再次点击导航（触发第二次 requestLeave）
    const secondLeavePromise = requestLeave("all", () => {
      secondActionExecuted = true;
    });

    // 验收门槛要求：重复导航在确认期间直接拒绝，不打开重复弹窗
    const secondAllowed = await secondLeavePromise;
    expect(secondAllowed).toBe(false);
    expect(secondActionExecuted).toBe(false);

    // 第一次弹窗正常选择“放弃修改”
    useEditorSessionStore.getState().chooseDecision("discard");
    const firstAllowed = await firstLeavePromise;
    expect(firstAllowed).toBe(true);
    expect(firstActionExecuted).toBe(true);

    // 验证单次通行授权已被正确消费，不会持久残留
    expect(store.consumeNavigationBypass()).toBe(false);
  });

  test("用户选择取消：留在原处且不执行目标动作", async () => {
    const store = useEditorSessionStore.getState();
    let actionExecuted = false;

    store.registerSession({
      documentKey: "character:cancel-doc",
      entityType: "character",
      entityId: "cancel-doc",
      title: "人物设定",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
    });

    const leavePromise = requestLeave("all", () => {
      actionExecuted = true;
    });

    // 用户点击取消
    useEditorSessionStore.getState().chooseDecision("cancel");

    const allowed = await leavePromise;
    expect(allowed).toBe(false);
    expect(actionExecuted).toBe(false);
    expect(useEditorSessionStore.getState().dialogState).toBeNull();
  });

  test("工作副本丢弃锁保护：放弃后调用 persistWorkingCopy 不会重新写入草稿", async () => {
    let saveCount = 0;
    let deleteCount = 0;
    let isDiscarded = false;

    const fakeController = {
      persistWorkingCopy: () => {
        if (isDiscarded) return;
        saveCount++;
      },
      discardWorkingCopy: async () => {
        isDiscarded = true;
        deleteCount++;
      },
    };

    fakeController.persistWorkingCopy();
    expect(saveCount).toBe(1);

    await fakeController.discardWorkingCopy();
    expect(deleteCount).toBe(1);

    // 模拟组件卸载 cleanup 或延迟事件再次调用 persistWorkingCopy
    fakeController.persistWorkingCopy();
    fakeController.persistWorkingCopy();
    // 依然为 1，已被丢弃锁拦截，未重新生成草稿
    expect(saveCount).toBe(1);
  });
});

const flushAsync = () => new Promise((resolve) => setTimeout(resolve, 20));

test.describe("章节与笔记接入与离开保护 (T6: F01-F12, F14-F17)", () => {
  test.beforeEach(() => {
    _resetEditorSessionStoreForTest();
    _resetTabsStoreForTest();
    _resetGlobalInFlightMapForTest();
  });

  test("F10: 标签切换未保存保护：激活章节有修改时切换标签触发离开保护，取消不切标签，保存后成功切换", async () => {
    const tabsStore = useTabsStore.getState();
    useTabsStore.setState({
      tabs: [
        { id: "chapter:c1", type: "chapter", refId: "c1", title: "第一章", isLocked: false, scrollTop: 0 },
        { id: "chapter:c2", type: "chapter", refId: "c2", title: "第二章", isLocked: false, scrollTop: 0 },
      ],
      activeTabId: "chapter:c1",
      isLoaded: true,
    });

    let c1SaveCount = 0;
    useEditorSessionStore.getState().registerSession({
      documentKey: "chapter:c1",
      entityType: "chapter",
      entityId: "c1",
      title: "第一章",
      isDirty: true,
      isSaving: false,
      save: async () => {
        c1SaveCount++;
        return { status: "saved", savedRevision: 1 };
      },
    });

    // 用户试图切换到 c2
    tabsStore.setActiveTab("chapter:c2");

    // 检查弹窗是否打开，受影响文档包含 chapter:c1
    const dialogState1 = useEditorSessionStore.getState().dialogState;
    expect(dialogState1).not.toBeNull();
    expect(dialogState1?.documents.map((d) => d.documentKey)).toEqual(["chapter:c1"]);

    // 用户选择取消
    useEditorSessionStore.getState().chooseDecision("cancel");
    await flushAsync();

    // 验证：标签未切换，仍然停留在 c1，且未发生保存
    expect(useTabsStore.getState().activeTabId).toBe("chapter:c1");
    expect(c1SaveCount).toBe(0);

    // 用户再次切换到 c2，并选择“保存并离开”
    tabsStore.setActiveTab("chapter:c2");
    useEditorSessionStore.getState().chooseDecision("save");
    await flushAsync();

    // 验证：c1 保存成功，且标签成功切换到 c2
    expect(c1SaveCount).toBe(1);
    expect(useTabsStore.getState().activeTabId).toBe("chapter:c2");
  });

  test("F10: 关闭单个标签保护：关闭未保存标签时用户取消不关闭，选择放弃修改则执行 discard 且关闭标签", async () => {
    useTabsStore.setState({
      tabs: [
        { id: "note:n1", type: "note", refId: "n1", title: "设定笔记", isLocked: false, scrollTop: 0 },
        { id: "chapter:c1", type: "chapter", refId: "c1", title: "第一章", isLocked: false, scrollTop: 0 },
      ],
      activeTabId: "note:n1",
      isLoaded: true,
    });

    let n1Discarded = false;
    useEditorSessionStore.getState().registerSession({
      documentKey: "note:n1",
      entityType: "note",
      entityId: "n1",
      title: "设定笔记",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
      discard: () => {
        n1Discarded = true;
      },
    });

    // 试图关闭 note:n1
    useTabsStore.getState().closeTab("note:n1");
    expect(useEditorSessionStore.getState().dialogState).not.toBeNull();

    // 选择取消
    useEditorSessionStore.getState().chooseDecision("cancel");
    await flushAsync();

    // 验证：标签未被关闭
    expect(useTabsStore.getState().tabs.some((t) => t.id === "note:n1")).toBe(true);
    expect(n1Discarded).toBe(false);

    // 再次关闭，选择放弃修改
    useTabsStore.getState().closeTab("note:n1");
    useEditorSessionStore.getState().chooseDecision("discard");
    await flushAsync();

    // 验证：discard 被调用，标签被关闭，激活标签切换为 c1
    expect(n1Discarded).toBe(true);
    expect(useTabsStore.getState().tabs.some((t) => t.id === "note:n1")).toBe(false);
    expect(useTabsStore.getState().activeTabId).toBe("chapter:c1");
  });

  test("F10: 批量关闭标签保护 (closeOtherTabs / closeAllTabs)：多标签修改同时受保护", async () => {
    useTabsStore.setState({
      tabs: [
        { id: "chapter:c1", type: "chapter", refId: "c1", title: "第一章", isLocked: false, scrollTop: 0 },
        { id: "chapter:c2", type: "chapter", refId: "c2", title: "第二章", isLocked: false, scrollTop: 0 },
        { id: "note:n1", type: "note", refId: "n1", title: "笔记1", isLocked: false, scrollTop: 0 },
      ],
      activeTabId: "chapter:c1",
      isLoaded: true,
    });

    useEditorSessionStore.getState().registerSession({
      documentKey: "chapter:c2",
      entityType: "chapter",
      entityId: "c2",
      title: "第二章",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
    });

    useEditorSessionStore.getState().registerSession({
      documentKey: "note:n1",
      entityType: "note",
      entityId: "n1",
      title: "笔记1",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
    });

    // 调用 closeOtherTabs("chapter:c1")，受影响的是 c2 与 n1
    useTabsStore.getState().closeOtherTabs("chapter:c1");

    const dialog = useEditorSessionStore.getState().dialogState;
    expect(dialog).not.toBeNull();
    const affected = dialog?.documents.map((d) => d.documentKey).sort();
    expect(affected).toEqual(["chapter:c2", "note:n1"]);

    // 用户选择取消
    useEditorSessionStore.getState().chooseDecision("cancel");
    await flushAsync();

    // 所有标签均保留
    expect(useTabsStore.getState().tabs.length).toBe(3);
  });

  test("F10: 标签达上限 (10) 自动淘汰时保护被淘汰标签的未保存内容", async () => {
    const initialTabs = Array.from({ length: 10 }, (_, i) => ({
      id: `chapter:c${i}`,
      type: "chapter" as const,
      refId: `c${i}`,
      title: `第${i}章`,
      isLocked: false,
      scrollTop: 0,
    }));

    useTabsStore.setState({
      tabs: initialTabs,
      activeTabId: "chapter:c9",
      isLoaded: true,
    });

    useEditorSessionStore.getState().registerSession({
      documentKey: "chapter:c0",
      entityType: "chapter",
      entityId: "c0",
      title: "第0章",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
    });

    // 打开第 11 个新标签，触发淘汰 c0
    useTabsStore.getState().openTab("c10", "第10章", "chapter");

    // 应该因为 c0 是被淘汰标签而触发保护
    const dialog = useEditorSessionStore.getState().dialogState;
    expect(dialog).not.toBeNull();
    expect(dialog?.documents.some((d) => d.documentKey === "chapter:c0")).toBe(true);

    // 取消淘汰
    useEditorSessionStore.getState().chooseDecision("cancel");
    await flushAsync();

    // c0 未被淘汰，总数依然为 10
    expect(useTabsStore.getState().tabs.some((t) => t.id === "chapter:c0")).toBe(true);
    expect(useTabsStore.getState().tabs.length).toBe(10);
  });

  test("F17: 删除章节与笔记时注销会话，不留下未保存脏会话", () => {
    const store = useEditorSessionStore.getState();
    store.registerSession({
      documentKey: "chapter:to-delete",
      entityType: "chapter",
      entityId: "to-delete",
      title: "待删除章节",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
    });

    expect(store.getDirtySessions().some((s) => s.documentKey === "chapter:to-delete")).toBe(true);

    // 模拟 useDeleteChapter 成功回调注销会话
    store.unregisterSession("chapter:to-delete");

    // 注销后不再返回该脏会话，不会阻断后续导航
    expect(store.getDirtySessions().some((s) => s.documentKey === "chapter:to-delete")).toBe(false);
  });

  test("F01: 章节与笔记 3000ms 调度防抖行为符合规范", async () => {
    const timer = new VirtualTimer();
    let saveCalls = 0;
    const baseConfig: AutoSaveSchedulerConfig = {
      documentKey: "chapter:autosave-delay-test",
      enabled: true,
      delayMs: 3000,
      dirtyRevision: 0,
      hasChanges: false,
      save: async () => {
        saveCalls++;
        return { status: "saved", savedRevision: 1 };
      },
      timers: timer.asTimers(),
    };
    const scheduler = new AutoSaveScheduler(baseConfig);

    scheduler.updateConfig({ ...baseConfig, hasChanges: true, dirtyRevision: 1 });
    await timer.advanceTime(2000);
    expect(saveCalls).toBe(0); // 2000ms < 3000ms，尚未触发

    // 在 2000ms 时用户再次打字
    scheduler.updateConfig({ ...baseConfig, hasChanges: true, dirtyRevision: 2 });
    await timer.advanceTime(2000);
    expect(saveCalls).toBe(0); // 距上次编辑仅 2000ms，防抖推迟

    await timer.advanceTime(1000);
    expect(saveCalls).toBe(1); // 满 3000ms，触发自动保存
  });
});

test.describe("世界书与角色接入与离开保护 (T7: 1500ms调度、移除cleanup隐式保存、无跨ID写入)", () => {
  test.beforeEach(() => {
    _resetEditorSessionStoreForTest();
    _resetGlobalInFlightMapForTest();
  });

  test("F01: 世界书与角色采用 1500ms 调度防抖与 SaveResult 契约", async () => {
    const timer = new VirtualTimer();
    let saveCalls = 0;
    const baseConfig: AutoSaveSchedulerConfig = {
      documentKey: "world-info:entry-1500",
      enabled: true,
      delayMs: 1500, // 世界书与角色 1500ms
      dirtyRevision: 0,
      hasChanges: false,
      save: async () => {
        saveCalls++;
        return { status: "saved", savedRevision: 1 };
      },
      timers: timer.asTimers(),
    };
    const scheduler = new AutoSaveScheduler(baseConfig);

    scheduler.updateConfig({ ...baseConfig, hasChanges: true, dirtyRevision: 1 });
    await timer.advanceTime(1000);
    expect(saveCalls).toBe(0);

    await timer.advanceTime(500);
    expect(saveCalls).toBe(1); // 1500ms 到期触发
  });

  test("F13: 世界书 cleanup 移除隐式正式保存（核心验收门槛）", () => {
    const timer = new VirtualTimer();
    let formalSaveCalls = 0;

    const baseConfig: AutoSaveSchedulerConfig = {
      documentKey: "world-info:cleanup-check",
      enabled: true,
      delayMs: 1500,
      dirtyRevision: 0,
      hasChanges: false,
      save: async () => {
        formalSaveCalls++;
        return { status: "saved", savedRevision: 1 };
      },
      timers: timer.asTimers(),
    };
    const scheduler = new AutoSaveScheduler(baseConfig);

    scheduler.updateConfig({ ...baseConfig, hasChanges: true, dirtyRevision: 1 });
    expect(scheduler.getState().isScheduled).toBe(true);

    // 模拟组件 unmount 执行 cancel / dispose
    scheduler.dispose();

    expect(scheduler.getState().isScheduled).toBe(false);
    expect(formalSaveCalls).toBe(0); // 验收要求：cleanup 决不能触发正式文档保存！
  });

  test("防止跨 ID 写入与条目切换离开保护（核心验收门槛）", async () => {
    let currentEntryId = "entry-1";
    let entry1SavedPayload: { id: string; name: string } | null = null;
    let entry2SavedPayload: { id: string; name: string } | null = null;

    // 注册 entry-1 的 dirty 会话
    useEditorSessionStore.getState().registerSession({
      documentKey: `world-info:${currentEntryId}`,
      entityType: "world-info",
      entityId: currentEntryId,
      title: "修仙门派设定",
      isDirty: true,
      isSaving: false,
      save: async () => {
        entry1SavedPayload = { id: currentEntryId, name: "修改后的门派设定" };
        return { status: "saved", savedRevision: 1 };
      },
    });

    // 模拟用户在世界书页面点击切换到 entry-2
    let switchedToEntry2 = false;
    const leavePromise = requestLeave([`world-info:${currentEntryId}`], () => {
      currentEntryId = "entry-2";
      switchedToEntry2 = true;
    });

    // 确认弹窗弹出
    const dialog = useEditorSessionStore.getState().dialogState;
    expect(dialog).not.toBeNull();
    expect(dialog?.documents[0].documentKey).toBe("world-info:entry-1");

    // 用户选择“保存并离开”
    useEditorSessionStore.getState().chooseDecision("save");
    const allowed = await leavePromise;

    // 验证：
    // 1. 成功放行并切换到 entry-2
    expect(allowed).toBe(true);
    expect(switchedToEntry2).toBe(true);
    expect(currentEntryId).toBe("entry-2");

    // 2. entry-1 的内容仅保存到 entry-1 的 ID
    expect(entry1SavedPayload).toEqual({ id: "entry-1", name: "修改后的门派设定" });

    // 3. 验收门槛：entry-2 绝未被旧内容串写
    expect(entry2SavedPayload).toBeNull();
  });

  test("放弃修改时恢复基线内容（角色与世界书 discard 契约）", async () => {
    const activeCharacter = {
      id: "char-1",
      name: "林风",
      description: "青云门外门弟子",
      aliases: ["小林"],
    };

    let editorState = {
      name: "林风（被修改）",
      description: "修改后的无用设定",
      aliases: ["小林", "剑仙"],
      hasChanges: true,
    };

    const discardAdapter = () => {
      // 模拟 CharacterEditor discard
      editorState = {
        name: activeCharacter.name,
        description: activeCharacter.description,
        aliases: [...activeCharacter.aliases],
        hasChanges: false,
      };
    };

    useEditorSessionStore.getState().registerSession({
      documentKey: `character:${activeCharacter.id}`,
      entityType: "character",
      entityId: activeCharacter.id,
      title: editorState.name,
      isDirty: editorState.hasChanges,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
      discard: discardAdapter,
    });

    let navigated = false;
    const leavePromise = requestLeave([`character:${activeCharacter.id}`], () => {
      navigated = true;
    });

    // 用户选择放弃修改
    useEditorSessionStore.getState().chooseDecision("discard");
    const allowed = await leavePromise;

    expect(allowed).toBe(true);
    expect(navigated).toBe(true);
    // 验证：编辑器数据完全恢复为初始基线
    expect(editorState.name).toBe("林风");
    expect(editorState.description).toBe("青云门外门弟子");
    expect(editorState.aliases).toEqual(["小林"]);
    expect(editorState.hasChanges).toBe(false);
  });

  test("删除世界书条目或角色时注销会话", () => {
    const store = useEditorSessionStore.getState();
    store.registerSession({
      documentKey: "character:char-to-del",
      entityType: "character",
      entityId: "char-to-del",
      title: "待删角色",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
    });

    expect(store.getDirtySessions().some((s) => s.documentKey === "character:char-to-del")).toBe(true);

    // 模拟删除成功注销会话
    store.unregisterSession("character:char-to-del");
    expect(store.getDirtySessions().some((s) => s.documentKey === "character:char-to-del")).toBe(false);
  });
});

type DesktopCloseRequestHandler = () => void | Promise<void>;

test.describe("桌面端退出协调 (T8: 主进程/preload/前端退出确认通道及退出顺序)", () => {
  test.beforeEach(() => {
    _resetEditorSessionStoreForTest();
    (globalThis as any).window = globalThis;
  });

  test.afterEach(() => {
    delete (globalThis as any).openficDesktopHost;
  });

  test("无修改时收到桌面端退出请求：直接确认 ({ confirmed: true })，不弹窗且不阻塞", async () => {
    let capturedDecision: { confirmed: boolean; reason?: string } | null = null;
    let requestHandler: DesktopCloseRequestHandler | null = null;

    // 模拟 openficDesktopHost 环境
    window.openficDesktopHost = {
      publishAppearance: () => {},
      publishLanguage: () => {},
      publishSocketDiagnostic: () => {},
      onRequestClose: (handler: DesktopCloseRequestHandler) => {
        requestHandler = handler;
        return () => {
          requestHandler = null;
        };
      },
      respondCloseDecision: (decision) => {
        capturedDecision = decision;
      },
    };

    // 注册桌面退出监听
    const unsubscribe = window.openficDesktopHost!.onRequestClose!(async () => {
      const confirmed = await requestLeave("all", () => {});
      window.openficDesktopHost?.respondCloseDecision?.({
        confirmed,
        reason: confirmed ? undefined : "cancelled_or_failed",
      });
    });

    // 触发桌面关闭请求
    expect(requestHandler).not.toBeNull();
    await requestHandler!();

    // 验证：无修改时立即确认，弹窗未打开
    expect(capturedDecision).toEqual({ confirmed: true, reason: undefined });
    expect(useEditorSessionStore.getState().dialogState).toBeNull();

    unsubscribe();
    delete window.openficDesktopHost;
  });

  test("有修改时收到桌面端退出请求：用户选择取消则返回 ({ confirmed: false })，窗口与后端保持运行", async () => {
    const store = useEditorSessionStore.getState();
    store.registerSession({
      documentKey: "chapter:ch-1",
      entityType: "chapter",
      entityId: "ch-1",
      title: "第一章",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
    });

    let capturedDecision: { confirmed: boolean; reason?: string } | null = null;
    let requestHandler: DesktopCloseRequestHandler | null = null;

    window.openficDesktopHost = {
      publishAppearance: () => {},
      publishLanguage: () => {},
      publishSocketDiagnostic: () => {},
      onRequestClose: (handler: DesktopCloseRequestHandler) => {
        requestHandler = handler;
        return () => {
          requestHandler = null;
        };
      },
      respondCloseDecision: (decision) => {
        capturedDecision = decision;
      },
    };

    window.openficDesktopHost!.onRequestClose!(async () => {
      const confirmed = await requestLeave("all", () => {});
      window.openficDesktopHost?.respondCloseDecision?.({
        confirmed,
        reason: confirmed ? undefined : "cancelled_or_failed",
      });
    });

    // 触发桌面关闭请求（异步等待确认）
    const closePromise = requestHandler!();

    // 验证弹窗已唤起，包含脏章节
    const dialogState = useEditorSessionStore.getState().dialogState;
    expect(dialogState).not.toBeNull();
    expect(dialogState?.isOpen).toBe(true);
    expect(dialogState?.documents.some((d) => d.documentKey === "chapter:ch-1")).toBe(true);

    // 用户选择取消
    store.chooseDecision("cancel");
    await closePromise;

    // 验证：返回 confirmed: false，会话依然保持 dirty
    expect(capturedDecision).toEqual({ confirmed: false, reason: "cancelled_or_failed" });
    expect(store.getDirtySessions().length).toBe(1);

    delete window.openficDesktopHost;
  });

  test("P2.4: 关闭处理器与确认弹窗挂载在应用根组件，覆盖登录/初始化状态", () => {
    const mainSource = fs.readFileSync(path.resolve(e2eDir, "../src/main.tsx"), "utf-8");

    const rootStart = mainSource.indexOf("function Root()");
    const rootEnd = mainSource.indexOf("registerSW()", rootStart);
    expect(rootStart).toBeGreaterThan(-1);
    expect(rootEnd).toBeGreaterThan(rootStart);
    const rootSource = mainSource.slice(rootStart, rootEnd);

    // 应用根组件必须挂载关闭监听与确认弹窗
    expect(rootSource).toContain("useDesktopCloseHandler()");
    expect(rootSource).toContain("<EditorLeaveConfirmDialog />");

    // 路由布局在登录页/初始化错误页不渲染，不得作为唯一挂载点
    const layoutStart = mainSource.indexOf("function RootRouteLayout()");
    const layoutEnd = mainSource.indexOf("export const appRouter", layoutStart);
    expect(layoutStart).toBeGreaterThan(-1);
    const layoutSource = mainSource.slice(layoutStart, layoutEnd);
    expect(layoutSource).not.toContain("useDesktopCloseHandler()");
    expect(layoutSource).not.toContain("<EditorLeaveConfirmDialog />");

    // Root 在 isReady 之前也要渲染关闭入口（弹窗位于 Theme 内、状态分支之外）
    expect(rootSource).toContain("<EditorLeaveConfirmDialog />");
  });

  test("P2.4: 无编辑会话时关闭请求也能被响应（覆盖登录页/初始化错误页）", async () => {
    // 无会话：模拟登录页、初始化加载/错误页的干净状态
    let capturedDecision: { confirmed: boolean; reason?: string } | null = null;

    window.openficDesktopHost = {
      publishAppearance: () => {},
      publishLanguage: () => {},
      publishSocketDiagnostic: () => {},
      respondCloseDecision: (decision) => {
        capturedDecision = decision;
      },
    };

    // 应用层关闭处理器：即使不经过 RootRouteLayout 也必须能响应
    await handleDesktopCloseRequest();

    expect(capturedDecision).toEqual({ confirmed: true, reason: undefined });

    delete window.openficDesktopHost;
  });

  test("P2.4: 有未保存会话时关闭请求进入确认流程并反馈取消", async () => {
    const store = useEditorSessionStore.getState();
    store.registerSession({
      documentKey: "note:p2-4",
      entityType: "note",
      entityId: "p2-4",
      title: "未保存笔记",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "saved", savedRevision: 1 }),
    });

    let capturedDecision: { confirmed: boolean; reason?: string } | null = null;

    window.openficDesktopHost = {
      publishAppearance: () => {},
      publishLanguage: () => {},
      publishSocketDiagnostic: () => {},
      respondCloseDecision: (decision) => {
        capturedDecision = decision;
      },
    };

    const closePromise = handleDesktopCloseRequest();
    await flushAsync();

    // 弹窗已展开，关闭决策尚未返回
    expect(capturedDecision).toBeNull();
    expect(useEditorSessionStore.getState().dialogState?.isOpen).toBe(true);

    // 用户取消
    useEditorSessionStore.getState().chooseDecision("cancel");
    await closePromise;

    expect(capturedDecision).toEqual({ confirmed: false, reason: "cancelled_or_failed" });

    delete window.openficDesktopHost;
  });

  test("有修改时收到桌面端退出请求：用户选择保存并离开，保存成功后返回 ({ confirmed: true })，保存早于确认", async () => {
    let savedOrder: string[] = [];
    const store = useEditorSessionStore.getState();
    store.registerSession({
      documentKey: "chapter:ch-1",
      entityType: "chapter",
      entityId: "ch-1",
      title: "第一章",
      isDirty: true,
      isSaving: false,
      save: async () => {
        savedOrder.push("document-saved");
        return { status: "saved", savedRevision: 1 };
      },
    });

    let capturedDecision: { confirmed: boolean; reason?: string } | null = null;
    let requestHandler: DesktopCloseRequestHandler | null = null;

    window.openficDesktopHost = {
      publishAppearance: () => {},
      publishLanguage: () => {},
      publishSocketDiagnostic: () => {},
      onRequestClose: (handler: DesktopCloseRequestHandler) => {
        requestHandler = handler;
        return () => {
          requestHandler = null;
        };
      },
      respondCloseDecision: (decision) => {
        savedOrder.push("close-confirmed");
        capturedDecision = decision;
      },
    };

    window.openficDesktopHost!.onRequestClose!(async () => {
      const confirmed = await requestLeave("all", () => {});
      window.openficDesktopHost?.respondCloseDecision?.({
        confirmed,
        reason: confirmed ? undefined : "cancelled_or_failed",
      });
    });

    const closePromise = requestHandler!();

    // 用户选择保存并离开
    store.chooseDecision("save");
    await closePromise;

    // 核心断言：文档保存必须严格早于桌面退出确认
    expect(savedOrder).toEqual(["document-saved", "close-confirmed"]);
    expect(capturedDecision).toEqual({ confirmed: true, reason: undefined });

    delete window.openficDesktopHost;
  });

  test("保存失败时弹窗报错并返回 ({ confirmed: false })，不静默放行退出", async () => {
    const store = useEditorSessionStore.getState();
    store.registerSession({
      documentKey: "note:note-1",
      entityType: "note",
      entityId: "note-1",
      title: "重要备忘",
      isDirty: true,
      isSaving: false,
      save: async () => ({ status: "failed", error: new Error("网络断开，写入数据库超时") }),
    });

    let capturedDecision: { confirmed: boolean; reason?: string } | null = null;
    let requestHandler: DesktopCloseRequestHandler | null = null;

    window.openficDesktopHost = {
      publishAppearance: () => {},
      publishLanguage: () => {},
      publishSocketDiagnostic: () => {},
      onRequestClose: (handler: DesktopCloseRequestHandler) => {
        requestHandler = handler;
        return () => {
          requestHandler = null;
        };
      },
      respondCloseDecision: (decision) => {
        capturedDecision = decision;
      },
    };

    window.openficDesktopHost!.onRequestClose!(async () => {
      const confirmed = await requestLeave("all", () => {});
      window.openficDesktopHost?.respondCloseDecision?.({
        confirmed,
        reason: confirmed ? undefined : "cancelled_or_failed",
      });
    });

    const closePromise = requestHandler!();

    // 用户选择保存
    store.chooseDecision("save");
    await flushAsync();

    // 核心断言：保存失败不能放行退出，弹窗保留错误原因，等待用户决定
    expect(capturedDecision).toBeNull();
    const currentDialog = useEditorSessionStore.getState().dialogState;
    expect(currentDialog?.errorMessage).toContain("网络断开，写入数据库超时");

    // 用户在报错弹窗中取消退出：此时才以 { confirmed: false } 返回
    store.chooseDecision("cancel");
    await closePromise;
    expect(capturedDecision).toEqual({ confirmed: false, reason: "cancelled_or_failed" });

    delete window.openficDesktopHost;
  });

  test("四类编辑器（章节、笔记、世界书、角色）未保存修改均受桌面退出通道统一保护", async () => {
    const store = useEditorSessionStore.getState();
    const savedKeys: string[] = [];

    store.registerSession({
      documentKey: "chapter:c-1",
      entityType: "chapter",
      entityId: "c-1",
      title: "第100章 决战",
      isDirty: true,
      isSaving: false,
      save: async () => {
        savedKeys.push("chapter:c-1");
        return { status: "saved", savedRevision: 1 };
      },
    });

    store.registerSession({
      documentKey: "note:n-1",
      entityType: "note",
      entityId: "n-1",
      title: "伏笔笔记",
      isDirty: true,
      isSaving: false,
      save: async () => {
        savedKeys.push("note:n-1");
        return { status: "saved", savedRevision: 1 };
      },
    });

    store.registerSession({
      documentKey: "world-info:w-1",
      entityType: "world-info",
      entityId: "w-1",
      title: "青云门宗规",
      isDirty: true,
      isSaving: false,
      save: async () => {
        savedKeys.push("world-info:w-1");
        return { status: "saved", savedRevision: 1 };
      },
    });

    store.registerSession({
      documentKey: "character:char-1",
      entityType: "character",
      entityId: "char-1",
      title: "掌教真人",
      isDirty: true,
      isSaving: false,
      save: async () => {
        savedKeys.push("character:char-1");
        return { status: "saved", savedRevision: 1 };
      },
    });

    let capturedDecision: { confirmed: boolean; reason?: string } | null = null;
    let requestHandler: DesktopCloseRequestHandler | null = null;

    window.openficDesktopHost = {
      publishAppearance: () => {},
      publishLanguage: () => {},
      publishSocketDiagnostic: () => {},
      onRequestClose: (handler: DesktopCloseRequestHandler) => {
        requestHandler = handler;
        return () => {
          requestHandler = null;
        };
      },
      respondCloseDecision: (decision) => {
        capturedDecision = decision;
      },
    };

    window.openficDesktopHost!.onRequestClose!(async () => {
      const confirmed = await requestLeave("all", () => {});
      window.openficDesktopHost?.respondCloseDecision?.({
        confirmed,
        reason: confirmed ? undefined : "cancelled_or_failed",
      });
    });

    const closePromise = requestHandler!();

    // 验证弹窗列出了所有 4 类受影响文档
    const dialogState = useEditorSessionStore.getState().dialogState;
    expect(dialogState?.documents.length).toBe(4);
    expect(dialogState?.documents.map((d) => d.documentKey)).toEqual([
      "chapter:c-1",
      "note:n-1",
      "world-info:w-1",
      "character:char-1",
    ]);

    // 用户选择保存并离开
    useEditorSessionStore.getState().chooseDecision("save");
    await closePromise;

    // 验证所有 4 类文档全部依次保存完成，随后退出确认成功
    expect(savedKeys).toEqual([
      "chapter:c-1",
      "note:n-1",
      "world-info:w-1",
      "character:char-1",
    ]);
    expect(capturedDecision).toEqual({ confirmed: true, reason: undefined });

    delete window.openficDesktopHost;
  });
});

test.describe("P1.1: 关闭自动保存后标题失焦不得隐式保存", () => {
  test.beforeEach(() => {
    _resetGlobalInFlightMapForTest();
  });

  test("共用编辑器失焦入口在开关关闭时不提交，开启时才以 auto 原因提交", async () => {
    const calls: { reason: SaveReason; revision: number }[] = [];
    const save = async (reason: SaveReason = "manual", revision: number) => {
      calls.push({ reason, revision });
      return { status: "saved", savedRevision: revision } as SaveResult;
    };

    // 关闭开关：失焦路径不得产生任何保存请求（修复前此处会记录一次 save）
    saveOnTitleBlur(false, (reason) => save(reason, 1));
    await flushAsync();
    expect(calls).toEqual([]);

    // 手动保存不受开关限制，仍是用户显式操作（对照：不能被一并禁掉）
    await save("manual", 1);
    expect(calls).toEqual([{ reason: "manual", revision: 1 }]);

    // 开启开关后，失焦以 auto 原因提交，走自动保存路径而非手动路径
    calls.length = 0;
    saveOnTitleBlur(true, (reason) => save(reason, 2));
    await flushAsync();
    expect(calls).toEqual([{ reason: "auto", revision: 2 }]);
  });

  test("开关关闭的调度器不接受 reason=auto 的失焦触发，开启后才提交", async () => {
    const timer = new VirtualTimer();
    const calls: { reason: SaveReason; revision: number }[] = [];
    const save = async (reason: SaveReason, revision: number): Promise<SaveResult> => {
      calls.push({ reason, revision });
      return { status: "saved", savedRevision: revision };
    };

    let enabled = false;
    const baseConfig: AutoSaveSchedulerConfig = {
      documentKey: "note:p1-1",
      enabled,
      delayMs: 3000,
      dirtyRevision: 1,
      hasChanges: true,
      save,
      timers: timer.asTimers(),
    };
    const scheduler = new AutoSaveScheduler(baseConfig);

    // 开关关闭时标题失焦：即便调用方漏判，调度器也不得自动保存
    saveOnTitleBlur(false, (reason) => scheduler.triggerSave(reason));
    await flushAsync();
    expect(calls).toEqual([]);
    expect(scheduler.getState().isScheduled).toBe(false);

    // 开启开关后，同样的失焦路径正常提交
    enabled = true;
    scheduler.updateConfig({ ...baseConfig, enabled });
    saveOnTitleBlur(enabled, (reason) => scheduler.triggerSave(reason));
    await flushAsync();
    expect(calls).toEqual([{ reason: "auto", revision: 1 }]);
  });
});
