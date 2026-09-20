import { useEffect } from "react";

import { useEditorSessionStore } from "../lib/editor-session-store";

/**
 * 全局浏览器离开与刷新保护 Hook。
 * 集中监听 beforeunload 事件，若存在任何未保存的编辑会话，弹出浏览器默认的离开警告。
 */
export function useGlobalBeforeUnload() {
  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      const dirtySessions = useEditorSessionStore.getState().getDirtySessions("all");
      if (dirtySessions.length > 0) {
        event.preventDefault();
        event.returnValue = "";
      }
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
    };
  }, []);
}
