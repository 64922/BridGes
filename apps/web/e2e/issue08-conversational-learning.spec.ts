import { expect, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const NOW = "2026-08-08T00:00:00Z";
const CONVERSATION_ID = "conv-issue08";

function teachingProjection(overrides: Record<string, unknown> = {}) {
  return {
    status: "ready",
    mission: {
      mission_id: "mission-1",
      stage: "mission_setup",
      goal: "学习“Transformer”并理解其核心机制",
      user_intent: "我想学习 Transformer",
      current_concept: null,
      level_assumption: "暂按初学者处理；你的回答会调整深度与例子。",
      level_basis: "尚未确认，默认按初学者开始。",
      taught_concepts: [],
      next_action: "回答一个澄清问题，或直接选择按初学者开始。",
      blocked_reason: null,
      recovery_steps: [],
    },
    goal: "学习“Transformer”并理解其核心机制",
    level_assumption: "暂按初学者处理；你的回答会调整深度与例子。",
    steps: ["确认学习目标", "确认你的已有水平", "开始第一个概念"],
    check_method: "本轮只问一个关键问题；也可以直接按初学者开始。",
    evidence_gate: {
      status: "sufficient",
      reason: "目标确认阶段不生成正式教学回答，无需通过事实证据门。",
      local_sources: [],
      external_sources: [],
      required_search: "none",
      search_status: null,
      gap: null,
      recovery_steps: [],
      checked_at: NOW,
    },
    quiz: null,
    evidence: [],
    next_prompt: "你之前接触过“Transformer”吗？可以简单回答，也可以直接说“按初学者开始”。",
    gap_response: null,
    can_answer_reliably: false,
    can_cancel: true,
    can_retry: false,
    can_skip: true,
    can_follow_up: true,
    can_switch_mode: true,
    ...overrides,
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
    run_lock_id: role === "assistant" ? "lock-issue08" : null,
    created_at: NOW,
    updated_at: NOW,
  };
}

async function stubConversation(page: import("@playwright/test").Page, history: unknown) {
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
}

test.describe("Issue 08：对话式教学循环的教学卡片", () => {
  test("mission_setup 阶段显示目标确认与一键按初学者开始", async ({ page }) => {
    const history = {
      conversation_id: CONVERSATION_ID,
      title: "Issue 08 学习对话",
      mode: "study",
      pinned: false,
      project_id: null,
      created_at: NOW,
      updated_at: NOW,
      messages: [
        message("u-1", "user", "我想学习 Transformer"),
        message("a-1", "assistant", "好的，我们一起来学：学习“Transformer”并理解其核心机制。你之前接触过“Transformer”吗？", teachingProjection()),
      ],
      mode_events: [],
    };
    await stubConversation(page, history);

    const credentials = uniqueCredentials("issue08a");
    await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
    await page.goto(`/chat/${CONVERSATION_ID}`);

    const card = page.getByTestId("teaching-card").last();
    await expect(card).toBeVisible();
    await expect(card).toContainText("确认目标");
    await expect(card).toContainText("学习“Transformer”并理解其核心机制");
    await expect(card.getByRole("button", { name: "按初学者开始" })).toBeVisible();
  });

  test("micro_lesson 阶段显示当前概念、已讲进度与下一步", async ({ page }) => {
    const microLesson = teachingProjection({
      mission: {
        mission_id: "mission-1",
        stage: "micro_lesson",
        goal: "学习“Transformer”并理解其核心机制",
        user_intent: "我想学习 Transformer",
        current_concept: "Transformer",
        level_assumption: "初学者",
        level_basis: "用户选择按初学者开始（或未声明已有基础）。",
        taught_concepts: ["Transformer 注意力机制"],
        next_action: "讲解“Transformer 注意力机制”并做一次理解检查。",
        blocked_reason: null,
        recovery_steps: [],
      },
      evidence_gate: {
        status: "sufficient",
        reason: "本地证据不足，已使用公开来源补充教学必需知识。",
        local_sources: [],
        external_sources: [{
          source_type: "tavily",
          source_id: "web-transformer",
          title: "Transformer 架构公开讲义",
          locator: "https://example.com/transformer",
          url: "https://example.com/transformer",
          accessed_at: NOW,
        }],
        required_search: "tavily",
        search_status: "ready",
        gap: null,
        recovery_steps: [],
        checked_at: NOW,
      },
      quiz: {
        question_id: "question-1",
        concept: "Transformer",
        question: "请用自己的话解释“Transformer”的核心含义，并举一个边界清楚的例子。",
        expected_focus: ["Transformer"],
        evidence_refs: ["web-transformer"],
        can_skip: true,
        can_follow_up: true,
      },
      can_answer_reliably: true,
    });
    const history = {
      conversation_id: CONVERSATION_ID,
      title: "Issue 08 学习对话",
      mode: "study",
      pinned: false,
      project_id: null,
      created_at: NOW,
      updated_at: NOW,
      messages: [
        message("u-1", "user", "我想学习 Transformer"),
        message("a-1", "assistant", "好的，我们一起来学。", teachingProjection()),
        message("u-2", "user", "按初学者开始"),
        message("a-2", "assistant", "基于合格来源讲解 [web-transformer]。", microLesson),
      ],
      mode_events: [],
    };
    await stubConversation(page, history);

    const credentials = uniqueCredentials("issue08b");
    await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
    await page.goto(`/chat/${CONVERSATION_ID}`);

    const card = page.getByTestId("teaching-card").last();
    await expect(card).toBeVisible();
    await expect(card).toContainText("讲解概念");
    await expect(card).toContainText("当前概念：Transformer");
    await expect(card).toContainText("已讲概念");
    await expect(card).toContainText("Transformer 注意力机制");
    await expect(card).toContainText("下一步");
    // 每轮最多一道理解检查题，且可跳过。
    await expect(card.getByRole("button", { name: "跳过这题" })).toBeVisible();
    // 来源可追溯：公开来源标题出现，图片等无关材料不出现。
    await expect(card).toContainText("Transformer 架构公开讲义");
  });

  test("刷新页面后教学进度保持一致（mission 恢复）", async ({ page }) => {
    const microLesson = teachingProjection({
      mission: {
        mission_id: "mission-1",
        stage: "micro_lesson",
        goal: "学习“Transformer”并理解其核心机制",
        user_intent: "我想学习 Transformer",
        current_concept: "Transformer",
        level_assumption: "初学者",
        level_basis: "用户选择按初学者开始（或未声明已有基础）。",
        taught_concepts: ["Transformer 注意力机制"],
        next_action: "讲解“Transformer 注意力机制”并做一次理解检查。",
        blocked_reason: null,
        recovery_steps: [],
      },
      can_answer_reliably: true,
    });
    const history = {
      conversation_id: CONVERSATION_ID,
      title: "Issue 08 学习对话",
      mode: "study",
      pinned: false,
      project_id: null,
      created_at: NOW,
      updated_at: NOW,
      messages: [
        message("u-1", "user", "我想学习 Transformer"),
        message("a-1", "assistant", "好的，我们一起来学。", teachingProjection()),
        message("u-2", "user", "按初学者开始"),
        message("a-2", "assistant", "基于合格来源讲解。", microLesson),
      ],
      mode_events: [],
    };
    await stubConversation(page, history);

    const credentials = uniqueCredentials("issue08c");
    await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
    await page.goto(`/chat/${CONVERSATION_ID}`);
    await expect(page.getByTestId("teaching-card").last()).toContainText("当前概念：Transformer");

    // 刷新（离开页面再进入）后恢复同一教学进度。
    await page.reload();
    await expect(page.getByTestId("teaching-card").last()).toContainText("当前概念：Transformer");
    await expect(page.getByTestId("teaching-card").last()).toContainText("讲解概念");
  });

  test("来源受阻时显示恢复动作且不回退成无关回答", async ({ page }) => {
    const blocked = teachingProjection({
      status: "empty",
      mission: {
        mission_id: "mission-1",
        stage: "blocked",
        goal: "学习“Transformer”并理解其核心机制",
        user_intent: "我想学习 Transformer",
        current_concept: "Transformer",
        level_assumption: "初学者",
        level_basis: "用户选择按初学者开始（或未声明已有基础）。",
        taught_concepts: [],
        next_action: "当前来源不足；重试、上传材料或更换主题后继续同一进度。",
        blocked_reason: "当前附件、项目文件和授权知识库没有可用命中；公开补充检索没有返回可用结果。",
        recovery_steps: ["重试公开检索，或上传一份与目标直接相关的材料。"],
      },
      evidence_gate: {
        status: "insufficient",
        reason: "本地证据不足；公开补充检索没有返回可用结果。",
        local_sources: [],
        external_sources: [],
        required_search: "tavily",
        search_status: "empty",
        gap: "没有找到能覆盖本轮目标的公开来源，暂不能可靠断言关键结论。",
        recovery_steps: ["重试公开检索，或上传一份与目标直接相关的材料。"],
        checked_at: NOW,
      },
      next_prompt: "重试可继续同一教学进度。",
      can_retry: true,
    });
    const history = {
      conversation_id: CONVERSATION_ID,
      title: "Issue 08 学习对话",
      mode: "study",
      pinned: false,
      project_id: null,
      created_at: NOW,
      updated_at: NOW,
      messages: [
        message("u-1", "user", "我想学习 Transformer"),
        message("a-1", "assistant", "好的，我们一起来学。", teachingProjection()),
        message("u-2", "user", "按初学者开始"),
        message("a-2", "assistant", "当前来源不足，暂不能可靠断言关键结论。请重试或上传材料。", blocked),
      ],
      mode_events: [],
    };
    await stubConversation(page, history);

    const credentials = uniqueCredentials("issue08d");
    await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
    await page.goto(`/chat/${CONVERSATION_ID}`);

    const card = page.getByTestId("teaching-card").last();
    await expect(card).toBeVisible();
    await expect(card).toContainText("来源受阻");
    await expect(card).toContainText("恢复动作");
    await expect(card).toContainText("重试公开检索，或上传一份与目标直接相关的材料。");
    // 正文说明受阻与恢复动作，不输出与学习无关内容。
    await expect(page.getByText("当前来源不足，暂不能可靠断言关键结论。请重试或上传材料。")).toBeVisible();
  });
});
