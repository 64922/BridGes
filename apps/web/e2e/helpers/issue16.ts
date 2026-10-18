import path from "node:path";

import { expect, type Page } from "@playwright/test";

export const VIEWPORTS = [
  { width: 1280, height: 720 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
] as const;

export const PASSWORD = "correct-horse-issue16";
export const NOW = "2026-10-18T00:00:00Z";

export function composer(page: Page) {
  return page.getByTestId("composer");
}

export function messageInput(page: Page) {
  return composer(page).getByLabel("输入消息");
}

export function thread(page: Page) {
  return page.getByTestId("chat-thread");
}

function apiUrl(page: Page, route: string): string {
  return new URL(route, page.url()).toString();
}

const API_ORIGIN = `http://127.0.0.1:${process.env.API_PORT || "8000"}`;

export async function configureScript(
  page: Page,
  script: { match: string; chunks: string[]; delay_ms?: number }
) {
  const response = await page.request.post(`${API_ORIGIN}/_test/chat-stream-script`, {
    data: { chunks: script.chunks, delay_ms: script.delay_ms ?? 0, match: script.match },
  });
  expect(response.ok(), `脚本注册失败：HTTP ${response.status()}`).toBeTruthy();
}

export async function getConversation(page: Page, conversationId: string) {
  const response = await page.request.get(
    apiUrl(page, `/api/chat/conversations/${conversationId}`)
  );
  expect(response.ok(), `读取会话失败：HTTP ${response.status()}`).toBeTruthy();
  return (await response.json()) as {
    messages: Array<{ role: string; content: string; status: string }>;
  };
}

export async function sendFromHome(page: Page, text: string): Promise<string> {
  await expect(page.getByTestId("new-chat-home")).toBeVisible();
  await messageInput(page).fill(text);
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page).toHaveURL(/\/chat\/[A-Za-z0-9_-]+/, { timeout: 60_000 });
  await expect(thread(page)).toBeVisible();
  const match = page.url().match(/\/chat\/([A-Za-z0-9_-]+)/);
  expect(match, `未从 URL 取得会话标识：${page.url()}`).not.toBeNull();
  return match![1];
}

export async function backToNewChat(page: Page) {
  await page
    .getByTestId("app-sidebar")
    .getByRole("link", { name: "新聊天", exact: true })
    .click();
  await expect(page.getByTestId("new-chat-home")).toBeVisible();
}

export async function chooseStudyMode(page: Page) {
  const toggle = page.getByTestId("mode-toggle");
  const studyButton = toggle.getByRole("button", { name: "学习模式" });
  if ((await studyButton.count()) > 0) {
    await studyButton.click();
  }
  await expect(toggle.getByRole("button", { name: "学习模式" })).toHaveAttribute(
    "aria-pressed",
    "true"
  );
}

export async function sendInConversation(page: Page, text: string) {
  await expect(page.getByRole("button", { name: "停止生成" })).toHaveCount(0, {
    timeout: 60_000,
  });
  await messageInput(page).fill(text);
  const send = page.getByRole("button", { name: "发送消息" });
  await expect(send).toBeEnabled({ timeout: 60_000 });
  await send.click();
}

export async function attachFile(
  page: Page,
  fileName: string,
  mimeType: string,
  content: Buffer
) {
  await page.getByTestId("composer-file-input").evaluate(
    (el, payload) => {
      const input = el as HTMLInputElement;
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(
        new File([Uint8Array.from(atob(payload.base64), (c) => c.charCodeAt(0))], payload.name, {
          type: payload.mimeType,
        })
      );
      Object.defineProperty(input, "files", { configurable: true, value: dataTransfer.files });
      input.dispatchEvent(new Event("change", { bubbles: true }));
    },
    { name: fileName, mimeType, base64: content.toString("base64") }
  );
  await expect(page.getByTestId("composer-attachments")).toContainText(fileName);
}

export async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => {
    const root = document.documentElement;
    return {
      clientWidth: root.clientWidth,
      scrollWidth: root.scrollWidth,
    };
  });
  expect(
    overflow.scrollWidth,
    `横向溢出：scrollWidth ${overflow.scrollWidth} > clientWidth ${overflow.clientWidth}`
  ).toBeLessThanOrEqual(overflow.clientWidth + 1);
}

export async function waitForSidebar(page: Page) {
  await expect(
    page
      .getByTestId("recent-conversations")
      .getByRole("status", { name: "正在加载最近对话" })
  ).toHaveCount(0, { timeout: 30_000 });
}

export async function expectComposerInsideViewport(page: Page) {
  const box = await composer(page).boundingBox();
  const viewport = page.viewportSize()!;
  expect(box, "输入区不可见").not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
  expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height + 1);
}

export async function screenshot(page: Page, name: string) {
  await page.screenshot({ path: evidencePath(name) });
}

function evidencePath(name: string) {
  return path.resolve(
    __dirname,
    "../../../../.scratch/chat-presentation-redesign/evidence/16",
    `${name}.png`
  );
}
