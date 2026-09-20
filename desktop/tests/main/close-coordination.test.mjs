import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { createCloseCoordinator } from "../../dist/main/close-coordinator.js";
import { IpcChannels } from "../../dist/shared/ipc.js";

const testDir = path.dirname(fileURLToPath(import.meta.url));

function createMockWindow() {
  const sentMessages = [];
  let isClosed = false;
  let isDestroyed = false;
  let isMinimizedState = false;
  let focused = false;

  const webContents = {
    send(channel, ...args) {
      sentMessages.push({ channel, args });
    },
  };

  const window = {
    webContents,
    close() {
      isClosed = true;
    },
    destroy() {
      isDestroyed = true;
    },
    isDestroyed() {
      return isDestroyed;
    },
    isMinimized() {
      return isMinimizedState;
    },
    restore() {
      isMinimizedState = false;
    },
    focus() {
      focused = true;
    },
    get isClosed() {
      return isClosed;
    },
    get sentMessages() {
      return sentMessages;
    },
    get focused() {
      return focused;
    },
  };

  return window;
}

test("guest preload 在前端监听器注册前收到关闭请求时会缓存并重放", async () => {
  const preloadSource = fs.readFileSync(
    path.resolve(testDir, "../../dist/preload/frontend-host-preload.cjs"),
    "utf8",
  );
  const ipcListeners = new Map();
  const hostMessages = [];
  let exposedApi = null;
  const electronMock = {
    contextBridge: {
      exposeInMainWorld: (name, api) => {
        assert.equal(name, "openficDesktopHost");
        exposedApi = api;
      },
    },
    ipcRenderer: {
      on: (channel, listener) => {
        ipcListeners.set(channel, listener);
      },
      sendToHost: (channel, ...args) => {
        hostMessages.push({ channel, args });
      },
    },
    webFrame: {
      getZoomFactor: () => 1,
      setZoomFactor: () => {},
    },
  };

  vm.runInNewContext(preloadSource, {
    console,
    exports: {},
    module: { exports: {} },
    Promise,
    require: (specifier) => {
      assert.equal(specifier, "electron");
      return electronMock;
    },
    window: {
      addEventListener: () => {},
    },
  });

  assert.ok(exposedApi);
  assert.deepEqual(hostMessages, [{ channel: "openfic:close-handler-ready", args: [] }]);
  const emitCloseRequest = ipcListeners.get("openfic:request-close");
  assert.equal(typeof emitCloseRequest, "function");

  // 关闭请求先到，React effect 后注册。
  emitCloseRequest();
  let delivered = 0;
  exposedApi.onRequestClose(() => {
    delivered++;
  });
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(delivered, 1, "早期关闭请求必须在监听器注册后重放一次");
});

test("宿主渲染器就绪前的关闭请求会排队，就绪或重载后可靠重放", () => {
  const mockWindow = createMockWindow();
  const coordinator = createCloseCoordinator({
    getWindow: () => mockWindow,
    isBackendRunning: () => true,
    stopBackend: async () => {},
    quitApp: () => {},
  });

  coordinator.handleWindowClose({ preventDefault: () => {} });
  assert.equal(coordinator.isPending(), true);
  assert.equal(mockWindow.sentMessages.length, 0, "preload 就绪前不应发送可能丢失的消息");

  coordinator.handleRendererReady({ send: () => {} });
  assert.equal(mockWindow.sentMessages.length, 0, "不接受未授权渲染器的就绪信号");

  coordinator.handleRendererReady(mockWindow.webContents);
  assert.equal(mockWindow.sentMessages.length, 1, "就绪后必须重放排队的关闭请求");
  coordinator.handleRendererReady(mockWindow.webContents);
  assert.equal(mockWindow.sentMessages.length, 1, "重复就绪信号不得重复发送");

  coordinator.markRendererNotReady();
  coordinator.handleRendererReady(mockWindow.webContents);
  assert.equal(mockWindow.sentMessages.length, 2, "等待确认时重载，新渲染器就绪后必须重放");
});

test("首次触发窗口关闭时阻止关闭，并向前端窗口发送 requestClose 消息", () => {
  const mockWindow = createMockWindow();
  let backendStopped = false;
  let appQuit = false;

  const coordinator = createCloseCoordinator({
    getWindow: () => mockWindow,
    isBackendRunning: () => true,
    stopBackend: async () => {
      backendStopped = true;
    },
    quitApp: () => {
      appQuit = true;
    },
  });
  coordinator.handleRendererReady(mockWindow.webContents);

  let prevented = false;
  coordinator.handleWindowClose({
    preventDefault: () => {
      prevented = true;
    },
  });

  assert.equal(prevented, true, "窗口关闭必须被拦截 preventDefault");
  assert.equal(coordinator.isPending(), true, "协调器应处于等待确认状态");
  assert.equal(coordinator.isConfirmed(), false, "此时尚未确认退出");
  assert.equal(backendStopped, false, "后端进程绝对不能提前停止");
  assert.equal(appQuit, false, "应用不能提前退出");
  assert.equal(mockWindow.sentMessages.length, 1);
  assert.equal(mockWindow.sentMessages[0].channel, IpcChannels.requestClose);
});

test("在确认请求处理中重复触发关闭时，不重复发送 requestClose 消息，并聚焦窗口", () => {
  const mockWindow = createMockWindow();
  const coordinator = createCloseCoordinator({
    getWindow: () => mockWindow,
    isBackendRunning: () => true,
    stopBackend: async () => {},
    quitApp: () => {},
  });
  coordinator.handleRendererReady(mockWindow.webContents);

  coordinator.handleWindowClose({ preventDefault: () => {} });
  assert.equal(mockWindow.sentMessages.length, 1);

  // 第二次触发
  coordinator.handleWindowClose({ preventDefault: () => {} });
  assert.equal(mockWindow.sentMessages.length, 1, "等待中不应发送重复消息");
  assert.equal(mockWindow.focused, true, "重复关闭应聚焦窗口让用户看到确认弹窗");
});

test("前端返回取消或失败 ({ confirmed: false }) 时，保持窗口打开且不停止后端", async () => {
  const mockWindow = createMockWindow();
  let backendStopped = false;
  let appQuit = false;

  const coordinator = createCloseCoordinator({
    getWindow: () => mockWindow,
    isBackendRunning: () => true,
    stopBackend: async () => {
      backendStopped = true;
    },
    quitApp: () => {
      appQuit = true;
    },
  });
  coordinator.handleRendererReady(mockWindow.webContents);

  coordinator.handleWindowClose({ preventDefault: () => {} });

  // 前端用户点击取消
  await coordinator.handleConfirmClose(
    { confirmed: false, reason: "cancelled" },
    mockWindow.webContents,
  );

  assert.equal(coordinator.isPending(), false, "等待状态已重置");
  assert.equal(coordinator.isConfirmed(), false, "未确认退出");
  assert.equal(mockWindow.isClosed, false, "窗口不能关闭");
  assert.equal(backendStopped, false, "后端不能停止");
  assert.equal(appQuit, false, "应用不能退出");

  // 取消后再次触发关闭，应该可以重新发起确认请求
  coordinator.handleWindowClose({ preventDefault: () => {} });
  assert.equal(mockWindow.sentMessages.length, 2, "取消后再次触发可重新发起确认");
});

test("非宿主窗口或未授权的 webContents 发送的 confirmClose 消息被直接忽略", async () => {
  const mockWindow = createMockWindow();
  const foreignWebContents = { send: () => {} };

  const coordinator = createCloseCoordinator({
    getWindow: () => mockWindow,
    isBackendRunning: () => true,
    stopBackend: async () => {},
    quitApp: () => {},
  });
  coordinator.handleRendererReady(mockWindow.webContents);

  coordinator.handleWindowClose({ preventDefault: () => {} });

  await coordinator.handleConfirmClose(
    { confirmed: true },
    foreignWebContents,
  );

  assert.equal(coordinator.isConfirmed(), false, "未授权 sender 不能使协调器确认");
  assert.equal(coordinator.isPending(), true, "依然在等待合法窗口响应");
});

test("前端返回确认 ({ confirmed: true }) 时，允许窗口关闭", async () => {
  const mockWindow = createMockWindow();
  let backendStopped = false;
  let appQuit = false;

  const coordinator = createCloseCoordinator({
    getWindow: () => mockWindow,
    isBackendRunning: () => true,
    stopBackend: async () => {
      backendStopped = true;
    },
    quitApp: () => {
      appQuit = true;
    },
  });
  coordinator.handleRendererReady(mockWindow.webContents);

  coordinator.handleWindowClose({ preventDefault: () => {} });

  // 前端确认已保存或放弃修改
  await coordinator.handleConfirmClose(
    { confirmed: true },
    mockWindow.webContents,
  );

  assert.equal(coordinator.isConfirmed(), true, "退出已确认");
  assert.equal(mockWindow.isClosed, true, "窗口被指示关闭");

  // 随后再次触发窗口关闭事件，不应再被 preventDefault
  let preventedAfterConfirmed = false;
  coordinator.handleWindowClose({
    preventDefault: () => {
      preventedAfterConfirmed = true;
    },
  });
  assert.equal(preventedAfterConfirmed, false, "确认后窗口正常放行关闭");
});

test("app.before-quit 未确认时被拦截，确认后才停止后端并退出应用", async () => {
  const mockWindow = createMockWindow();
  let backendStopped = false;
  let appQuit = false;

  const coordinator = createCloseCoordinator({
    getWindow: () => mockWindow,
    isBackendRunning: () => true,
    stopBackend: async () => {
      backendStopped = true;
    },
    quitApp: () => {
      appQuit = true;
    },
  });
  coordinator.handleRendererReady(mockWindow.webContents);

  let beforeQuitPrevented = false;
  coordinator.handleBeforeQuit({
    preventDefault: () => {
      beforeQuitPrevented = true;
    },
  });

  assert.equal(beforeQuitPrevented, true, "未确认前 before-quit 必须被拦截");
  assert.equal(backendStopped, false, "未确认前不能停止后端");
  assert.equal(appQuit, false, "未确认前不能退出");

  // 前端确认
  await coordinator.handleConfirmClose({ confirmed: true }, mockWindow.webContents);

  // 再次触发 before-quit（例如窗口关闭触发的流程）
  let quitPrevented = false;
  coordinator.handleBeforeQuit({
    preventDefault: () => {
      quitPrevented = true;
    },
  });

  assert.equal(quitPrevented, true, "停止后端时拦截退出以等待异步停止");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(backendStopped, true, "确认后正常停止后端");
  assert.equal(appQuit, true, "后端停止后正常调用 app.quit()");
});
