import { expect, test } from "@playwright/test";

import cases from "../../tests/fixtures/character-token-count-cases.json" with { type: "json" };
import { countTokens, preloadTiktokenEncoding } from "../src/lib/tiktoken-utils";

const saved = cases.cases.find((item) => item.id === "nested_markdown_list")!;
const edited = cases.cases.find((item) => item.id === "english")!;

test("character descriptions match the shared o200k_base token counts", async () => {
  expect(cases.encoding).toBe("o200k_base");
  await preloadTiktokenEncoding(cases.encoding);

  for (const { id, content, expected } of cases.cases) {
    expect(countTokens(content), id).toBe(expected);
  }
});

test("character list keeps the saved count while the editor shows a draft", async ({ page }) => {
  await page.goto("/__editor-save-regressions__/?mode=character-tokens");
  const list = page.getByTestId("character-token-list");
  const editor = page.getByTestId("character-token-editor");
  const savedCount = editor.getByText(new RegExp(`^${saved.expected}\\s`));
  const editedCount = editor.getByText(new RegExp(`^${edited.expected}\\s`));

  await expect(list).toContainText(String(saved.expected));
  await expect(savedCount).toBeVisible();

  await editor.locator(".ProseMirror").fill(edited.content);
  await expect(editedCount).toBeVisible();
  await expect(list).toContainText(String(saved.expected));

  await page.evaluate(() => window.__entityRegressionHarness!.discard());
  await expect(savedCount).toBeVisible();
  await expect(list).toContainText(String(saved.expected));

  await editor.locator(".ProseMirror").fill(edited.content);
  await editor.getByRole("button", { name: "保存", exact: true }).click();
  await expect(editedCount).toBeVisible();
  await expect(list).toContainText(String(edited.expected));

  await page.evaluate(() => window.__entityRegressionHarness!.refreshList());
  await expect(list).toContainText(String(edited.expected));
});
