import { useEffect } from "react";

import {
  onDesktopRequestClose,
  respondDesktopCloseDecision,
} from "@/lib/desktop-appearance-bridge";

import { requestLeave } from "../lib/editor-session-store";

/**
 * 桌面端窗口关闭与退出协调 Hook。
 * 在桌面环境中监听 openficDesktopHost.onRequestClose 事件，
 * 触发 requestLeave 统一未保存确认流程，并将决策反馈给桌面宿主。
 */
export function useDesktopCloseHandler() {
  useEffect(() => {
    const unsubscribe = onDesktopRequestClose(async () => {
      try {
        const confirmed = await requestLeave("all", async () => {
          // 确认退出（已保存或放弃修改）
        });
        respondDesktopCloseDecision({
          confirmed,
          reason: confirmed ? undefined : "cancelled_or_failed",
        });
      } catch (error) {
        respondDesktopCloseDecision({
          confirmed: false,
          reason: error instanceof Error ? error.message : "unknown_error",
        });
      }
    });

    return unsubscribe;
  }, []);
}
