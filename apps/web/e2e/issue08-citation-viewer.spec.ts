import { expect, test } from "@playwright/test";

import {
  ALL_CITATIONS,
  CITATION_A,
  CITATION_B,
  CITATION_RETRY,
  defaultDetail,
  detailBody,
  message,
  openConversation,
  OTHER_CONVERSATION_ID,
  type MockMessage,
} from "./helpers/issue08-chat-api";

/**
 * 对话改版 Issue 08：真实引用在默认关闭的右侧面板中读取。
 *
 * 边界与生命周期用可观测的协议替身验证：引用身份固定到具体消息、快速切换
 * 只呈现当前选择、失败可重试、删除/授权变化不保留片段、关闭归还焦点、
 * 切换会话释放旧面板，以及目标桌面尺寸与缩放下正文/面板/输入框互不遮挡。
 * 真实服务端的授权与删除判定由 retrieval pytest 覆盖，本 spec 不重复伪造。
 */

test.describe("Issue 08 — 真实引用与右侧资料面板", () => {
  test("默认关闭；打开后可并排阅读，继续追问不关闭面板", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const { sendBodies } = await openConversation(page, { enableSend: true });

    await expect(page.getByTestId("citation-viewer")).toHaveCount(0);
    await expect(page.getByTestId("retrieval-citations-card")).toBeVisible();

    // 新回答携带新引用也不会自动打开面板。
    await page.locator("#composer-input").fill("继续追问");
    await page.locator("#composer-input").press("Enter");
    await expect(page.getByText("后续回答已收到。")).toBeVisible();
    await expect(page.getByTestId("citation-viewer")).toHaveCount(0);
    expect(sendBodies).toHaveLength(1);

    await page.locator("#msg-a-1").getByTestId("citation-item-1").click();
    const viewer = page.getByTestId("citation-viewer");
    await expect(viewer).toBeVisible();
    await expect(viewer).toContainText("课程笔记.md");
    await expect(page.getByTestId("citation-viewer-location")).toContainText("第 1 页");
    await expect(page.getByTestId("citation-viewer-location")).toContainText("熵增原理");
    await expect(page.getByTestId("citation-viewer-snippet")).toContainText(
      "熵在孤立系统中不会减少。"
    );
    await expect(page.getByTestId("citation-viewer-status")).toContainText("原文可访问");

    // 面板打开时继续追问：对话更新，资料仍可读。
    await page.locator("#composer-input").fill("再追问一次");
    await page.locator("#composer-input").press("Enter");
    await expect(page.getByText("后续回答已收到。")).toHaveCount(2);
    await expect(page.getByTestId("citation-viewer-snippet")).toContainText(
      "熵在孤立系统中不会减少。"
    );
    await expect(page.getByTestId("composer")).toBeVisible();
  });

  test("快速切换引用只呈现当前选择，迟到结果不覆盖新资料", async ({ page }) => {
    await openConversation(page, {
      detailHandler: async (citationId) => {
        if (citationId === CITATION_A.citation_id) {
          await new Promise((resolve) => setTimeout(resolve, 400));
          return {
            status: 200,
            body: detailBody(CITATION_A, "accessible", "原文可访问。"),
          };
        }
        return {
          status: 200,
          body: detailBody(CITATION_B, "accessible", "原文可访问。"),
        };
      },
    });

    await page.getByTestId("citation-item-1").click();
    await page.getByTestId("citation-item-2").click();
    await expect(page.getByTestId("citation-viewer-snippet")).toContainText(
      "可逆过程是理想化的极限。"
    );
    await page.waitForTimeout(600);
    await expect(page.getByTestId("citation-viewer-snippet")).toContainText(
      "可逆过程是理想化的极限。"
    );
    await expect(page.getByTestId("citation-viewer")).not.toContainText(
      "熵在孤立系统中不会减少。"
    );
  });

  test("读取失败可重试；删除、授权变化与无资料分别呈现且不保留片段", async ({ page }) => {
    let retryAttempts = 0;
    await openConversation(page, {
      detailHandler: async (citationId) => {
        if (citationId === CITATION_RETRY.citation_id) {
          retryAttempts += 1;
          // React StrictMode 在开发服务器上会挂载 effect 两次：前两次请求
          // 都返回失败，用户点击重试后的第三次才成功（生产构建只请求一次）。
          if (retryAttempts <= 2) {
            return { status: 503, body: { detail: { message: "temporary" } } };
          }
          return {
            status: 200,
            body: detailBody(CITATION_RETRY, "accessible", "原文可访问。"),
          };
        }
        return defaultDetail(citationId);
      },
    });

    await page.getByTestId("citation-item-3").click();
    await expect(page.getByTestId("citation-viewer-error")).toContainText("资料读取失败");
    await page.getByTestId("citation-viewer-retry").click();
    await expect(page.getByTestId("citation-viewer-snippet")).toContainText(
      "重试后读取到的片段。"
    );

    await page.getByTestId("citation-item-4").click();
    await expect(page.getByTestId("citation-viewer-status")).toContainText("原文已删除");
    await expect(page.getByTestId("citation-viewer-unavailable")).toContainText(
      "原文已删除，无法打开。"
    );
    await expect(page.getByTestId("citation-viewer-snippet")).toHaveCount(0);
    await expect(page.getByTestId("citation-open-4")).toHaveCount(0);

    await page.getByTestId("citation-item-5").click();
    await expect(page.getByTestId("citation-viewer-status")).toContainText("授权已变化");
    await expect(page.getByTestId("citation-viewer-unavailable")).toContainText(
      "附件已从消息中移除，授权已变化"
    );
    await expect(page.getByTestId("citation-viewer-snippet")).toHaveCount(0);
    await expect(page.getByTestId("citation-viewer")).not.toContainText(
      "这段敏感片段不应显示。"
    );

    await page.getByTestId("citation-item-6").click();
    await expect(page.getByTestId("citation-viewer-missing")).toContainText(
      "没有可显示的资料"
    );
  });

  test("Esc 与关闭按钮归还焦点且不改变对话滚动；切换会话清除旧面板", async ({ page }) => {
    const longMessages: MockMessage[] = [];
    for (let index = 0; index < 12; index += 1) {
      longMessages.push(message(`u-hist-${index}`, "user", `历史问题 ${index}`));
      longMessages.push(message(`a-hist-${index}`, "assistant", `历史回答 ${index}`));
    }
    longMessages.push(message("u-1", "user", "解释熵增"));
    longMessages.push(message("a-1", "assistant", "熵是系统无序程度的度量。", ALL_CITATIONS));
    await openConversation(page, { messages: longMessages });

    const trigger = page.getByTestId("citation-item-1");
    await trigger.click();
    await expect(page.getByTestId("citation-viewer")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("citation-viewer")).toHaveCount(0);
    await expect(trigger).toBeFocused();

    await trigger.click();
    const thread = page.getByTestId("chat-thread");
    const scrollTarget = await thread.evaluate((element) => {
      element.style.scrollBehavior = "auto";
      const target = Math.min(120, Math.max(0, element.scrollHeight - element.clientHeight - 24));
      element.scrollTop = target;
      return element.scrollTop;
    });
    expect(scrollTarget).toBeGreaterThan(0);
    await page.getByTestId("citation-viewer-close").click();
    await expect(page.getByTestId("citation-viewer")).toHaveCount(0);
    await expect(trigger).toBeFocused();
    expect(await thread.evaluate((element) => element.scrollTop)).toBe(scrollTarget);

    await trigger.click();
    await expect(page.getByTestId("citation-viewer")).toBeVisible();
    await page.getByRole("link", { name: /另一个会话/ }).click();
    await page.waitForURL(`**/chat/${OTHER_CONVERSATION_ID}`);
    await expect(page.getByTestId("citation-viewer")).toHaveCount(0);
  });

  test("三种目标桌面尺寸及 125% 缩放下正文、面板与输入框互不遮挡", async ({
    page,
  }, testInfo) => {
    await openConversation(page);
    await page.getByTestId("citation-item-1").click();
    await expect(page.getByTestId("citation-viewer")).toBeVisible();

    for (const viewport of [
      { width: 1280, height: 720, label: "1280x720" },
      { width: 1440, height: 900, label: "1440x900" },
      { width: 1920, height: 1080, label: "1920x1080" },
      // 1280x720 在 125% 浏览器缩放下的等效 CSS 视口。
      { width: 1024, height: 576, label: "1280x720-zoom125" },
    ]) {
      await page.setViewportSize(viewport);
      const main = await page.getByTestId("main-content").boundingBox();
      const panel = await page.getByTestId("citation-viewer").boundingBox();
      const composer = await page.getByTestId("composer").boundingBox();
      expect(main).not.toBeNull();
      expect(panel).not.toBeNull();
      expect(composer).not.toBeNull();
      if (!main || !panel || !composer) continue;
      expect(panel.x).toBeGreaterThanOrEqual(main.x + main.width - 1);
      expect(composer.x + composer.width).toBeLessThanOrEqual(panel.x + 1);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1
        )
      ).toBe(true);
      await expect(page.getByTestId("citation-viewer-snippet")).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath(`panel-${viewport.label}.png`),
        fullPage: false,
      });
    }
  });
});
