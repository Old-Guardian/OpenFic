import { useEffect, useRef } from "react";

import type { EditorSessionRegistration } from "../lib/editor-session.types";
import { useEditorSessionStore } from "../lib/editor-session-store";

export function useEditorSession(registration: EditorSessionRegistration) {
  const latestRegistrationRef = useRef(registration);
  latestRegistrationRef.current = registration;

  const documentKey = registration.documentKey;

  useEffect(() => {
    if (!documentKey) return;

    const unregister = useEditorSessionStore.getState().registerSession({
      ...latestRegistrationRef.current,
      save: () => latestRegistrationRef.current.save(),
      discard: () => latestRegistrationRef.current.discard?.(),
      getIsDirty: () =>
        latestRegistrationRef.current.getIsDirty
          ? latestRegistrationRef.current.getIsDirty!()
          : latestRegistrationRef.current.isDirty,
    });

    return () => {
      unregister();
    };
  }, [documentKey]);

  useEffect(() => {
    if (!documentKey) return;

    useEditorSessionStore.getState().updateSession(documentKey, {
      title: registration.title,
      isDirty: registration.isDirty,
      isSaving: registration.isSaving,
    });
  }, [documentKey, registration.isDirty, registration.isSaving, registration.title]);
}
