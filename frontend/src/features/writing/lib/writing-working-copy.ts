export interface TimestampedWritingWorkingCopy {
  updatedAt: Date;
}

export interface WritingWorkingCopyDraft {
  title: string;
  content: string;
}

export interface RemoteWritingEntity extends WritingWorkingCopyDraft {
  updatedAt: string;
}

export interface LocalWritingWorkingCopy extends WritingWorkingCopyDraft {
  updatedAt: Date;
}

function getTimestamp(value: string): number {
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

export function isRemoteWritingEntityNewer(
  remoteUpdatedAt: string,
  currentUpdatedAt: string,
): boolean {
  return getTimestamp(remoteUpdatedAt) > getTimestamp(currentUpdatedAt);
}

export function getNextWritingWorkingCopyTimestamp(current: Date): Date {
  return new Date(Math.max(Date.now(), current.getTime() + 1));
}

export function isWritingWorkingCopyNewer(
  workingCopy: TimestampedWritingWorkingCopy,
  remoteUpdatedAt: string,
): boolean {
  return workingCopy.updatedAt.getTime() > getTimestamp(remoteUpdatedAt);
}

export function shouldReplaceWritingWorkingCopy(
  next: TimestampedWritingWorkingCopy,
  current: TimestampedWritingWorkingCopy | undefined,
): boolean {
  return !current || next.updatedAt.getTime() >= current.updatedAt.getTime();
}

export function shouldDeleteWritingWorkingCopy(
  workingCopy: TimestampedWritingWorkingCopy,
  remoteUpdatedAt: string,
): boolean {
  return !isWritingWorkingCopyNewer(workingCopy, remoteUpdatedAt);
}

export function areWritingWorkingCopyDraftsEqual(
  left: WritingWorkingCopyDraft,
  right: WritingWorkingCopyDraft,
): boolean {
  return left.title === right.title && left.content === right.content;
}

/**
 * 同时判定“草稿真的发生了编辑”和“草稿相对已保存基线为脏”。
 *
 * 两者不能合并：在途保存 B 时改回旧基线 A，相对旧基线虽然是 clean，
 * 仍然是一次新编辑，必须推进 revision 并持久化最新草稿。
 */
export function classifyWritingDraftChange(
  previous: WritingWorkingCopyDraft,
  next: WritingWorkingCopyDraft,
  saved: WritingWorkingCopyDraft,
): { didChange: boolean; isDirty: boolean } {
  return {
    didChange: !areWritingWorkingCopyDraftsEqual(previous, next),
    isDirty: !areWritingWorkingCopyDraftsEqual(saved, next),
  };
}

export function resolveWritingWorkingCopy(
  remote: RemoteWritingEntity,
  workingCopy: LocalWritingWorkingCopy | null,
): { draft: WritingWorkingCopyDraft; shouldDelete: boolean } {
  if (
    workingCopy &&
    isWritingWorkingCopyNewer(workingCopy, remote.updatedAt) &&
    !areWritingWorkingCopyDraftsEqual(workingCopy, remote)
  ) {
    return {
      draft: { title: workingCopy.title, content: workingCopy.content },
      shouldDelete: false,
    };
  }

  return {
    draft: { title: remote.title, content: remote.content },
    shouldDelete: workingCopy !== null,
  };
}
