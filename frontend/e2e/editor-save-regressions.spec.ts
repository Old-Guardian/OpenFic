import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { countTokens, preloadTiktokenEncoding } from "../src/lib/tiktoken-utils";
import fixture from "./fixtures/character-save-baseline.json" with { type: "json" };

const frontendDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const backendDir = path.resolve(frontendDir, "../backend");
const backendPython = path.join(
  backendDir,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);

type Snapshot = {
  contentChanges: number;
  saveCalls: { reason: string; revision: number; content: string }[];
  content: string;
  dirty: boolean;
  revision: number;
  tokenCount: number;
};

// This is an intentionally red baseline for T1–T3. Each assertion names the
// user-visible contract and exercises the current implementation directly.
test("T1: initial Markdown mount keeps the saved character clean", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/");
  await expect(page.getByTestId("regression-harness")).toBeVisible();

  const snapshot = (await page.evaluate(() => window.__editorSaveHarness!.snapshot())) as Snapshot;
  expect(snapshot.contentChanges).toBe(0);
  expect(snapshot.dirty).toBe(false);
  expect(snapshot.revision).toBe(0);
  expect(snapshot.content).toBe(fixture.description);
  expect(snapshot.saveCalls).toHaveLength(0);
});

test("T1: lock, focus and cursor movement do not create changes", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/");
  await expect(page.getByTestId("regression-harness")).toBeVisible();
  const editor = page.locator(".ProseMirror");

  await editor.click();
  await editor.press("ArrowRight");
  await editor.press("ArrowLeft");
  await page.evaluate(() => window.__editorSaveHarness!.setLocked(true));
  await expect(editor).toHaveAttribute("contenteditable", "false");
  await page.evaluate(() => window.__editorSaveHarness!.setLocked(false));
  await expect(editor).toHaveAttribute("contenteditable", "true");

  const snapshot = (await page.evaluate(() => window.__editorSaveHarness!.snapshot())) as Snapshot;
  expect(snapshot.contentChanges).toBe(0);
  expect(snapshot.revision).toBe(0);
  expect(snapshot.dirty).toBe(false);
});

test("T1: typing, deletion and paste produce document changes", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/?case=plain");
  await expect(page.getByTestId("regression-harness")).toBeVisible();
  const editor = page.locator(".ProseMirror");

  await editor.fill("Typed text");
  let snapshot = (await page.evaluate(() => window.__editorSaveHarness!.snapshot())) as Snapshot;
  expect(snapshot.content).toContain("Typed text");
  expect(snapshot.dirty).toBe(true);
  const afterTyping = snapshot.contentChanges;

  await editor.press("Backspace");
  snapshot = (await page.evaluate(() => window.__editorSaveHarness!.snapshot())) as Snapshot;
  expect(snapshot.contentChanges).toBeGreaterThan(afterTyping);
  expect(snapshot.content).toContain("Typed tex");
  const afterDeletion = snapshot.contentChanges;

  await page.evaluate(() => {
    const clipboardData = new DataTransfer();
    clipboardData.setData("text/plain", " pasted");
    document
      .querySelector(".ProseMirror")!
      .dispatchEvent(
        new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData }),
      );
  });
  snapshot = (await page.evaluate(() => window.__editorSaveHarness!.snapshot())) as Snapshot;
  expect(snapshot.contentChanges).toBeGreaterThan(afterDeletion);
  expect(snapshot.content).toContain("pasted");
});

test("T1: formatting, undo and redo produce document changes", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/?case=plain");
  await expect(page.getByTestId("regression-harness")).toBeVisible();
  const editor = page.locator(".ProseMirror");
  await editor.fill("format me");
  await editor.press("ControlOrMeta+a");
  const beforeFormat = await page.evaluate(
    () => window.__editorSaveHarness!.snapshot().contentChanges,
  );

  await editor.press("ControlOrMeta+b");
  let snapshot = (await page.evaluate(() => window.__editorSaveHarness!.snapshot())) as Snapshot;
  expect(snapshot.contentChanges).toBeGreaterThan(beforeFormat);
  expect(snapshot.content).toContain("**format me**");
  const afterFormat = snapshot.contentChanges;

  await editor.press("ControlOrMeta+z");
  snapshot = (await page.evaluate(() => window.__editorSaveHarness!.snapshot())) as Snapshot;
  expect(snapshot.contentChanges).toBeGreaterThan(afterFormat);
  expect(snapshot.content).toContain("format me");
  expect(snapshot.content).not.toContain("**format me**");
  const afterUndo = snapshot.contentChanges;

  await editor.press("ControlOrMeta+Shift+z");
  snapshot = (await page.evaluate(() => window.__editorSaveHarness!.snapshot())) as Snapshot;
  expect(snapshot.contentChanges).toBeGreaterThan(afterUndo);
  expect(snapshot.content).toContain("**format me**");
});

test("T1: character remount and discard keep the saved Markdown clean", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/?mode=character");
  await expect(page.getByTestId("entity-regression-harness")).toBeVisible();
  const editor = page.locator(".ProseMirror");
  await expect(editor.locator("li")).toHaveCount(3);
  for (let index = 0; index < 3; index += 1) {
    await page.evaluate(() => window.__entityRegressionHarness!.remount());
    await expect(editor.locator("li")).toHaveCount(3);
    const snapshot = await page.evaluate(() => window.__entityRegressionHarness!.snapshot());
    expect(snapshot).toEqual({ dirty: false, saveCalls: 0 });
  }

  await editor.fill("Unsaved replacement");
  await expect
    .poll(async () => page.evaluate(() => window.__entityRegressionHarness!.snapshot().dirty))
    .toBe(true);
  await page.evaluate(() => window.__entityRegressionHarness!.discard());
  await expect(editor.locator("li")).toHaveCount(3);
  const snapshot = await page.evaluate(() => window.__entityRegressionHarness!.snapshot());
  expect(snapshot).toEqual({ dirty: false, saveCalls: 0 });
});

test("T1: world info discard restores a formatted Markdown baseline", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/?mode=world-info");
  await expect(page.getByTestId("entity-regression-harness")).toBeVisible();
  const editor = page.locator(".ProseMirror");
  await expect(editor.locator("li")).toHaveCount(3);
  await editor.fill("Unsaved replacement");
  await expect
    .poll(async () => page.evaluate(() => window.__entityRegressionHarness!.snapshot().dirty))
    .toBe(true);

  await page.evaluate(() => window.__entityRegressionHarness!.discard());
  await expect(editor.locator("li")).toHaveCount(3);
  const snapshot = await page.evaluate(() => window.__entityRegressionHarness!.snapshot());
  expect(snapshot).toEqual({ dirty: false, saveCalls: 0 });
});

test("T2: manual save submits the edited content under real StrictMode", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/?case=plain");
  await expect(page.getByTestId("regression-harness")).toBeVisible();

  const editedContent = "A newly edited character description";
  await page.locator(".ProseMirror[contenteditable='true']").fill(editedContent);
  await expect
    .poll(async () => page.evaluate(() => window.__editorSaveHarness!.snapshot().dirty))
    .toBe(true);
  await page.getByRole("button", { name: "保存", exact: true }).click();

  await expect
    .poll(
      async () => page.evaluate(() => window.__editorSaveHarness!.snapshot().saveCalls.length),
      { timeout: 5_000 },
    )
    .toBe(1);
  const snapshot = (await page.evaluate(() => window.__editorSaveHarness!.snapshot())) as Snapshot;
  expect(snapshot.saveCalls[0]).toMatchObject({
    reason: "manual",
    revision: snapshot.revision,
    content: editedContent,
  });
});

test("T2: shortcut saves once, clean save does nothing, and disabled auto save stays idle", async ({
  page,
}) => {
  await page.goto("/__editor-save-regressions__/?case=plain");
  await expect(page.getByTestId("regression-harness")).toBeVisible();
  const editor = page.locator(".ProseMirror");
  await editor.focus();
  await editor.press("ControlOrMeta+s");
  expect(await page.evaluate(() => window.__editorSaveHarness!.snapshot().saveCalls)).toHaveLength(
    0,
  );

  await editor.fill("Saved with shortcut");
  await editor.press("ControlOrMeta+s");
  await expect
    .poll(async () => page.evaluate(() => window.__editorSaveHarness!.snapshot().saveCalls.length))
    .toBe(1);
  const snapshot = await page.evaluate(() => window.__editorSaveHarness!.snapshot());
  expect(snapshot.saveCalls[0]).toMatchObject({ reason: "manual", content: "Saved with shortcut" });
  await page.waitForTimeout(1700);
  expect(await page.evaluate(() => window.__editorSaveHarness!.snapshot().saveCalls)).toHaveLength(
    1,
  );
});

test("T2: disabled auto save leaves a dirty draft untouched past its delay", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/?mode=hook-lifecycle");
  await expect(page.getByTestId("hook-lifecycle-harness")).toBeVisible();
  await page.getByTestId("hook-content").fill("Unsaved while disabled");
  await page.waitForTimeout(1700);
  const snapshot = await page.evaluate(() => window.__hookLifecycleHarness!.snapshot());
  expect(snapshot.dirty).toBe(true);
  expect(snapshot.isScheduled).toBe(false);
  expect(snapshot.calls).toHaveLength(0);
});

test("T2: enabled auto save survives rerender and fires once after the original delay", async ({
  page,
}) => {
  await page.goto("/__editor-save-regressions__/?mode=hook-lifecycle");
  await expect(page.getByTestId("hook-lifecycle-harness")).toBeVisible();
  await page.evaluate(() => window.__hookLifecycleHarness!.setEnabled(true));
  await page.getByTestId("hook-content").fill("Auto saved content");
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().isScheduled))
    .toBe(true);
  await page.waitForTimeout(700);
  await page.evaluate(() => window.__hookLifecycleHarness!.rerender());
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().calls.length), {
      timeout: 1300,
    })
    .toBe(1);
  const snapshot = await page.evaluate(() => window.__hookLifecycleHarness!.snapshot());
  expect(snapshot.calls[0]).toMatchObject({ reason: "auto", content: "Auto saved content" });
  expect(snapshot.dirty).toBe(false);
  expect(snapshot.isScheduled).toBe(false);
  await page.waitForTimeout(300);
  expect(await page.evaluate(() => window.__hookLifecycleHarness!.snapshot().calls)).toHaveLength(
    1,
  );
});

test("T2: an in-flight save cannot mark newer content saved", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/?mode=hook-lifecycle");
  await expect(page.getByTestId("hook-lifecycle-harness")).toBeVisible();
  await page.evaluate(() => window.__hookLifecycleHarness!.setSaveMode("deferred"));
  await page.getByTestId("hook-content").fill("First version");
  await page.getByRole("button", { name: "Save" }).click();
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().calls.length))
    .toBe(1);
  await page.getByTestId("hook-content").fill("Second version");
  await page.evaluate(() => {
    window.__hookLifecycleHarness!.setSaveMode("immediate");
    window.__hookLifecycleHarness!.resolveNextSave();
  });
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().isSaving))
    .toBe(false);
  let snapshot = await page.evaluate(() => window.__hookLifecycleHarness!.snapshot());
  expect(snapshot.dirty).toBe(true);
  expect(snapshot.lastSavedRevision).toBe(1);

  await page.getByRole("button", { name: "Save" }).click();
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().dirty))
    .toBe(false);
  snapshot = await page.evaluate(() => window.__hookLifecycleHarness!.snapshot());
  expect(snapshot.calls.map((call) => call.content)).toEqual(["First version", "Second version"]);
  expect(snapshot.lastSavedRevision).toBe(2);
});

test("T2: failure keeps changes, retry saves them, and Agent lock blocks submission", async ({
  page,
}) => {
  await page.goto("/__editor-save-regressions__/?mode=hook-lifecycle");
  await expect(page.getByTestId("hook-lifecycle-harness")).toBeVisible();
  await page.evaluate(() => window.__hookLifecycleHarness!.setSaveMode("failed"));
  await page.getByTestId("hook-content").fill("Retry me");
  await page.getByRole("button", { name: "Save" }).click();
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().calls.length))
    .toBe(1);
  expect((await page.evaluate(() => window.__hookLifecycleHarness!.snapshot())).dirty).toBe(true);

  await page.evaluate(() => window.__hookLifecycleHarness!.setLocked(true));
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().locked))
    .toBe(true);
  const blocked = await page.evaluate(() => window.__hookLifecycleHarness!.manualSave());
  expect(blocked.status).toBe("blocked");
  expect((await page.evaluate(() => window.__hookLifecycleHarness!.snapshot())).calls).toHaveLength(
    1,
  );

  await page.evaluate(() => {
    window.__hookLifecycleHarness!.setLocked(false);
    window.__hookLifecycleHarness!.setSaveMode("immediate");
  });
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().locked))
    .toBe(false);
  await page.getByRole("button", { name: "Save" }).click();
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().dirty))
    .toBe(false);
  expect((await page.evaluate(() => window.__hookLifecycleHarness!.snapshot())).calls).toHaveLength(
    2,
  );
});

test("T2: remount cancels pending auto save and a new instance can save", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/?mode=hook-lifecycle");
  await expect(page.getByTestId("hook-lifecycle-harness")).toBeVisible();
  await page.evaluate(() => window.__hookLifecycleHarness!.setEnabled(true));
  await page.getByTestId("hook-content").fill("Abandoned edit");
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().isScheduled))
    .toBe(true);
  await page.evaluate(() => window.__hookLifecycleHarness!.remount());
  await expect(page.getByTestId("hook-content")).toHaveValue("Content for character:A");
  await page.waitForTimeout(1700);
  expect((await page.evaluate(() => window.__hookLifecycleHarness!.snapshot())).calls).toHaveLength(
    0,
  );

  await page.getByTestId("hook-content").fill("New edit");
  await page.getByRole("button", { name: "Save" }).click();
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().calls.length))
    .toBe(1);
  expect(
    (await page.evaluate(() => window.__hookLifecycleHarness!.snapshot())).calls[0].content,
  ).toBe("New edit");
});

test("T2: remounted same document waits for an old in-flight save", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/?mode=hook-lifecycle");
  await expect(page.getByTestId("hook-lifecycle-harness")).toBeVisible();
  await page.evaluate(() => window.__hookLifecycleHarness!.setSaveMode("deferred"));
  await page.getByTestId("hook-content").fill("Old instance");
  await page.getByRole("button", { name: "Save" }).click();
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().calls.length))
    .toBe(1);
  await page.evaluate(() => window.__hookLifecycleHarness!.remount());
  await expect(page.getByTestId("hook-content")).toHaveValue("Content for character:A");
  await page.getByTestId("hook-content").fill("New instance");
  await page.getByRole("button", { name: "Save" }).click();
  expect((await page.evaluate(() => window.__hookLifecycleHarness!.snapshot())).calls).toHaveLength(
    1,
  );
  expect((await page.evaluate(() => window.__hookLifecycleHarness!.snapshot())).isSaving).toBe(
    true,
  );

  await page.evaluate(() => window.__hookLifecycleHarness!.resolveNextSave());
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().calls.length))
    .toBe(2);
  let snapshot = await page.evaluate(() => window.__hookLifecycleHarness!.snapshot());
  expect(snapshot.calls.map((call) => call.content)).toEqual(["Old instance", "New instance"]);
  expect(snapshot.maxInFlightForCurrentDocument).toBe(1);
  expect(snapshot.isSaving).toBe(true);
  expect(snapshot.dirty).toBe(true);

  await page.evaluate(() => window.__hookLifecycleHarness!.resolveNextSave());
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().isSaving))
    .toBe(false);
  snapshot = await page.evaluate(() => window.__hookLifecycleHarness!.snapshot());
  expect(snapshot.dirty).toBe(false);
  expect(snapshot.isSaving).toBe(false);
});

test("T2: disposed instance does not submit its queued old edit", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/?mode=hook-lifecycle");
  await expect(page.getByTestId("hook-lifecycle-harness")).toBeVisible();
  await page.evaluate(() => window.__hookLifecycleHarness!.setSaveMode("deferred"));
  await page.getByTestId("hook-content").fill("Old first edit");
  await page.getByRole("button", { name: "Save" }).click();
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().calls.length))
    .toBe(1);
  await page.getByTestId("hook-content").fill("Old queued edit");
  await page.getByRole("button", { name: "Save" }).click();
  await page.evaluate(() => window.__hookLifecycleHarness!.remount());
  await expect(page.getByTestId("hook-content")).toHaveValue("Content for character:A");

  await page.evaluate(() => window.__hookLifecycleHarness!.resolveNextSave());
  await page.waitForTimeout(100);
  expect((await page.evaluate(() => window.__hookLifecycleHarness!.snapshot())).calls).toHaveLength(
    1,
  );
  await page.evaluate(() => window.__hookLifecycleHarness!.setSaveMode("immediate"));
  await page.getByTestId("hook-content").fill("Fresh edit");
  await page.getByRole("button", { name: "Save" }).click();
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().calls.length))
    .toBe(2);
  const snapshot = await page.evaluate(() => window.__hookLifecycleHarness!.snapshot());
  expect(snapshot.calls.map((call) => call.content)).toEqual(["Old first edit", "Fresh edit"]);
  expect(snapshot.maxInFlightForCurrentDocument).toBe(1);
});

test("T2: switching documents never submits A content as B or accepts A state", async ({
  page,
}) => {
  await page.goto("/__editor-save-regressions__/?mode=hook-lifecycle");
  await expect(page.getByTestId("hook-lifecycle-harness")).toBeVisible();
  await page.evaluate(() => window.__hookLifecycleHarness!.setSaveMode("deferred"));
  await page.getByTestId("hook-content").fill("A draft one");
  await page.getByTestId("hook-content").fill("A draft two");
  await page.getByRole("button", { name: "Save" }).click();
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().calls.length))
    .toBe(1);

  await page.evaluate(() => {
    window.__hookLifecycleHarness!.setSaveMode("immediate");
    window.__hookLifecycleHarness!.switchDocument("character:B");
  });
  await expect(page.getByTestId("hook-content")).toHaveValue("Content for character:B");
  await page.getByTestId("hook-content").fill("B draft");
  await page.getByRole("button", { name: "Save" }).click();
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().calls.length))
    .toBe(2);
  await page.evaluate(() => window.__hookLifecycleHarness!.resolveNextSave());
  await expect
    .poll(async () => page.evaluate(() => window.__hookLifecycleHarness!.snapshot().isSaving))
    .toBe(false);
  const snapshot = await page.evaluate(() => window.__hookLifecycleHarness!.snapshot());
  expect(snapshot.calls.map(({ documentKey, content }) => [documentKey, content])).toEqual([
    ["character:A", "A draft two"],
    ["character:B", "B draft"],
  ]);
  expect(snapshot.documentKey).toBe("character:B");
  expect(snapshot.lastSavedRevision).toBe(1);
  expect(snapshot.dirty).toBe(false);
});

test("character service and editor count the same saved Markdown", async () => {
  // Pure logic check: no Vite server or browser is needed for this case.
  await preloadTiktokenEncoding("o200k_base");
  const frontendCount = countTokens(fixture.description);
  const script = [
    "import json, sys",
    "from app.storage.services.character_service import calculate_token_count",
    "print(json.dumps(calculate_token_count(sys.stdin.read())))",
  ].join("\n");
  const backendCount = Number(
    execFileSync(backendPython, ["-c", script], {
      cwd: backendDir,
      encoding: "utf8",
      input: fixture.description,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    })
      .trim()
      .split(/\r?\n/)
      .at(-1),
  );

  expect(frontendCount).toBeGreaterThan(0);
  expect(backendCount).toBe(frontendCount);
});
