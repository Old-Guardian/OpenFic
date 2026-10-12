import { expect, test, type Page, type Route } from "@playwright/test";

import zhTranslations from "../src/i18n/locales/zh-CN.json" with { type: "json" };

const pageErrors = new WeakMap<Page, string[]>();
test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  pageErrors.set(page, errors);
  page.on("pageerror", (error) => errors.push(error.message));
});
test.afterEach(async ({ page }) => {
  expect(pageErrors.get(page)).toEqual([]);
});

function historyPage(content: string | null, cursor: number | null, hasMore = false) {
  return {
    messages: content
      ? [
          {
            id: content,
            type: "user_request",
            role: "user",
            display: "list",
            status: "completed",
            timestamp: Date.UTC(2026, 9, 11),
            content,
          },
        ]
      : [],
    messagesCursor: cursor,
    messagesHasMore: hasMore,
  };
}

async function openFixture(page: Page, mode = "empty") {
  await page.route("**/__pagination_fixture?*", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"></head><body><div id="root"></div><script type="module">
import RefreshRuntime from "/@react-refresh";
RefreshRuntime.injectIntoGlobalHook(window);
window.$RefreshReg$ = () => {};
window.$RefreshSig$ = () => type => type;
window.__vite_plugin_react_preamble_installed__ = true;
</script><script type="module" src="/e2e/agent-message-pagination.fixture.tsx"></script></body></html>`,
    }),
  );
  await page.goto(`/__pagination_fixture?mode=${mode}`);
}

test("empty first page automatically loads earlier visible messages", async ({ page }) => {
  const requests: string[] = [];
  await page.route("**/__pagination_page/**", async (route) => {
    requests.push(route.request().url());
    await route.fulfill({ json: historyPage("Earlier history", 200) });
  });
  await openFixture(page);
  await expect(page.getByText("Earlier history", { exact: true })).toBeVisible();
  expect(requests).toHaveLength(1);
});

test("consecutive empty pages advance until visible history without parallel requests", async ({
  page,
}) => {
  const cursors: number[] = [];
  await page.route("**/__pagination_page/**", async (route) => {
    const cursor = Number(new URL(route.request().url()).searchParams.get("cursor"));
    cursors.push(cursor);
    if (cursor === 300 || cursor === 250) {
      await route.fulfill({ json: historyPage(null, cursor - 50, true) });
    } else {
      await route.fulfill({ json: historyPage("Visible after empty pages", 150, true) });
    }
  });
  await openFixture(page);
  await expect(page.getByText("Visible after empty pages", { exact: true })).toBeVisible();
  expect(cursors).toEqual([300, 250, 200]);
});

test("empty older pages advance in one load and preserve the current message anchor", async ({
  page,
}) => {
  const cursors: number[] = [];
  await page.route("**/__pagination_page/**", async (route) => {
    const cursor = Number(new URL(route.request().url()).searchParams.get("cursor"));
    cursors.push(cursor);
    await route.fulfill({
      json:
        cursor === 300 ? historyPage(null, 250, true) : historyPage("Older visible history", 200),
    });
  });
  await openFixture(page, "older");
  await expect(page.getByText("Current message 11", { exact: true })).toBeVisible();
  expect(cursors).toEqual([]);
  const viewport = page.locator(".ai-sidebar-messages");
  await viewport.hover();
  await page.mouse.wheel(0, -3000);
  await expect.poll(() => cursors.length).toBe(2);
  await expect(page.getByTestId("pagination-state")).toContainText('"isLoadingEarlier":false');
  await expect(page.getByText("Current message 0", { exact: true })).toBeVisible();
  expect(cursors).toEqual([300, 250]);
});

test("failure after an empty page waits for retry at the advanced cursor", async ({ page }) => {
  const cursors: number[] = [];
  await page.route("**/__pagination_page/**", async (route) => {
    const cursor = Number(new URL(route.request().url()).searchParams.get("cursor"));
    cursors.push(cursor);
    if (cursor === 300) await route.fulfill({ json: historyPage(null, 250, true) });
    else if (cursors.length === 2) await route.fulfill({ status: 500 });
    else await route.fulfill({ json: historyPage("Retried history", 200) });
  });
  await openFixture(page);
  await expect(page.getByTestId("pagination-state")).toContainText('"hasEarlierError":true');
  expect(cursors).toEqual([300, 250]);
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.getByText("Retried history", { exact: true })).toBeVisible();
  expect(cursors).toEqual([300, 250, 250]);
});

test("an exhausted empty history stops loading", async ({ page }) => {
  let requests = 0;
  await page.route("**/__pagination_page/**", async (route) => {
    requests += 1;
    await route.fulfill({ json: historyPage(null, null) });
  });
  await openFixture(page);
  await expect(page.getByTestId("pagination-state")).toContainText('"messagesHasMore":false');
  await expect(page.getByTestId("pagination-state")).toContainText('"isLoadingEarlier":false');
  expect(requests).toBe(1);
});

test("a non-advancing cursor cannot loop on empty history", async ({ page }) => {
  let requests = 0;
  await page.route("**/__pagination_page/**", async (route) => {
    requests += 1;
    await route.fulfill({ json: historyPage(null, 300, true) });
  });
  await openFixture(page);
  await expect(page.getByTestId("pagination-state")).toContainText('"messagesHasMore":false');
  expect(requests).toBe(1);
});

test("switching sessions discards a pending empty-page response", async ({ page }) => {
  let pending: Route | undefined;
  const requests: string[] = [];
  await page.route("**/__pagination_page/**", async (route) => {
    const url = route.request().url();
    requests.push(url);
    if (url.includes("/A?")) pending = route;
    else await route.fulfill({ json: historyPage("Session B history", 900) });
  });
  await openFixture(page);
  await expect.poll(() => Boolean(pending)).toBe(true);
  await page.getByRole("button", { name: "Switch session" }).click();
  await expect(page.getByText("Session B history", { exact: true })).toBeVisible();
  await pending!.fulfill({ json: historyPage(null, 250, true) });
  await expect(page.getByTestId("pagination-state")).toContainText('"sessionId":"B"');
  await expect(page.getByTestId("pagination-state")).toContainText('"messagesHasMore":false');
  expect(requests.filter((url) => url.includes("/A?"))).toHaveLength(1);
});

test("streaming pauses follow on upward scroll and resumes after returning to bottom", async ({
  page,
}) => {
  await openFixture(page, "streaming");
  const container = page.locator(".ai-sidebar-messages");
  await expect(page.getByTestId("scroll-state")).toHaveText("true");
  await expect
    .poll(() =>
      container.evaluate(
        (element) => element.scrollHeight - element.clientHeight - element.scrollTop,
      ),
    )
    .toBeLessThan(5);
  await container.hover();
  await page.mouse.wheel(0, -500);
  await expect(page.getByTestId("scroll-state")).toHaveText("false");
  const previousTop = await container.evaluate((element) => element.scrollTop);
  await page.getByRole("button", { name: "Append output" }).click();
  await expect(container.getByText("Streaming paragraph", { exact: true })).toHaveCount(30);
  expect(await container.evaluate((element) => element.scrollTop)).toBeCloseTo(previousTop, 0);
  await page.getByRole("button", { name: "Return to bottom" }).click();
  await expect(page.getByTestId("scroll-state")).toHaveText("true");
  await page.getByRole("button", { name: "Append output" }).click();
  await expect(container.getByText("Streaming paragraph", { exact: true })).toHaveCount(60);
  await expect
    .poll(() =>
      container.evaluate(
        (element) => element.scrollHeight - element.clientHeight - element.scrollTop,
      ),
    )
    .toBeLessThan(5);
});

test.describe("mobile selection", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });
  test("select sheets keep values and distinguish both Vertex providers", async ({ page }) => {
    await openFixture(page, "mobile-selection");
    await page.getByRole("button", { name: "Mobile choice", exact: true }).tap();
    const choice = page.getByRole("dialog", { name: "Mobile choice" });
    await expect(choice).toBeVisible();
    await choice.getByText("Beta", { exact: true }).click();
    await expect(choice).toBeHidden();
    await expect(page.getByTestId("selection-state")).toContainText('"choice":"beta"');
    await page.getByRole("textbox", { name: "Choose provider" }).tap();
    const providers = page.getByRole("dialog");
    await expect(
      providers.getByText(zhTranslations.connections.vertexGeminiBadge, { exact: true }),
    ).toBeVisible();
    await expect(
      providers.getByText(zhTranslations.connections.vertexAnthropicBadge, { exact: true }),
    ).toBeVisible();
    await providers
      .getByText(zhTranslations.connections.vertexAnthropicBadge, { exact: true })
      .tap();
    await expect(providers).toBeHidden();
    await expect(page.getByTestId("selection-state")).toContainText(
      '"provider":"google-vertex-anthropic"',
    );
  });
});
