import { useEffect } from "react";

import {
  onDesktopRequestClose,
  respondDesktopCloseDecision,
} from "@/lib/desktop-appearance-bridge";

import { requestLeave } from "../lib/editor-session-store";

/**
 * 处理一次桌面端关闭请求：走统一未保存确认流程，并把决策反馈给宿主。
 * 独立为纯函数以便测试，且不依赖 React 挂载位置。
 */
export async function handleDesktopCloseRequest(): Promise<void> {
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
}

/**
 * 桌面端窗口关闭与退出协调 Hook。
 * 在桌面环境中监听 openficDesktopHost.onRequestClose 事件，
 * 触发 requestLeave 统一未保存确认流程，并将决策反馈给桌面宿主。
 *
 * 注意：必须挂载在覆盖初始化、认证与主页面状态的组件上（应用根组件），
 * 否则登录页与初始化错误页会没有关闭请求响应入口。
 */
export function useDesktopCloseHandler() {
  useEffect(() => {
    const unsubscribe = onDesktopRequestClose(handleDesktopCloseRequest);

    return unsubscribe;
  }, []);
}
