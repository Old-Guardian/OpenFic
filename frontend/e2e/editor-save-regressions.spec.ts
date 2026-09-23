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

test("manual save submits the edited content under real StrictMode", async ({ page }) => {
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
