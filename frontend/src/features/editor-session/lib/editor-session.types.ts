import type { SaveResult } from "@/hooks/use-auto-save";

export interface EditorSessionRegistration {
  /** 实体全局唯一标识，如 "chapter:123"、"note:456"、"world-info:789"、"character:abc" */
  documentKey: string;
  /** 实体类别 */
  entityType: "chapter" | "note" | "world-info" | "character";
  /** 实体 ID */
  entityId: string;
  /** 用于离开确认弹窗中展示的文档名称或标题 */
  title: string;
  /** 当前是否有未保存修改 */
  isDirty: boolean;
  /** 当前是否正在保存中 */
  isSaving: boolean;
  /** 动态获取当前是否 dirty（若提供，在检查未保存时优先调用以获取最新状态） */
  getIsDirty?: () => boolean;
  /** 保存适配器函数，返回规范的 SaveResult 契约 */
  save: () => Promise<SaveResult>;
  /** 放弃修改适配器（例如放弃工作副本、重置未保存状态等） */
  discard?: () => Promise<void> | void;
}

export type LeaveDecision = "save" | "discard" | "cancel";

export interface LeaveConfirmDialogState {
  isOpen: boolean;
  documents: EditorSessionRegistration[];
  isProcessing: boolean;
  errorMessage: string | null;
  resolve: ((decision: LeaveDecision) => void) | null;
}
