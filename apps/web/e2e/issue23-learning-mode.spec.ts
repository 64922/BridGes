import { expect, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const NOW = "2026-08-04T00:00:00Z";
const CONVERSATION_ID = "conv-issue23";

function teachingProjection() {
  return {
    status: "ready",
    goal: "学习“量子纠缠”并理解其核心机制",
    level_assumption: "暂按初学者处理；你可以通过追问调整深度、例子和范围。",
    steps: ["确定本轮学习目标与范围", "从多个角度检索并组织一次性全面介绍", "标注来源并留下可针对性追问的入口"],
    check_method: "不强制测验；可以按需追问、要求换例子或继续深入。",
    evidence_gate: {
      status: "sufficient",
      reason: "本轮使用了当前对话授权的学习材料。",
      local_sources: [{ source_type: "attachment", source_id: "cit-1", title: "量子纠缠讲义", locator: "第 2 页", accessed_at: "2026-08-04T08:00:00Z" }],
      external_sources: [],
      required_search: "none",
      search_status: null,
      gap: null,
      recovery_steps: [],
      checked_at: NOW,
    },
    quiz: null,
    learning_progress: {
      schema_version: "learning-progress-v1",
      goal: "学习“量子纠缠”并理解其核心机制",
      covered_topics: ["核心思想", "应用场景"],
      source_message_id: "a-1",
      created_at: NOW,
      updated_at: NOW,
    },
    evidence: [],
    next_prompt: "你可以就其中任一部分继续追问，我会结合已覆盖主题自然深入。",
    gap_response: null,
    can_answer_reliably: true,
    can_cancel: true,
    can_retry: false,
    can_skip: false,
    can_follow_up: true,
    can_switch_mode: true,
  };
}

function message(id: string, role: "user" | "assistant", content: string, teaching: unknown = null) {
  return {
    message_id: id,
    conversation_id: CONVERSATION_ID,
    role,
    attempt_number: 1,
    status: "done",
    content,
    attachments: [],
    thinking: null,
    retrieval: null,
    web_search: null,
    arxiv_search: null,
    teaching,
    error_code: null,
    error_message: null,
    duration_ms: role === "assistant" ? 100 : null,
    model_id: role === "assistant" ? "qwen3.7-plus-2026-05-26" : null,
    run_lock_id: role === "assistant" ? "lock-issue23" : null,
    created_at: NOW,
    updated_at: NOW,
  };
}

test.describe("Issue 02：统一聊天流中的学习教学卡片", () => {
  test("展示目标、证据门和轻量学习进度", async ({ page }) => {
    const history = {
      conversation_id: CONVERSATION_ID,
      title: "Issue 23 学习对话",
      mode: "study",
      pinned: false,
      project_id: null,
      created_at: NOW,
      updated_at: NOW,
      messages: [
        message("u-1", "user", "解释量子纠缠"),
        message("a-1", "assistant", "# 核心思想\n\n基于本轮材料开始讲解 [reference:1]。", teachingProjection()),
      ],
      mode_events: [],
    };

    await page.route("**/api/chat/conversations", async (route) => {
      await route.fulfill({
        status: route.request().method() === "POST" ? 201 : 200,
        contentType: "application/json",
        body: JSON.stringify(route.request().method() === "POST" ? history : { conversations: [] }),
      });
    });
    await page.route(`**/api/chat/conversations/${CONVERSATION_ID}`, async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(history) });
    });

    const credentials = uniqueCredentials("issue23");
    await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
    await page.goto(`/chat/${CONVERSATION_ID}`);

    const card = page.getByTestId("teaching-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText("本轮教学");
    await expect(card).toContainText("证据门：证据充足");
    await expect(card).toContainText("量子纠缠讲义");
    await expect(card).toContainText("[reference:1]");
    await expect(card).toContainText("已覆盖主题");
    await expect(card).toContainText("核心思想、应用场景");
    await expect(card.getByRole("button", { name: "跳过这题" })).toHaveCount(0);
  });
});
