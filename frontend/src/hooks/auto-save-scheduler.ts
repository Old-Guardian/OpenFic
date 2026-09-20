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

/** 全局逐文档串行队列的队尾 Promise（永不 reject），保证同一 documentKey 的保存严格排队 */
const documentSaveQueues = new Map<string, Promise<void>>();

/**
 * 将保存任务排入该文档的串行队列。
 *
 * 无人在途/排队时立即执行，保持调用方的同步启动语义；已有队列时串接在队尾，
 * 使任一时刻至多一个 save 在途。队尾自身吞掉异常，避免失败请求卡死后续任务。
 */
function enqueueDocumentSave(
  documentKey: string,
  task: () => Promise<SaveResult>,
): Promise<SaveResult> {
  const previous = documentSaveQueues.get(documentKey);
  const run = previous ? previous.then(task, task) : task();

  const tail = run.then(
    () => undefined,
    () => undefined,
  );
  documentSaveQueues.set(documentKey, tail);
  void tail.then(() => {
    if (documentSaveQueues.get(documentKey) === tail) {
      documentSaveQueues.delete(documentKey);
    }
  });

  return run;
}

export class AutoSaveScheduler {
  private config: AutoSaveSchedulerConfig;
  private timers: TimerFunctions;
  private timerId: any = null;
  private lastSavedRevision: number | null = null;
  /** 本实例尚未完成的保存数（含排队等待者），任一大于 0 即对外视为保存中 */
  private pendingSaveCount: number = 0;
  /** 已放弃修改：阻止后续自动调度与尚未发出的排队保存，直到用户再次编辑 */
  private isCancelled: boolean = false;
  /** whenIdle 的等待者，保存全部结束时统一唤醒 */
  private pendingIdleResolvers: (() => void)[] = [];
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
      isSaving: this.pendingSaveCount > 0,
      lastSavedRevision: this.lastSavedRevision,
      isScheduled: this.timerId !== null,
    };
  }

  private notifyStateChange() {
    if (this.config.onStateChange) {
      this.config.onStateChange(this.getState());
    }
  }

  private drainPendingIdle() {
    if (this.pendingSaveCount > 0 || this.pendingIdleResolvers.length === 0) {
      return;
    }
    const resolvers = this.pendingIdleResolvers;
    this.pendingIdleResolvers = [];
    for (const resolve of resolvers) {
      resolve();
    }
  }

  private canScheduleAutoSave(): boolean {
    if (this.isDisposed) return false;
    if (this.isCancelled) return false;
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

    // 6. 真实草稿变更（dirtyRevision 变更）：视为重新开始编辑，解除放弃状态并重置定时器
    if (nextConfig.dirtyRevision !== prevConfig.dirtyRevision) {
      this.isCancelled = false;
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

    // 放弃后不得再提交，直到用户再次编辑重置取消标记
    if (this.isCancelled) {
      return { status: "unchanged" };
    }

    // 检查是否被锁定
    if (this.config.blockedReason) {
      return { status: "blocked", reason: this.config.blockedReason };
    }

    // 自动保存入口即检查开关：关闭后不得提交新的自动保存；手动/离开保存不受限
    if (reason === "auto" && !this.config.enabled) {
      return { status: "unchanged" };
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

    const queuedDocumentKey = this.config.documentKey;

    // 登记在途状态。等待者必须看到 isSaving=true，即使真正的 save 要等队首完成。
    this.pendingSaveCount++;
    this.notifyStateChange();

    // 进入逐文档串行队列。每个 triggerSave 占据独立队列槽位，串行执行各自的 save；
    // 因此多个等待者不会在队首结束后一拥而上，任一时刻至多一个 save 在途 (Rule 3)。
    const result = await enqueueDocumentSave(queuedDocumentKey, async () => {
      // 排队期间可能已取消：文档身份变更时不得把旧内容写入新实体。
      if (this.config.documentKey !== queuedDocumentKey) {
        return { status: "unchanged" };
      }
      if (this.isDisposed) {
        return { status: "unchanged" };
      }
      if (this.config.blockedReason) {
        return { status: "blocked", reason: this.config.blockedReason };
      }
      // 排队等待期间用户可能已放弃修改：不得再提交被放弃的内容
      if (this.isCancelled) {
        return { status: "unchanged" };
      }
      // 排队等待期间开关可能被关闭：自动保存不得在等待结束后继续提交新请求。
      // 已发出的请求允许完成，但此任务此前尚未发出，仍受最新开关约束。
      if (reason === "auto" && !this.config.enabled) {
        return { status: "unchanged" };
      }
      if (
        !this.config.hasChanges ||
        (this.lastSavedRevision !== null &&
          this.config.dirtyRevision <= this.lastSavedRevision)
      ) {
        return { status: "unchanged" };
      }

      // 固定执行时刻的快照版本
      const targetRevision = this.config.dirtyRevision;
      try {
        const saveResult = await this.config.save(reason, targetRevision);
        if (saveResult.status === "saved") {
          if (
            this.lastSavedRevision === null ||
            saveResult.savedRevision > this.lastSavedRevision
          ) {
            this.lastSavedRevision = saveResult.savedRevision;
          }
        }
        return saveResult;
      } catch (error) {
        return { status: "failed", error };
      }
    });

    this.pendingSaveCount--;
    this.notifyStateChange();
    this.drainPendingIdle();

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

  /**
   * 取消调度，进入放弃状态：清理定时器并阻止尚未发出的排队保存。
   * 用户再次编辑（updateConfig 检测到 dirtyRevision 变更）时自动解除。
   */
  public cancel() {
    this.isCancelled = true;
    this.clearTimer();
  }

  /**
   * 恢复被 cancel 暂停的保存能力。
   *
   * 放弃修改需要先 cancel，避免在等待 IndexedDB 清理时继续提交；如果清理失败，
   * 调用方必须 resume，让同一弹窗中的“保存并离开”和后续手动保存仍能工作。
   */
  public resume() {
    if (this.isDisposed || !this.isCancelled) return;
    this.isCancelled = false;
    if (this.canScheduleAutoSave()) {
      this.startTimer(this.config.delayMs);
    }
  }

  /** 等待本实例全部保存（含在途与排队）结束。放弃修改前调用，避免提前放行 */
  public whenIdle(): Promise<void> {
    if (this.pendingSaveCount === 0) {
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      this.pendingIdleResolvers.push(resolve);
    });
  }

  public dispose() {
    this.isDisposed = true;
    this.clearTimer();
  }
}

/** 仅供测试使用：清理全局逐文档串行队列 */
export function _resetGlobalInFlightMapForTest() {
  documentSaveQueues.clear();
}
