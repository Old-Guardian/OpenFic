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
test("initial Markdown mount keeps the saved character clean", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/");
  await expect(page.getByTestId("regression-harness")).toBeVisible();

  const snapshot = await page.evaluate(() => window.__editorSaveHarness!.snapshot()) as Snapshot;
  expect(snapshot.contentChanges).toBe(0);
  expect(snapshot.dirty).toBe(false);
  expect(snapshot.revision).toBe(0);
  expect(snapshot.content).toBe(fixture.description);
  expect(snapshot.saveCalls).toHaveLength(0);
});

test("manual save submits the edited content under real StrictMode", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/?case=plain");
  await expect(page.getByTestId("regression-harness")).toBeVisible();

  const editedContent = "A newly edited character description";
  await page.locator(".ProseMirror[contenteditable='true']").fill(editedContent);
  await expect.poll(async () => page.evaluate(() => window.__editorSaveHarness!.snapshot().dirty)).toBe(true);
  await page.getByRole("button", { name: "保存", exact: true }).click();

  await expect.poll(
    async () => page.evaluate(() => window.__editorSaveHarness!.snapshot().saveCalls.length),
    { timeout: 5_000 },
  ).toBe(1);
  const snapshot = await page.evaluate(() => window.__editorSaveHarness!.snapshot()) as Snapshot;
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
  const backendCount = Number(execFileSync(backendPython, ["-c", script], {
    cwd: backendDir,
    encoding: "utf8",
    input: fixture.description,
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
  }).trim().split(/\r?\n/).at(-1));

  expect(frontendCount).toBeGreaterThan(0);
  expect(backendCount).toBe(frontendCount);
});
