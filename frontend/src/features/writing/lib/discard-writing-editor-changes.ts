interface PausableAutoSave {
  cancel: () => void;
  resume: () => void;
  whenIdle: () => Promise<void>;
}

interface DiscardWritingEditorChangesOptions {
  autoSave: PausableAutoSave;
  discardWorkingCopy: () => Promise<void>;
  onDiscarded: () => void;
}

/**
 * 丢弃章节/笔记的本地修改。
 *
 * cancel 在清理期间阻止排队保存；只有 IndexedDB 清理成功才算真正放弃。
 * 清理失败时恢复调度器，否则同一离开弹窗中改选保存会被误报为 unchanged。
 */
export async function discardWritingEditorChanges({
  autoSave,
  discardWorkingCopy,
  onDiscarded,
}: DiscardWritingEditorChangesOptions): Promise<void> {
  autoSave.cancel();
  try {
    await autoSave.whenIdle();
    await discardWorkingCopy();
    onDiscarded();
  } catch (error) {
    autoSave.resume();
    throw error;
  }
}
