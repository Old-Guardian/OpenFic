import { create } from "zustand";

import i18n from "@/i18n";

import type {
  EditorSessionRegistration,
  LeaveConfirmDialogState,
  LeaveDecision,
} from "./editor-session.types";

interface EditorSessionStoreState {
  sessions: Map<string, EditorSessionRegistration>;
  dialogState: LeaveConfirmDialogState | null;
  navigationBypassed: boolean;

  registerSession: (session: EditorSessionRegistration) => () => void;
  updateSession: (documentKey: string, patch: Partial<EditorSessionRegistration>) => void;
  unregisterSession: (documentKey: string) => void;
  getDirtySessions: (affectedKeys?: string[] | "all") => EditorSessionRegistration[];
  setNavigationBypassed: (bypassed: boolean) => void;
  consumeNavigationBypass: () => boolean;

  openConfirmDialog: (documents: EditorSessionRegistration[]) => void;
  chooseDecision: (decision: LeaveDecision) => void;
  /** 等待用户在当前弹窗中的下一次决策；弹窗未打开时返回 null */
  waitForDecision: () => Promise<LeaveDecision | null>;
  closeConfirmDialog: () => void;
  setDialogProcessing: (isProcessing: boolean, errorMessage?: string | null) => void;
}

export const useEditorSessionStore = create<EditorSessionStoreState>((set, get) => ({
  sessions: new Map(),
  dialogState: null,
  navigationBypassed: false,

  registerSession: (session: EditorSessionRegistration) => {
    set((state) => {
      const nextSessions = new Map(state.sessions);
      nextSessions.set(session.documentKey, session);
      return { sessions: nextSessions };
    });

    return () => {
      get().unregisterSession(session.documentKey);
    };
  },

  updateSession: (documentKey: string, patch: Partial<EditorSessionRegistration>) => {
    set((state) => {
      const existing = state.sessions.get(documentKey);
      if (!existing) return state;
      const nextSessions = new Map(state.sessions);
      nextSessions.set(documentKey, { ...existing, ...patch });
      return { sessions: nextSessions };
    });
  },

  unregisterSession: (documentKey: string) => {
    set((state) => {
      if (!state.sessions.has(documentKey)) return state;
      const nextSessions = new Map(state.sessions);
      nextSessions.delete(documentKey);
      return { sessions: nextSessions };
    });
  },

  getDirtySessions: (affectedKeys: string[] | "all" = "all") => {
    const { sessions } = get();
    const result: EditorSessionRegistration[] = [];

    for (const [key, session] of sessions) {
      if (affectedKeys !== "all" && !affectedKeys.includes(key)) {
        continue;
      }
      const isDirty = session.getIsDirty ? session.getIsDirty() : session.isDirty;
      if (isDirty) {
        result.push(session);
      }
    }

    return result;
  },

  setNavigationBypassed: (bypassed: boolean) => {
    set({ navigationBypassed: bypassed });
  },

  consumeNavigationBypass: () => {
    const { navigationBypassed } = get();
    if (navigationBypassed) {
      set({ navigationBypassed: false });
      return true;
    }
    return false;
  },

  openConfirmDialog: (documents: EditorSessionRegistration[]) => {
    set({
      dialogState: {
        isOpen: true,
        documents,
        isProcessing: false,
        errorMessage: null,
      },
    });
  },

  chooseDecision: (decision: LeaveDecision) => {
    const pending = decisionResolvers.shift();
    if (pending) {
      pending(decision);
      return;
    }
    // 没有等待者（不应发生）：取消时直接关闭，其余保持原状等待重试
    if (decision === "cancel") {
      set({ dialogState: null });
    }
  },

  waitForDecision: () => {
    const { dialogState } = get();
    if (!dialogState?.isOpen) {
      return Promise.resolve(null);
    }
    return new Promise<LeaveDecision | null>((resolve) => {
      decisionResolvers.push(resolve);
    });
  },

  closeConfirmDialog: () => {
    decisionResolvers.length = 0;
    set({ dialogState: null });
  },

  setDialogProcessing: (isProcessing: boolean, errorMessage: string | null = null) => {
    set((state) => {
      if (!state.dialogState) return state;
      return {
        dialogState: {
          ...state.dialogState,
          isProcessing,
          errorMessage: errorMessage ?? null,
        },
      };
    });
  },
}));

let leaveInProgress = false;

/**
 * 待决决策的等待者队列。每次 waitForDecision 入队，chooseDecision 出队唤醒。
 * 失败后弹窗保持打开，用户再次点击会产生新的等待者，形成可重试的决策循环。
 */
const decisionResolvers: ((decision: LeaveDecision | null) => void)[] = [];

/**
 * 请求离开当前文档或视图。
 * 检查受影响文档是否有未保存修改；若有，弹出统一确认对话框。
 * 只有用户确认“保存并离开”或“放弃修改”且操作成功后，才执行 action。
 * 取消或保存失败留在原处，不执行 action。
 */
export async function requestLeave(
  affectedDocumentKeys: string[] | "all" = "all",
  action: () => Promise<void> | void,
): Promise<boolean> {
  if (leaveInProgress) {
    return false;
  }

  const store = useEditorSessionStore.getState();
  const dirtyDocuments = store.getDirtySessions(affectedDocumentKeys);

  if (dirtyDocuments.length === 0) {
    store.setNavigationBypassed(true);
    try {
      await action();
      return true;
    } finally {
      store.setNavigationBypassed(false);
    }
  }

  leaveInProgress = true;
  try {
    store.openConfirmDialog(dirtyDocuments);

    // 决策循环：覆盖失败后的重试、放弃与取消，弹窗始终有有效的决策消费者。
    while (true) {
      const decision = await store.waitForDecision();
      if (decision === null || decision === "cancel") {
        store.closeConfirmDialog();
        return false;
      }

      if (decision === "save") {
        store.setDialogProcessing(true, null);
        let failureReason: string | null = null;
        for (const doc of dirtyDocuments) {
          try {
            const result = await doc.save();
            if (result.status === "failed" || result.status === "blocked") {
              failureReason =
                result.status === "blocked"
                  ? result.reason
                  : result.error instanceof Error
                    ? result.error.message
                    : i18n.t("editorSession.saveFailed");
              break;
            }
          } catch (error) {
            failureReason =
              error instanceof Error ? error.message : i18n.t("editorSession.saveFailed");
            break;
          }
        }

        if (failureReason !== null) {
          // 保留错误弹窗，恢复按钮，等待用户重试或放弃
          store.setDialogProcessing(false, failureReason);
          continue;
        }

        store.closeConfirmDialog();
        store.setNavigationBypassed(true);
        try {
          await action();
          return true;
        } finally {
          store.setNavigationBypassed(false);
        }
      }

      if (decision === "discard") {
        store.setDialogProcessing(true, null);
        for (const doc of dirtyDocuments) {
          try {
            await doc.discard?.();
          } catch (error) {
            console.error("Failed to discard editor session changes:", doc.documentKey, error);
          }
        }

        store.closeConfirmDialog();
        store.setNavigationBypassed(true);
        try {
          await action();
          return true;
        } finally {
          store.setNavigationBypassed(false);
        }
      }
    }
  } finally {
    leaveInProgress = false;
  }
}

/** 供测试重置 store 状态 */
export function _resetEditorSessionStoreForTest() {
  leaveInProgress = false;
  decisionResolvers.length = 0;
  useEditorSessionStore.setState({
    sessions: new Map(),
    dialogState: null,
    navigationBypassed: false,
  });
}
