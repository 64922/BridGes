import { expect, test } from "@playwright/test";

import {
  commuteRoute,
  message,
  paperSearch,
  tiebaResearch,
} from "../src/lib/chat-modules.test-support";

import { signUp, uniqueCredentials } from "./helpers/auth";
import {
  backToNewChat,
  expectComposerInsideViewport,
  expectNoHorizontalOverflow,
  messageInput,
  NOW,
  PASSWORD,
  screenshot,
  thread,
  VIEWPORTS,
  waitForSidebar,
} from "./helpers/issue16";

/**
 * Issue 16：跨模块组合的桌面布局验收。
 *
 * 本文件用受控 API 投影核对呈现层合同：核心正文默认可见、过程与详情默认
 * 关闭、右侧面板不遮断输入、三视口无水平溢出，以及面板关闭后的焦点归还。
 * 它不证明各模块真实上游可用；真实链路见 issue16-desktop-acceptance.spec.ts。
 */

function paperRecommendation() {
  return {
    abs_url: "https://arxiv.org/abs/1706.03762",
    arxiv_id: "1706.03762",
    authors: ["Ashish Vaswani"],
    candidate_id: "arxiv:1706.03762",
    full_text_available: false,
    full_text_url: null,
    match_basis: "标题与摘要命中 transformer/attention。",
    match_evidence: [
      { quote: "Attention Is All You Need", requirement: "Transformer", source: "title" },
    ],
    order: 1,
    pdf_url: null,
    primary_category: "cs.CL",
    published_year: 2017,
    read_scope: "abstract",
    reason_zh: "奠基工作，先读这一篇。",
    relevance: "direct",
    research_group: "Transformer 注意力",
    role: "foundation",
    source: "arxiv",
    source_abstract: "We propose the Transformer, a model relying entirely on attention.",
    sources: ["arxiv"],
    summary_evidence: {
      quote: "a model relying entirely on attention",
      requirement: "source_summary",
      source: "abstract",
    },
    summary_zh: "这篇论文提出完全依赖注意力机制的 Transformer。",
    title: "Attention Is All You Need",
    unverified: ["未通读全文"],
  };
}

function userMessage(conversationId: string, id: string, content: string) {
  return {
    attempt_number: 1,
    content,
    conversation_id: conversationId,
    created_at: NOW,
    message_id: id,
    role: "user",
    status: "done",
    updated_at: NOW,
  };
}

function visualMessages(conversationId: string) {
  const paper = message(
    "msg-visual-paper",
    paperSearch({
      context_label: "机器学习中的 Transformer 结构",
      final_query: "transformer attention",
      normalized_term: "Transformer",
      original_phrase: "Transformer",
      papers: [paperRecommendation()] as never,
      research_question: "Transformer 注意力机制如何支持序列建模",
      searched_at: NOW,
    })
  );
  paper.conversation_id = conversationId;
  paper.content = "先读《Attention Is All You Need》，理解自注意力，再看实现。";

  const tieba = message(
    "msg-visual-tieba",
    null,
    tiebaResearch({
      completed_at: NOW,
      original_question: "华东交通大学吧里的自习室讨论",
      summary: "吧友普遍提到图书馆自习室晚间更安静，但开放时间仍需以官方为准。",
      topic: "自习室",
    })
  );
  tieba.conversation_id = conversationId;
  tieba.content = "贴吧里近期的自习体验集中在图书馆与教学楼空教室。";

  const commute = message("msg-visual-commute", null);
  commute.conversation_id = conversationId;
  commute.content = "从南区宿舍步行到图书馆约 18 分钟，建议提前 25 分钟出门。";
  commute.commute_route = commuteRoute({
    base_duration_seconds: 1080,
    destination: {
      campus_verified: true,
      location: "115.858,28.682",
      match_basis: "校区内 POI",
      name: "图书馆",
      original_phrase: "图书馆",
      query: "华东交通大学图书馆",
      role: "destination",
      unverified: [],
    },
    distance_m: 1_350,
    origin: {
      campus_verified: true,
      location: "115.852,28.678",
      match_basis: "校区内 POI",
      name: "南区宿舍",
      original_phrase: "南区宿舍",
      query: "华东交通大学南区宿舍",
      role: "origin",
      unverified: [],
    },
    resolved_at: NOW,
    suggested_total_seconds: 1_500,
  });

  const image = message("msg-visual-image", null);
  image.conversation_id = conversationId;
  image.content = "图片任务失败，已保留生成要求，可直接重试。";
  (image as Record<string, unknown>).image = {
    model_id: "gpt-image-1",
    prompt: "雨后的校园图书馆，暖色灯光",
    status: "failed",
    task_id: "task-visual-image",
  };

  const historical = message("msg-visual-history", null);
  historical.conversation_id = conversationId;
  historical.content = "历史记录：上次讨论的结论是先读综述，再补实验。";
  historical.created_at = "2026-09-01T08:00:00Z";
  historical.updated_at = "2026-09-01T08:00:01Z";

  return [
    userMessage(conversationId, "msg-visual-user-paper", "帮我找 Transformer 的入门论文"),
    paper,
    userMessage(conversationId, "msg-visual-user-tieba", "贴吧里怎么讨论自习室"),
    tieba,
    userMessage(conversationId, "msg-visual-user-commute", "从南区宿舍到图书馆怎么走"),
    commute,
    userMessage(conversationId, "msg-visual-user-image", "画一张雨后的图书馆"),
    image,
    userMessage(conversationId, "msg-visual-user-history", "上次我们讨论到哪了"),
    historical,
  ];
}

test("受控投影：跨模块布局、详情默认关闭与三视口无溢出", async ({ page }) => {
  const account = uniqueCredentials("i16-visual");
  await signUp(page, account.username, account.qqEmail, PASSWORD);
  await backToNewChat(page);

  const conversationId = "conv-issue16-visual";
  const messages = visualMessages(conversationId);
  await page.route("**/api/chat/conversations/conv-issue16-visual", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      body: JSON.stringify({
        conversation_id: conversationId,
        created_at: NOW,
        messages,
        mode: "companion",
        title: "跨模块桌面验收",
        updated_at: NOW,
      }),
      contentType: "application/json",
      status: 200,
    });
  });

  await page.goto(`/chat/${conversationId}`);
  await expect(thread(page)).toBeVisible();
  await expect(thread(page)).toContainText("Attention Is All You Need");
  await expect(thread(page)).toContainText("图书馆自习室晚间更安静");
  await expect(thread(page)).toContainText("步行到图书馆约 18 分钟");
  await expect(thread(page)).toContainText("图片任务失败");
  await expect(thread(page)).toContainText("历史记录");
  await expect(page.getByTestId("public-process-details")).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await waitForSidebar(page);

  for (const viewport of VIEWPORTS) {
    await page.setViewportSize(viewport);
    await expectComposerInsideViewport(page);
    await expectNoHorizontalOverflow(page);
    const sidebarBox = await page.getByTestId("app-sidebar").boundingBox();
    const threadBox = await thread(page).boundingBox();
    expect(sidebarBox).not.toBeNull();
    expect(threadBox).not.toBeNull();
    expect(sidebarBox!.x + sidebarBox!.width).toBeLessThanOrEqual(threadBox!.x + 1);
    await screenshot(page, `projection-${viewport.width}x${viewport.height}`);
  }

  await page.setViewportSize({ width: 1440, height: 900 });
  const paperTrigger = page.getByTestId("paper-details-open").first();
  await expect(paperTrigger).toBeVisible();
  await paperTrigger.click();
  await expect(page.getByTestId("paper-details-panel")).toBeVisible();
  await expect(thread(page)).toContainText("Attention Is All You Need");
  await messageInput(page).focus();
  await expect(page.getByTestId("paper-details-panel")).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await screenshot(page, "projection-panel-open-1440x900");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("paper-details-panel")).toHaveCount(0);
  await expect(paperTrigger).toBeFocused();

  // 常见桌面 125% 缩放：等价于 CSS 视口按比例缩小，检查无横向溢出。
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize({
      height: Math.round(viewport.height / 1.25),
      width: Math.round(viewport.width / 1.25),
    });
    await expectComposerInsideViewport(page);
    await expectNoHorizontalOverflow(page);
    await screenshot(page, `projection-${viewport.width}x${viewport.height}-zoom125`);
  }
});
