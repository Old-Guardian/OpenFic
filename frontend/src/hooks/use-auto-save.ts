import { useCallback, useEffect, useRef, useState } from "react";

import {
  AutoSaveScheduler,
  type AutoSaveSchedulerConfig,
  type AutoSaveSchedulerState,
  type SaveReason,
  type SaveResult,
} from "./auto-save-scheduler";

export type { SaveReason, SaveResult };

export interface UseAutoSaveOptions {
  /** 文档全局唯一 Key，推荐形如 "chapter:123"、"character:456" */
  documentKey: string;
  /** 是否启用自动保存（受全局开关及上下文控制） */
  enabled?: boolean;
  /** 防抖延迟毫秒数，小说章节/笔记通常为 3000ms，世界书/角色通常为 1500ms */
  delayMs?: number;
  /** 单调递增的编辑版本号，仅在草稿真正发生变动时自增 */
  dirtyRevision: number;
  /** 当前是否有修改 */
  hasChanges: boolean;
  /** 若处于不可保存状态（如 Agent 锁定），传入锁定原因 */
  blockedReason?: string | null;
  /** 保存适配器函数，返回规范的 SaveResult 契约 */
  save: (reason: SaveReason, revision: number) => Promise<SaveResult>;
}

export interface UseAutoSaveReturn {
  /** 手动或离开时触发保存 */
  save: (reason?: SaveReason) => Promise<SaveResult>;
  /** 取消当前待执行的自动保存定时器 */
  cancel: () => void;
  /** 是否正在执行保存请求 */
  isSaving: boolean;
  /** 最近一次成功保存的版本号 */
  lastSavedRevision: number | null;
  /** 当前是否已有安排在排队的自动保存计时器 */
  isScheduled: boolean;
}

export function useAutoSave({
  documentKey,
  enabled = true,
  delayMs = 3000,
  dirtyRevision,
  hasChanges,
  blockedReason = null,
  save,
}: UseAutoSaveOptions): UseAutoSaveReturn {
  const saveRef = useRef(save);
  saveRef.current = save;

  const [state, setState] = useState<AutoSaveSchedulerState>({
    isSaving: false,
    lastSavedRevision: null,
    isScheduled: false,
  });

  const schedulerRef = useRef<AutoSaveScheduler | null>(null);

  if (schedulerRef.current === null) {
    const initialConfig: AutoSaveSchedulerConfig = {
      documentKey,
      enabled,
      delayMs,
      dirtyRevision,
      hasChanges,
      blockedReason,
      save: (reason, rev) => saveRef.current(reason, rev),
      onStateChange: (nextState) => {
        setState(nextState);
      },
    };
    schedulerRef.current = new AutoSaveScheduler(initialConfig);
  }

  useEffect(() => {
    schedulerRef.current?.updateConfig({
      documentKey,
      enabled,
      delayMs,
      dirtyRevision,
      hasChanges,
      blockedReason,
      save: (reason, rev) => saveRef.current(reason, rev),
      onStateChange: (nextState) => {
        setState(nextState);
      },
    });
  }, [blockedReason, delayMs, dirtyRevision, documentKey, enabled, hasChanges]);

  useEffect(() => {
    return () => {
      schedulerRef.current?.dispose();
    };
  }, []);

  const triggerSave = useCallback(async (reason: SaveReason = "manual") => {
    if (!schedulerRef.current) {
      return { status: "unchanged" as const };
    }
    return schedulerRef.current.triggerSave(reason);
  }, []);

  const cancel = useCallback(() => {
    schedulerRef.current?.cancel();
  }, []);

  return {
    save: triggerSave,
    cancel,
    isSaving: state.isSaving,
    lastSavedRevision: state.lastSavedRevision,
    isScheduled: state.isScheduled,
  };
}
