/**
 * Auto Save Scheduler
 *
 * 负责单文档的自动保存调度、并发控制、单调版本基线和在途排队。
 * 遵循计划文档 T3 规范：
 * 1. 只有真实草稿变化（dirtyRevision 变更）才重置延迟，无关重渲染不推迟保存。
 * 2. 开关关闭、锁定、实体变更或卸载时清理定时器。
 * 3. 按文档保证最多一个在途请求；并发/显式保存先等待在途请求，再检查是否有新修改。
 * 4. 响应只更新已保存基线，不无条件清空当前修改状态。
 * 5. 自动保存完成后若有新版本且仍允许自动保存，安排下一次保存；关闭则保留 dirty。
 * 6. 失败保留 dirty，不创建无限快速重试。
 */

export type SaveReason = "auto" | "manual" | "leave";

export type SaveResult =
  | { status: "saved"; savedRevision: number }
  | { status: "unchanged" }
  | { status: "blocked"; reason: string }
  | { status: "failed"; error: unknown };

export interface TimerFunctions {
  setTimeout: (handler: () => void, timeout?: number) => any;
  clearTimeout: (id: any) => void;
  now?: () => number;
}

export interface AutoSaveSchedulerConfig {
  documentKey: string;
  enabled: boolean;
  delayMs: number;
  dirtyRevision: number;
  hasChanges: boolean;
  blockedReason?: string | null;
  save: (reason: SaveReason, revision: number) => Promise<SaveResult>;
  onStateChange?: (state: AutoSaveSchedulerState) => void;
  timers?: TimerFunctions;
}

export interface AutoSaveSchedulerState {
  isSaving: boolean;
  lastSavedRevision: number | null;
  isScheduled: boolean;
}

const defaultTimers: TimerFunctions = {
  setTimeout: (cb, ms) => setTimeout(cb, ms),
  clearTimeout: (id) => clearTimeout(id),
  now: () => Date.now(),
};

/** 全局按 documentKey 串行化在途保存 Promise */
const globalInFlightMap = new Map<string, Promise<SaveResult>>();

export class AutoSaveScheduler {
  private config: AutoSaveSchedulerConfig;
  private timers: TimerFunctions;
  private timerId: any = null;
  private lastSavedRevision: number | null = null;
  private isSaving: boolean = false;
  private isDisposed: boolean = false;

  constructor(config: AutoSaveSchedulerConfig) {
    this.config = config;
    this.timers = config.timers ?? defaultTimers;
    this.checkInitialScheduling();
  }

  private checkInitialScheduling() {
    if (this.canScheduleAutoSave()) {
      this.startTimer(this.config.delayMs);
    }
  }

  public getState(): AutoSaveSchedulerState {
    return {
      isSaving: this.isSaving,
      lastSavedRevision: this.lastSavedRevision,
      isScheduled: this.timerId !== null,
    };
  }

  private notifyStateChange() {
    if (this.config.onStateChange) {
      this.config.onStateChange(this.getState());
    }
  }

  private canScheduleAutoSave(): boolean {
    if (this.isDisposed) return false;
    if (!this.config.enabled) return false;
    if (!this.config.hasChanges) return false;
    if (this.config.blockedReason) return false;
    if (
      this.lastSavedRevision !== null &&
      this.config.dirtyRevision <= this.lastSavedRevision
    ) {
      return false;
    }
    return true;
  }

  private startTimer(delayMs: number) {
    this.clearTimer();
    this.timerId = this.timers.setTimeout(() => {
      this.timerId = null;
      void this.handleTimerFired();
    }, delayMs);
    this.notifyStateChange();
  }

  private clearTimer() {
    if (this.timerId !== null) {
      this.timers.clearTimeout(this.timerId);
      this.timerId = null;
      this.notifyStateChange();
    }
  }

  private async handleTimerFired() {
    if (!this.canScheduleAutoSave()) {
      return;
    }
    await this.triggerSave("auto");
  }

  /**
   * 更新配置。
   * 处理各种状态变化：dirtyRevision 递增、enabled 切换、锁切换、实体 ID 切换等。
   */
  public updateConfig(nextConfig: AutoSaveSchedulerConfig) {
    if (this.isDisposed) return;
    const prevConfig = this.config;
    this.config = nextConfig;
    if (nextConfig.timers) {
      this.timers = nextConfig.timers;
    }

    // 1. 文档身份发生改变
    if (prevConfig.documentKey !== nextConfig.documentKey) {
      this.clearTimer();
      this.lastSavedRevision = null;
      this.checkInitialScheduling();
      return;
    }

    // 2. 处于锁定状态
    if (nextConfig.blockedReason) {
      this.clearTimer();
      return;
    }

    // 3. enabled 从 true 变为 false：取消定时任务
    if (prevConfig.enabled && !nextConfig.enabled) {
      this.clearTimer();
      return;
    }

    // 4. hasChanges 变为 false
    if (!nextConfig.hasChanges) {
      this.clearTimer();
      return;
    }

    // 5. enabled 从 false 变为 true，或者从锁定中解锁：重新按对应延迟调度
    const wasBlockedOrDisabled = !prevConfig.enabled || Boolean(prevConfig.blockedReason);
    const isNowEnabledAndUnlocked = nextConfig.enabled && !nextConfig.blockedReason;
    if (wasBlockedOrDisabled && isNowEnabledAndUnlocked) {
      if (this.canScheduleAutoSave()) {
        this.startTimer(nextConfig.delayMs);
      }
      return;
    }

    // 6. 真实草稿变更（dirtyRevision 变更）：重置定时器
    if (nextConfig.dirtyRevision !== prevConfig.dirtyRevision) {
      if (this.canScheduleAutoSave()) {
        this.startTimer(nextConfig.delayMs);
      } else {
        this.clearTimer();
      }
      return;
    }

    // 7. 无关重渲染（dirtyRevision 未变，enabled 仍为 true 等）：保持现有定时器，不推迟
    if (this.timerId === null && this.canScheduleAutoSave()) {
      this.startTimer(nextConfig.delayMs);
    }
  }

  /**
   * 手动或自动触发保存
   */
  public async triggerSave(reason: SaveReason = "manual"): Promise<SaveResult> {
    if (this.isDisposed) {
      return { status: "unchanged" };
    }

    // 检查是否被锁定
    if (this.config.blockedReason) {
      return { status: "blocked", reason: this.config.blockedReason };
    }

    // 检查是否有未保存修改
    if (
      !this.config.hasChanges ||
      (this.lastSavedRevision !== null &&
        this.config.dirtyRevision <= this.lastSavedRevision)
    ) {
      return { status: "unchanged" };
    }

    // 取消待执行的自动定时保存
    this.clearTimer();

    const documentKey = this.config.documentKey;

    // 检查该文档是否已有在途保存请求 (Rule 3)
    const existingInFlight = globalInFlightMap.get(documentKey);
    if (existingInFlight) {
      // 等待在途请求完成
      await existingInFlight;

      // 再次检查自身状态
      if (this.isDisposed) {
        return { status: "unchanged" };
      }
      if (this.config.blockedReason) {
        return { status: "blocked", reason: this.config.blockedReason };
      }
      if (
        !this.config.hasChanges ||
        (this.lastSavedRevision !== null &&
          this.config.dirtyRevision <= this.lastSavedRevision)
      ) {
        return { status: "unchanged" };
      }
    }

    // 固定当前保存的快照版本
    const targetRevision = this.config.dirtyRevision;

    this.isSaving = true;
    this.notifyStateChange();

    const task: { promise: Promise<SaveResult> | null } = { promise: null };
    const execute = async (): Promise<SaveResult> => {
      try {
        const result = await this.config.save(reason, targetRevision);
        if (result.status === "saved") {
          if (
            this.lastSavedRevision === null ||
            result.savedRevision > this.lastSavedRevision
          ) {
            this.lastSavedRevision = result.savedRevision;
          }
        }
        return result;
      } catch (error) {
        return { status: "failed", error };
      } finally {
        if (globalInFlightMap.get(documentKey) === task.promise) {
          globalInFlightMap.delete(documentKey);
        }
      }
    };

    task.promise = execute();
    globalInFlightMap.set(documentKey, task.promise);

    let result: SaveResult;
    try {
      result = await task.promise;
    } finally {
      this.isSaving = false;
      this.notifyStateChange();
    }

    // 保存完成后的排队与调度检查 (Rule 5 & Rule 77)
    if (!this.isDisposed) {
      if (result.status === "saved") {
        // 如果在请求期间产生了更新的修改版本 (Rule 5)
        if (this.canScheduleAutoSave()) {
          this.startTimer(this.config.delayMs);
        }
      }
      // 如果保存失败或校验不通过，保留 dirty，不自动启动快速重试定时器 (Rule 77)
    }

    return result;
  }

  public cancel() {
    this.clearTimer();
  }

  public dispose() {
    this.isDisposed = true;
    this.clearTimer();
  }
}

/** 仅供测试使用：清理全局在途请求表 */
export function _resetGlobalInFlightMapForTest() {
  globalInFlightMap.clear();
}
