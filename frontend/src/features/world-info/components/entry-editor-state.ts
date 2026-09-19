export interface EntryEditorState {
  name: string;
  content: string;
  tokenCount: number;
  aliases: string[];
}

export function resolveRemoteEntryEditorState(
  localState: EntryEditorState,
  remoteState: EntryEditorState,
  hasLocalChanges: boolean,
): EntryEditorState {
  return hasLocalChanges ? localState : remoteState;
}
