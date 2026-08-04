import { expect, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const NOW = "2026-08-04T00:00:00Z";
const CONVERSATION_ID = "conv-issue23";

function teachingProjection() {
  return {
    status: "ready",
    goal: "本轮目标：理解量子纠缠，并能说明其核心机制。",
    level_assumption: "暂按初学者处理；会根据你的回答调整深度。",
    steps: ["确认目标", "用例子解释", "进行一次理解检查"],
    check_method: "每轮最多一道理解检查题；可以跳过或追问。",
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
    quiz: {
      question_id: "question-1",
      concept: "量子纠缠",
      question: "请用自己的话解释量子纠缠。",
      expected_focus: ["量子纠缠"],
      evidence_refs: ["cit-1"],
      can_skip: true,
      can_follow_up: true,
    },
    evidence: [],
    next_prompt: "你可以回答这道题，也可以跳过、追问或切换模式。",
    gap_response: null,
    can_answer_reliably: true,
    can_cancel: true,
    can_retry: false,
    can_skip: true,
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

test.describe("Issue 23：统一聊天流中的学习教学卡片", () => {
  test("展示目标、证据门和可跳过的理解检查", async ({ page }) => {
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
        message("a-1", "assistant", "基于本轮材料开始讲解。", teachingProjection()),
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
    await expect(card).toContainText("请用自己的话解释量子纠缠");
    await expect(card.getByRole("button", { name: "跳过这题" })).toBeVisible();
  });
});
