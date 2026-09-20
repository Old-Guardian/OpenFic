import { useEffect } from "react";
import { useBlocker } from "react-router";

import { requestLeave, useEditorSessionStore } from "../lib/editor-session-store";

/**
 * 路由离开拦截 Hook。
 * 基于 React Router 的 useBlocker，当存在未保存的编辑会话时拦截站内导航，
 * 触发统一的离开确认流程。
 */
export function useEditorRouteBlocker() {
  const blocker = useBlocker(({ currentLocation, nextLocation }) => {
    // 若已获得一次性通行授权，直接放行
    if (useEditorSessionStore.getState().consumeNavigationBypass()) {
      return false;
    }

    // 相同地址不拦截
    if (
      currentLocation.pathname === nextLocation.pathname &&
      currentLocation.search === nextLocation.search &&
      currentLocation.hash === nextLocation.hash
    ) {
      return false;
    }

    // 检查是否有未保存会话
    const dirtySessions = useEditorSessionStore.getState().getDirtySessions("all");
    return dirtySessions.length > 0;
  });

  useEffect(() => {
    if (blocker.state === "blocked") {
      void (async () => {
        const allowed = await requestLeave("all", () => {
          blocker.proceed();
        });
        if (!allowed) {
          blocker.reset();
        }
      })();
    }
  }, [blocker]);
}
