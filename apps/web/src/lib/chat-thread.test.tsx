import { describe, expect, it } from "vitest";

import { buildThreadMessages } from "./chat-thread";
import type { ChatMessageProjection, TurnResultProjection } from "@/lib/api";

const NOW = "2026-10-05T08:00:00Z";

function message(
  overrides: Partial<ChatMessageProjection> = {}
): ChatMessageProjection {
  return {
    message_id: "m-1",
    conversation_id: "c-1",
    role: "assistant",
    attempt_number: 1,
    status: "done",
    content: "回答正文",
    attachments: [],
    thinking: null,
    retrieval: null,
    retrieval_decision: null,
    web_search: null,
    arxiv_search: null,
    route: null,
    teaching: null,
    context_note: null,
    skill: null,
    humanizer: null,
    career_planning: null,
    image: null,
    video: null,
    mcp_call: null,
    read_aloud: null,
    error_code: null,
    error_message: null,
    turn_result: null,
    duration_ms: 1200,
    model_id: "qwen3.7-plus-2026-05-26",
    run_lock_id: null,
    active_run: null,
    module_id: null,
    paper_search: null,
    module_suggestion: null,
    tieba_research: null,
    career_plan: null,
    learning_resources: null,
    commute_route: null,
    github_projects: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function turnResult(
  overrides: Partial<TurnResultProjection> = {}
): TurnResultProjection {
  return {
    version: "turn-result-v1",
    outcome: "complete",
    outcome_label: "本轮已完成。",
    trust: "qualified",
    trust_label: "结果已通过核验。",
    capability_list: ["paper"],
    actual_module_id: "paper",
    requested_module_id: null,
    route_source: "body_intent",
    delivered: [],
    blocked: [],
    gaps: [],
    recovery: null,
    wait_reason: null,
    task_id: null,
    task_version: null,
    ...overrides,
  };
}

describe("buildThreadMessages（工单 38）", () => {
  it("停止的最终尝试渲染为 stopped 且不丢结果投影", () => {
    const items = buildThreadMessages([
      message({
        message_id: "a-stopped",
        status: "stopped",
        content: "部分正文",
        turn_result: turnResult({
          outcome: "cancelled",
          outcome_label: "本轮已停止，不会自动继续。",
          trust: null,
          trust_label: null,
        }),
      }),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].status).toBe("stopped");
    expect(items[0].turnResult?.outcome).toBe("cancelled");
    expect(items[0].errorText).toBeUndefined();
  });

  it("错误保持 error 文案，完成不携带状态芯片", () => {
    const errored = buildThreadMessages([
      message({ status: "error", error_message: "模型暂不可用。" }),
    ]);
    expect(errored[0].status).toBe("error");
    expect(errored[0].errorText).toBe("模型暂不可用。");
    const done = buildThreadMessages([message({ turn_result: turnResult() })]);
    expect(done[0].status).toBeUndefined();
    expect(done[0].turnResult?.outcome).toBe("complete");
  });

  it("同轮历史失败尝试折叠且不进入结果投影", () => {
    const items = buildThreadMessages([
      message({ message_id: "a-1", status: "error", error_message: "旧失败" }),
      message({ message_id: "a-2", status: "done", content: "新回答" }),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].id).toBe("a-2");
    expect(items[0].previousAttempts).toEqual([
      { attemptNumber: 1, status: "error", errorMessage: "旧失败" },
    ]);
  });
});
