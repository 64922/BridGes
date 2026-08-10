import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const CONVERSATION_ID = "conv-issue20";
const NOW = "2026-08-10T00:00:00Z";

function message(
  id: string,
  role: "user" | "assistant",
  content: string,
  retrieval?: unknown
): Record<string, unknown> {
  return {
    message_id: id,
    conversation_id: CONVERSATION_ID,
    role,
    attempt_number: 1,
    status: "done",
    content,
    attachments: [],
    retrieval: retrieval ?? null,
    error_code: null,
    error_message: null,
    duration_ms: role === "assistant" ? 90 : null,
    model_id: role === "assistant" ? "mock-model" : null,
    run_lock_id: null,
    created_at: NOW,
    updated_at: NOW,
  };
}

async function openConversation(page: Page, messages: unknown[]): Promise<void> {
  const credentials = uniqueCredentials("issue20");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
  await page.route(
    "**/api/chat/conversations/" + CONVERSATION_ID,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          conversation_id: CONVERSATION_ID,
          title: "引用契约",
          mode: "study",
          created_at: NOW,
          updated_at: NOW,
          messages,
          mode_events: [],
        }),
      });
    }
  );
  await page.goto("/chat/" + CONVERSATION_ID);
  await expect(page.getByTestId("composer")).toBeVisible();
}

test.describe("Issue 20 — 紧凑引用与详情错误恢复", () => {
  test("只展示文件名和定位信息，详情可用键盘展开", async ({ page }) => {
    const retrieval = {
      citations: [
        {
          citation_id: "cit-1",
          filename: "课程笔记.md",
          page_number: 3,
          section_title: "熵增原理",
          snippet: "熵在孤立系统中不会减少。",
          rank: 1,
        },
      ],
    };
    await openConversation(page, [
      message("u-1", "user", "解释熵增"),
      message("a-1", "assistant", "熵是系统无序程度的度量。", retrieval),
    ]);

    const card = page.getByTestId("retrieval-citations-card");
    await expect(card).toContainText("课程笔记.md");
    await expect(card).toContainText("第 3 页");
    await expect(card).toContainText("熵增原理");
    await expect(page.getByText("本地检索")).toHaveCount(0);
    await expect(page.getByText("检索充分")).toHaveCount(0);

    await card.getByTestId("citation-item-1").press("Enter");
    await expect(card.getByTestId("citation-detail-1")).toContainText("熵在孤立系统中不会减少");
  });

  test("详情失败显示中文局部错误并支持重试", async ({ page }) => {
    let detailAttempts = 0;
    await page.route(
      "**/api/chat/conversations/" + CONVERSATION_ID + "/messages/*/citations/*",
      async (route) => {
        detailAttempts += 1;
        if (detailAttempts === 1) {
          await route.fulfill({
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({ detail: "temporary upstream failure" }),
          });
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            citation: {
              citation_id: "cit-1",
              filename: "课程笔记.md",
              page_number: 3,
              section_title: "熵增原理",
              snippet: "详情恢复成功。",
            },
            access_status: "accessible",
            access_message: null,
          }),
        });
      }
    );
    await openConversation(page, [
      message("u-1", "user", "解释熵增"),
      message("a-1", "assistant", "熵是系统无序程度的度量。", {
        citations: [
          {
            citation_id: "cit-1",
            filename: "课程笔记.md",
            page_number: 3,
            section_title: "熵增原理",
            rank: 1,
          },
        ],
      }),
    ]);

    const item = page.getByTestId("citation-item-1");
    await item.press("Enter");
    await expect(page.getByTestId("citation-detail-error-1")).toContainText("引用详情加载失败");
    await page.getByTestId("citation-detail-retry-1").click();
    await expect(page.getByTestId("citation-detail-1")).toContainText("详情恢复成功");
  });

  test("无引用时不渲染旧的本地检索过程卡", async ({ page }) => {
    await openConversation(page, [
      message("u-1", "user", "你好"),
      message("a-1", "assistant", "你好，我在。"),
    ]);
    await expect(page.getByTestId("retrieval-citations-card")).toHaveCount(0);
    await expect(page.getByText("本地检索")).toHaveCount(0);
  });
});
