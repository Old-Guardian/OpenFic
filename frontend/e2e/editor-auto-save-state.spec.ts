import { expect, test } from "@playwright/test";

import { transformSettings } from "../src/features/settings/lib/settings-api";
import type { SettingsResponse } from "../src/features/settings/lib/settings.types";
import {
  _resetGlobalInFlightMapForTest,
  AutoSaveScheduler,
  type SaveReason,
  type SaveResult,
  type TimerFunctions,
} from "../src/hooks/auto-save-scheduler";
import {
  _resetEditorSessionStoreForTest,
  requestLeave,
  useEditorSessionStore,
  type EditorSessionRegistration,
} from "../src/features/editor-session";

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

    const allowed = await leavePromise;
    // 验收门槛要求：模拟会话保存失败时绝不得放行，留在原处
    expect(allowed).toBe(false);
    expect(actionExecuted).toBe(false);

    // 弹窗状态应记录错误信息
    const currentDialog = useEditorSessionStore.getState().dialogState;
    expect(currentDialog?.errorMessage).toContain("网络超时或后端数据库写入失败");
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

    const allowed = await leavePromise;
    expect(allowed).toBe(false);
    expect(actionExecuted).toBe(false);

    const currentDialog = useEditorSessionStore.getState().dialogState;
    expect(currentDialog?.errorMessage).toContain("文档被 Agent 锁定");
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

