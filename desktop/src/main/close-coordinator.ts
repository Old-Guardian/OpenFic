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
  requestCloseConfirmation: () => void;
  isConfirmed: () => boolean;
  isPending: () => boolean;
  reset: () => void;
}

export function createCloseCoordinator(options: CloseCoordinatorOptions): CloseCoordinator {
  let isConfirmedToClose = false;
  let isCloseRequestPending = false;
  let isQuitting = false;

  const log = options.log ?? (() => undefined);

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
    window.webContents.send(IpcChannels.requestClose);
    log("close confirmation requested from window");
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
    requestCloseConfirmation,
    isConfirmed: () => isConfirmedToClose,
    isPending: () => isCloseRequestPending,
    reset: () => {
      isConfirmedToClose = false;
      isCloseRequestPending = false;
      isQuitting = false;
    },
  };
}
