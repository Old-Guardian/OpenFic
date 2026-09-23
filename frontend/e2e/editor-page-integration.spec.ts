import { expect, test, type APIRequestContext, type Page, type Route } from "@playwright/test";

import tokenCases from "../../tests/fixtures/character-token-count-cases.json" with { type: "json" };
import savedFixture from "./fixtures/character-save-baseline.json" with { type: "json" };

const backendOrigin = process.env.T4_BACKEND_URL ?? "";
const appOrigin = process.env.T4_APP_URL ?? "";
const editedCase = tokenCases.cases.find((item) => item.id === "mixed")!;
const savedCase = tokenCases.cases.find((item) => item.id === "nested_markdown_list")!;

test.beforeEach(() => {
  test.skip(
    !backendOrigin || !appOrigin,
    "Set T4_BACKEND_URL and T4_APP_URL to isolated test servers",
  );
});

async function api(request: APIRequestContext, path: string) {
  return request.get(`${backendOrigin}/api/v1${path}`);
}

async function createProject(request: APIRequestContext): Promise<string> {
  const response = await request.post(`${backendOrigin}/api/v1/projects`, {
    form: { title: `T4 editor regression ${Date.now()}` },
  });
  expect(response.status()).toBe(201);
  return (await response.json()).id as string;
}

async function setAutoSave(request: APIRequestContext, enabled: boolean): Promise<void> {
  const response = await request.patch(`${backendOrigin}/api/v1/settings`, {
    data: { editor_auto_save: enabled },
  });
  expect(response.ok()).toBeTruthy();
}

type EditorKind = "character" | "world-info" | "chapter" | "note";

async function createEditorFixture(request: APIRequestContext, kind: EditorKind) {
  const projectId = await createProject(request);
  let firstId: string;
  let secondId: string;
  let url: string;
  let itemSelector: (id: string) => string;
  let editorSelector: string;
  let apiPath: (id: string) => string;

  if (kind === "character") {
    const create = async (name: string, description: string) => {
      const response = await request.post(
        `${backendOrigin}/api/v1/projects/${projectId}/characters`,
        {
          form: { name, description },
        },
      );
      expect(response.status()).toBe(201);
      return (await response.json()).id as string;
    };
    firstId = await create("T4角色A", "初始内容 A");
    secondId = await create("T4角色B", "初始内容 B");
    url = `/characters?projectId=${projectId}`;
    itemSelector = (id) =>
      `.characters-list-item:has-text("${id === firstId ? "T4角色A" : "T4角色B"}")`;
    editorSelector = ".characters-editor-shell";
    apiPath = (id) => `/characters/${id}`;
  } else if (kind === "world-info") {
    const worldResponse = await api(request, `/projects/${projectId}/world-info`);
    expect(worldResponse.ok()).toBeTruthy();
    const world = await worldResponse.json();
    const create = async (name: string, content: string) => {
      const response = await request.post(
        `${backendOrigin}/api/v1/world-info/${world.id}/entries`,
        {
          data: { name, content },
        },
      );
      expect(response.status()).toBe(201);
      return (await response.json()).id as string;
    };
    firstId = await create("T4条目A", "初始内容 A");
    secondId = await create("T4条目B", "初始内容 B");
    url = `/world-info?projectId=${projectId}`;
    itemSelector = (id) => `[data-entry-id="${id}"]`;
    editorSelector = ".world-info-page-editor-shell";
    apiPath = (id) => `/world-info-entries/${id}`;
  } else {
    if (kind === "chapter") {
      const volumeResponse = await request.post(
        `${backendOrigin}/api/v1/projects/${projectId}/volumes`,
        {
          data: { title: "T4卷" },
        },
      );
      expect(volumeResponse.status()).toBe(201);
      const volume = await volumeResponse.json();
      const create = async (title: string, content: string) => {
        const response = await request.post(
          `${backendOrigin}/api/v1/projects/${projectId}/chapters`,
          {
            data: { volume_id: volume.id, title, content },
          },
        );
        expect(response.status()).toBe(201);
        return (await response.json()).id as string;
      };
      firstId = await create("T4章节A", "初始内容 A");
      secondId = await create("T4章节B", "初始内容 B");
      itemSelector = (id) =>
        `.chapter-list-item-row:has-text("${id === firstId ? "T4章节A" : "T4章节B"}")`;
      apiPath = (id) => `/chapters/${id}`;
    } else {
      const create = async (title: string, content: string) => {
        const response = await request.post(`${backendOrigin}/api/v1/projects/${projectId}/notes`, {
          data: { title, content },
        });
        expect(response.status()).toBe(201);
        return (await response.json()).id as string;
      };
      firstId = await create("T4笔记A", "初始内容 A");
      secondId = await create("T4笔记B", "初始内容 B");
      itemSelector = (id) =>
        `.writing-page-sidebar div:has(> span:has-text("${id === firstId ? "T4笔记A" : "T4笔记B"}"))`;
      apiPath = (id) => `/notes/${id}`;
    }
    url = `/projects/${projectId}`;
    editorSelector = ".writing-page-editor-shell";
  }

  return { kind, projectId, firstId, secondId, url, itemSelector, editorSelector, apiPath };
}

async function openEditor(page: Page, fixture: Awaited<ReturnType<typeof createEditorFixture>>) {
  await page.goto(`${appOrigin}${fixture.url}`);
  if (fixture.kind === "note") {
    await page
      .locator(".writing-sidebar-segmented-control")
      .getByRole("radio", { name: "笔记" })
      .click();
  }
  const firstItem = page.locator(fixture.itemSelector(fixture.firstId));
  const secondItem = page.locator(fixture.itemSelector(fixture.secondId));
  await expect(firstItem.first()).toBeVisible();
  await firstItem.first().click();
  const editor = page.locator(fixture.editorSelector);
  await expect(editor.locator(".ProseMirror")).toContainText("初始内容 A");
  return { editor, firstItem: firstItem.first(), secondItem: secondItem.first() };
}

async function readWritingDraft(
  page: Page,
  kind: "chapter" | "note",
  id: string,
): Promise<string | null> {
  return page.evaluate(
    ({ kind, id }) =>
      new Promise<string | null>((resolve, reject) => {
        const openRequest = indexedDB.open("OpenFicDB");
        openRequest.onerror = () => reject(openRequest.error);
        openRequest.onsuccess = () => {
          const database = openRequest.result;
          const transaction = database.transaction("writingWorkingCopies", "readonly");
          const request = transaction.objectStore("writingWorkingCopies").get(`${kind}:${id}`);
          request.onerror = () => reject(request.error);
          request.onsuccess = () =>
            resolve((request.result?.content as string | undefined) ?? null);
          transaction.oncomplete = () => database.close();
        };
      }),
    { kind, id },
  );
}

test("T4: character page keeps saved Markdown and token count through navigation and reload", async ({
  page,
  request,
}) => {
  await setAutoSave(request, false);
  const projectId = await createProject(request);

  try {
    const firstResponse = await request.post(
      `${backendOrigin}/api/v1/projects/${projectId}/characters`,
      { form: { name: savedFixture.name, description: savedFixture.description } },
    );
    expect(firstResponse.status()).toBe(201);
    const first = await firstResponse.json();
    const secondResponse = await request.post(
      `${backendOrigin}/api/v1/projects/${projectId}/characters`,
      { form: { name: "顾明", description: "另一名角色" } },
    );
    expect(secondResponse.status()).toBe(201);

    await page.goto(`${appOrigin}/characters?projectId=${projectId}`);
    const firstItem = page.locator(".characters-list-item").filter({ hasText: savedFixture.name });
    const secondItem = page.locator(".characters-list-item").filter({ hasText: "顾明" });
    const editor = page.locator(".characters-editor-shell");

    await expect(firstItem).toBeVisible();
    await expect(firstItem).toContainText(String(savedCase.expected));
    await firstItem.click();
    await expect(editor.locator(".ProseMirror li")).toHaveCount(3);
    await expect(editor.getByText(new RegExp(`^${savedCase.expected}\\s`))).toBeVisible();

    await editor.locator(".ProseMirror").fill(editedCase.content);
    await expect(editor.getByText(new RegExp(`^${editedCase.expected}\\s`))).toBeVisible();
    await expect(firstItem).toContainText(String(savedCase.expected));

    const savedResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/v1/characters/${first.id}`) &&
        response.request().method() === "PATCH",
    );
    await editor.getByRole("button", { name: "保存", exact: true }).click();
    expect((await savedResponse).status()).toBe(200);
    await expect(firstItem).toContainText(String(editedCase.expected));
    await expect(editor.getByText(new RegExp(`^${editedCase.expected}\\s`))).toBeVisible();

    const savedCharacter = await api(request, `/characters/${first.id}`);
    expect((await savedCharacter.json()).description).toBe(editedCase.content);
    const savedList = await api(request, `/projects/${projectId}/characters`);
    expect(
      (await savedList.json()).items.find((item: { id: string }) => item.id === first.id)
        .token_count,
    ).toBe(editedCase.expected);

    await secondItem.click();
    await expect(editor.locator(".ProseMirror")).toContainText("另一名角色");
    await firstItem.click();
    await expect(editor.locator(".ProseMirror")).toContainText(editedCase.content);
    await page.reload();
    await expect(firstItem).toContainText(String(editedCase.expected));
    await firstItem.click();
    await expect(editor.locator(".ProseMirror")).toContainText(editedCase.content);
    await expect(editor.getByText(new RegExp(`^${editedCase.expected}\\s`))).toBeVisible();
  } finally {
    await request.delete(`${backendOrigin}/api/v1/projects/${projectId}`);
  }
});

test("T4: world info uses the shared Markdown editor and saves its actual content", async ({
  page,
  request,
}) => {
  await setAutoSave(request, false);
  const projectId = await createProject(request);

  try {
    const worldInfoResponse = await api(request, `/projects/${projectId}/world-info`);
    expect(worldInfoResponse.ok()).toBeTruthy();
    const worldInfo = await worldInfoResponse.json();
    const entryResponse = await request.post(
      `${backendOrigin}/api/v1/world-info/${worldInfo.id}/entries`,
      {
        data: {
          name: "林青条目",
          content: savedFixture.description,
          token_count: savedCase.expected,
        },
      },
    );
    expect(entryResponse.status()).toBe(201);
    const entry = await entryResponse.json();

    await page.goto(`${appOrigin}/world-info?projectId=${projectId}`);
    await page.locator(`[data-entry-id="${entry.id}"]`).click();
    const editor = page.locator(".world-info-page-editor-shell");
    await expect(editor.locator(".ProseMirror li")).toHaveCount(3);
    await editor.locator(".ProseMirror").fill(editedCase.content);
    const savedResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/v1/world-info-entries/${entry.id}`) &&
        response.request().method() === "PATCH",
    );
    await editor.getByRole("button", { name: "保存", exact: true }).click();
    expect((await savedResponse).status()).toBe(200);
    const detail = await api(request, `/world-info-entries/${entry.id}`);
    expect((await detail.json()).content).toBe(editedCase.content);
  } finally {
    await request.delete(`${backendOrigin}/api/v1/projects/${projectId}`);
  }
});

test("T4: chapter and note editors save through the shared hook", async ({ page, request }) => {
  await setAutoSave(request, false);
  const projectId = await createProject(request);

  try {
    const volumeResponse = await request.post(
      `${backendOrigin}/api/v1/projects/${projectId}/volumes`,
      {
        data: { title: "第一卷" },
      },
    );
    expect(volumeResponse.status()).toBe(201);
    const volume = await volumeResponse.json();
    const chapterResponse = await request.post(
      `${backendOrigin}/api/v1/projects/${projectId}/chapters`,
      {
        data: { volume_id: volume.id, title: "第一章", content: "章节原文" },
      },
    );
    expect(chapterResponse.status()).toBe(201);
    const chapter = await chapterResponse.json();
    const noteResponse = await request.post(`${backendOrigin}/api/v1/projects/${projectId}/notes`, {
      data: { title: "线索笔记", content: "笔记原文" },
    });
    expect(noteResponse.status()).toBe(201);
    const note = await noteResponse.json();

    await page.goto(`${appOrigin}/projects/${projectId}`);
    await page.locator(".chapter-list-item-row").filter({ hasText: "第一章" }).click();
    const editor = page.locator(".writing-page-editor-shell");
    await expect(editor.locator(".ProseMirror")).toContainText("章节原文");
    await editor.locator(".ProseMirror").fill("章节新内容");
    const chapterSaved = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/v1/chapters/${chapter.id}`) &&
        response.request().method() === "PATCH",
    );
    await editor.getByRole("button", { name: "保存", exact: true }).click();
    expect((await chapterSaved).status()).toBe(200);
    expect((await (await api(request, `/chapters/${chapter.id}`)).json()).content).toContain(
      "章节新内容",
    );

    await page
      .locator(".writing-sidebar-segmented-control")
      .getByRole("radio", { name: "笔记" })
      .click();
    await page.locator(".writing-page-sidebar").getByText("线索笔记", { exact: true }).click();
    await expect(editor.locator(".ProseMirror")).toContainText("笔记原文");
    await editor.locator(".ProseMirror").fill("笔记新内容");
    const noteSaved = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/v1/notes/${note.id}`) &&
        response.request().method() === "PATCH",
    );
    await editor.getByRole("button", { name: "保存", exact: true }).click();
    expect((await noteSaved).status()).toBe(200);
    expect((await (await api(request, `/notes/${note.id}`)).json()).content).toBe("笔记新内容");
  } finally {
    await request.delete(`${backendOrigin}/api/v1/projects/${projectId}`);
  }
});

for (const kind of ["character", "world-info", "chapter", "note"] as const) {
  test(`T4: ${kind} honors manual save, failed retry, leave choices and auto save`, async ({
    page,
    request,
  }) => {
    await setAutoSave(request, false);
    const fixture = await createEditorFixture(request, kind);
    const patchSuffix = `/api/v1${fixture.apiPath(fixture.firstId)}`;
    const readSavedContent = async () => {
      const response = await api(request, fixture.apiPath(fixture.firstId));
      expect(response.ok()).toBeTruthy();
      const data = await response.json();
      return kind === "character" ? (data.description as string) : (data.content as string);
    };

    try {
      const { editor, firstItem, secondItem } = await openEditor(page, fixture);
      const body = editor.locator(".ProseMirror");
      let patchCount = 0;
      page.on("request", (request) => {
        if (request.url().endsWith(patchSuffix) && request.method() === "PATCH") patchCount += 1;
      });

      await body.fill("手动保存一");
      await page.waitForTimeout(kind === "chapter" || kind === "note" ? 3200 : 1700);
      expect(patchCount).toBe(0);
      const manualResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith(patchSuffix) && response.request().method() === "PATCH",
      );
      await editor.getByRole("button", { name: "保存", exact: true }).click();
      expect((await manualResponse).status()).toBe(200);
      await expect.poll(readSavedContent).toContain("手动保存一");

      await body.fill("失败后重试");
      const failSave = async (route: Route) => {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: '{"detail":"controlled failure"}',
        });
      };
      await page.route(`**${patchSuffix}`, failSave);
      const failedResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith(patchSuffix) && response.request().method() === "PATCH",
      );
      await editor.getByRole("button", { name: "保存", exact: true }).click();
      expect((await failedResponse).status()).toBe(503);
      expect(await readSavedContent()).toContain("手动保存一");
      await page.unroute(`**${patchSuffix}`, failSave);
      const retryResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith(patchSuffix) && response.request().method() === "PATCH",
      );
      await editor.getByRole("button", { name: "保存", exact: true }).click();
      expect((await retryResponse).status()).toBe(200);
      await expect.poll(readSavedContent).toContain("失败后重试");

      await body.fill("离开取消");
      await expect(editor.getByRole("button", { name: "保存", exact: true })).toBeEnabled();
      await secondItem.click();
      const leaveDialog = page.getByRole("dialog", { name: "未保存的修改" });
      await expect(leaveDialog).toBeVisible();
      await leaveDialog.getByRole("button", { name: "取消" }).click();
      await expect(body).toContainText("离开取消");

      await secondItem.click();
      await leaveDialog.getByRole("button", { name: "放弃修改" }).click();
      await expect(body).toContainText("初始内容 B");
      expect(await readSavedContent()).toContain("失败后重试");

      await firstItem.click();
      await expect(body).toContainText("失败后重试");
      await body.fill("离开保存");
      await expect(editor.getByRole("button", { name: "保存", exact: true })).toBeEnabled();
      await secondItem.click();
      await leaveDialog.getByRole("button", { name: "保存并离开" }).click();
      await expect(body).toContainText("初始内容 B");
      await expect.poll(readSavedContent).toContain("离开保存");

      await setAutoSave(request, true);
      await page.reload();
      if (kind === "note") {
        await page
          .locator(".writing-sidebar-segmented-control")
          .getByRole("radio", { name: "笔记" })
          .click();
      }
      await page.locator(fixture.itemSelector(fixture.firstId)).first().click();
      const autoBody = page.locator(fixture.editorSelector).locator(".ProseMirror");
      await expect(autoBody).toContainText("离开保存");
      await autoBody.fill("自动保存完成");
      await expect.poll(readSavedContent, { timeout: 15000 }).toContain("自动保存完成");

      if (kind === "chapter" || kind === "note") {
        await setAutoSave(request, false);
        await page.reload();
        if (kind === "note") {
          await page
            .locator(".writing-sidebar-segmented-control")
            .getByRole("radio", { name: "笔记" })
            .click();
        }
        await page.locator(fixture.itemSelector(fixture.firstId)).first().click();
        const draftBody = page.locator(fixture.editorSelector).locator(".ProseMirror");
        await expect(draftBody).toContainText("自动保存完成");
        await draftBody.fill("本地草稿恢复");
        await expect
          .poll(() => readWritingDraft(page, kind, fixture.firstId))
          .toContain("本地草稿恢复");
        expect(await readSavedContent()).toContain("自动保存完成");

        page.on("dialog", (dialog) => void dialog.accept());
        await page.reload();
        if (kind === "note") {
          await page
            .locator(".writing-sidebar-segmented-control")
            .getByRole("radio", { name: "笔记" })
            .click();
        }
        await page.locator(fixture.itemSelector(fixture.firstId)).first().click();
        await expect(page.locator(fixture.editorSelector).locator(".ProseMirror")).toContainText(
          "本地草稿恢复",
        );
        expect(await readSavedContent()).toContain("自动保存完成");
      }
    } finally {
      await request.delete(`${backendOrigin}/api/v1/projects/${fixture.projectId}`);
    }
  });
}
