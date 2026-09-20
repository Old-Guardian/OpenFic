import { useCallback, useRef } from "react";

import {
  deleteWritingWorkingCopy,
  deleteWritingWorkingCopyIfMatches,
  flushWritingWorkingCopy,
  saveWritingWorkingCopy,
  type WritingWorkingCopyType,
} from "@/lib/local-db";

export interface WritingDraft {
  title: string;
  content: string;
}

export interface WritingWorkingCopyController {
  persistWorkingCopy: (draft: WritingDraft, baseUpdatedAt: string, updatedAt: Date) => void;
  clearWorkingCopy: (draft: WritingDraft, updatedAt: Date) => Promise<void>;
  flushWorkingCopy: () => Promise<void>;
  discardWorkingCopy: () => Promise<void>;
}

interface UseWritingWorkingCopyOptions {
  type: WritingWorkingCopyType;
  entityId: string;
}

export function useWritingWorkingCopy({ type, entityId }: UseWritingWorkingCopyOptions) {
  const isDiscardedRef = useRef(false);

  const persistWorkingCopy = useCallback(
    (draft: WritingDraft, baseUpdatedAt: string, updatedAt: Date) => {
      if (isDiscardedRef.current) return;
      void saveWritingWorkingCopy({
        entityId,
        type,
        title: draft.title,
        content: draft.content,
        baseUpdatedAt,
        updatedAt,
      });
    },
    [entityId, type],
  );

  const clearWorkingCopy = useCallback(
    (draft: WritingDraft, updatedAt: Date) =>
      deleteWritingWorkingCopyIfMatches(type, entityId, draft, updatedAt),
    [entityId, type],
  );

  const flushWorkingCopy = useCallback(
    () => flushWritingWorkingCopy(type, entityId),
    [entityId, type],
  );

  const discardWorkingCopy = useCallback(async () => {
    isDiscardedRef.current = true;
    await flushWritingWorkingCopy(type, entityId);
    await deleteWritingWorkingCopy(type, entityId);
  }, [entityId, type]);

  return {
    persistWorkingCopy,
    clearWorkingCopy,
    flushWorkingCopy,
    discardWorkingCopy,
  };
}
