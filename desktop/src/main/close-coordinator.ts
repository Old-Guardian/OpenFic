import type { BrowserWindow, WebContents } from "electron";
import { IpcChannels, type ConfirmCloseRequest } from "../shared/ipc.js";

export interface CloseCoordinatorOptions {
  getWindow: () => BrowserWindow | null;
  isBackendRunning: () => boolean;
  stopBackend: () => Promise<void>;
  quitApp: () => void;
  log?: (message: string) => void;
}

export interface CloseCoordinator {
  handleWindowClose: (event: { preventDefault: () => void }) => void;
  handleBeforeQuit: (event: { preventDefault: () => void }) => void;
  handleConfirmClose: (request: ConfirmCloseRequest, sender: WebContents) => Promise<void>;
  handleRendererReady: (sender: WebContents) => void;
  markRendererNotReady: () => void;
  requestCloseConfirmation: () => void;
  isConfirmed: () => boolean;
  isPending: () => boolean;
  reset: () => void;
}

export function createCloseCoordinator(options: CloseCoordinatorOptions): CloseCoordinator {
  let isConfirmedToClose = false;
  let isCloseRequestPending = false;
  let isRendererReady = false;
  let wasCloseRequestSent = false;
  let isQuitting = false;

  const log = options.log ?? (() => undefined);

  function sendPendingCloseRequest(): void {
    const window = options.getWindow();
    if (
      !isCloseRequestPending ||
      wasCloseRequestSent ||
      !isRendererReady ||
      !window ||
      window.isDestroyed()
    ) {
      return;
    }
    wasCloseRequestSent = true;
    window.webContents.send(IpcChannels.requestClose);
    log("close confirmation requested from window");
  }

  function requestCloseConfirmation(): void {
    const window = options.getWindow();
    if (isCloseRequestPending) {
      if (window && !window.isDestroyed()) {
        if (window.isMinimized()) window.restore();
        window.focus();
      }
      return;
    }

    if (!window || window.isDestroyed()) {
      isConfirmedToClose = true;
      options.quitApp();
      return;
    }

    if (window.isMinimized()) window.restore();
    window.focus();
    isCloseRequestPending = true;
    wasCloseRequestSent = false;
    sendPendingCloseRequest();
  }

  function handleRendererReady(sender: WebContents): void {
    const window = options.getWindow();
    if (!window || window.isDestroyed() || sender !== window.webContents) {
      log("ignored close-handler-ready from unauthorized or destroyed sender");
      return;
    }
    if (isRendererReady) return;
    isRendererReady = true;
    sendPendingCloseRequest();
  }

  function markRendererNotReady(): void {
    isRendererReady = false;
    // 渲染器重载会销毁旧监听器；若正在等待确认，就在新渲染器就绪后重放。
    if (isCloseRequestPending) wasCloseRequestSent = false;
  }

  function handleWindowClose(event: { preventDefault: () => void }): void {
    if (isConfirmedToClose) {
      return;
    }
    event.preventDefault();
    requestCloseConfirmation();
  }

  function handleBeforeQuit(event: { preventDefault: () => void }): void {
    const window = options.getWindow();
    if (!isConfirmedToClose && window && !window.isDestroyed()) {
      event.preventDefault();
      requestCloseConfirmation();
      return;
    }

    if (isQuitting) return;

    if (!options.isBackendRunning()) {
      isQuitting = true;
      return;
    }

    event.preventDefault();
    isQuitting = true;
    void options.stopBackend().finally(() => options.quitApp());
  }

  async function handleConfirmClose(request: ConfirmCloseRequest, sender: WebContents): Promise<void> {
    const window = options.getWindow();
    if (!window || window.isDestroyed() || sender !== window.webContents) {
      log("ignored confirm-close from unauthorized or destroyed sender");
      return;
    }

    isCloseRequestPending = false;
    wasCloseRequestSent = false;
    if (request.confirmed) {
      log("close confirmed by window: closing window and preparing exit");
      isConfirmedToClose = true;
      window.close();
      if (window.isDestroyed()) {
        options.quitApp();
      }
    } else {
      log(`close cancelled by window: reason=${request.reason ?? "cancel"}`);
      isConfirmedToClose = false;
      isQuitting = false;
    }
  }

  return {
    handleWindowClose,
    handleBeforeQuit,
    handleConfirmClose,
    handleRendererReady,
    markRendererNotReady,
    requestCloseConfirmation,
    isConfirmed: () => isConfirmedToClose,
    isPending: () => isCloseRequestPending,
    reset: () => {
      isConfirmedToClose = false;
      isCloseRequestPending = false;
      isRendererReady = false;
      wasCloseRequestSent = false;
      isQuitting = false;
    },
  };
}
