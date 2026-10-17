import { describe, expect, it } from "vitest";

import type { ChatStreamEvent, ChatMessageProjection } from "@/lib/api";

import {
  activeRunFromAssistant,
  activeRunFromError,
  applyRunProgress,
  type ActiveRun,
} from "./chat-run";

const NOW = "2026-10-17T08:00:00Z";

function message(
  overrides: Partial<ChatMessageProjection> = {}
): ChatMessageProjection {
  return {
    message_id: "a-1",
    conversation_id: "c-1",
    role: "assistant",
    attempt_number: 1,
    status: "streaming",
    content: "",
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
    duration_ms: null,
    model_id: null,
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

function run(overrides: Partial<ActiveRun> = {}): ActiveRun {
  return {
    messageId: "a-1",
    content: "已收到",
    kind: "send",
    status: "streaming",
    thinking: null,
    webSearch: null,
    arxivSearch: null,
    teaching: null,
    stage: null,
    node: null,
    humanizerProcess: null,
    careerProcess: null,
    imageProcess: null,
    videoProcess: null,
    ...overrides,
  };
}

function event(data: ChatStreamEvent["data"]): ChatStreamEvent {
  return { event: data.kind, data } as ChatStreamEvent;
}

describe("chat run 投影与事件归并", () => {
  it("创建响应已有部分正文时保留内容，终态投影不再显示排队阶段", () => {
    const partial = activeRunFromAssistant(
      message({ content: "已接收的部分正文" }),
      "send"
    );
    expect(partial.content).toBe("已接收的部分正文");
    expect(partial.stage?.stage).toBe("queued");

    const replay = activeRunFromAssistant(
      message({ status: "done", content: "完整回答" }),
      "send"
    );
    expect(replay.content).toBe("完整回答");
    expect(replay.stage).toBeNull();
  });

  it("delta 只追加到匹配的消息，终态后不再推进正文", () => {
    const delta = event({ kind: "delta", message_id: "a-1", delta: "，继续" });
    const next = applyRunProgress(run(), delta);
    expect(next?.content).toBe("已收到，继续");

    expect(
      applyRunProgress(run(), {
        ...delta,
        data: { kind: "delta", message_id: "other", delta: "污染" },
      })
    ).toEqual(run());

    const done = run({ status: "done" });
    expect(applyRunProgress(done, delta)).toBe(done);
    const failed = run({ status: "error", errorText: "失败" });
    expect(applyRunProgress(failed, delta)).toBe(failed);
  });

  it("节点完成后清空阶段，阶段事件接管进度行", () => {
    const nodeStarted = event({
      kind: "node",
      message_id: "a-1",
      node: "validate_turn",
      status: "started",
    });
    const withNode = applyRunProgress(run(), nodeStarted);
    expect(withNode?.node?.node).toBe("validate_turn");

    const nodeCompleted = event({
      kind: "node",
      message_id: "a-1",
      node: "validate_turn",
      status: "completed",
    });
    const cleared = applyRunProgress(withNode, nodeCompleted);
    expect(cleared?.node).toBeNull();
    expect(cleared?.stage).toBeNull();

    const stage = event({
      kind: "stage",
      message_id: "a-1",
      stage: "model_generation",
      status: "active",
    });
    const withStage = applyRunProgress(
      run({ node: nodeStarted.data.kind === "node" ? nodeStarted.data : null }),
      stage
    );
    expect(withStage?.stage?.stage).toBe("model_generation");
    expect(withStage?.node).toBeNull();
  });

  it("终态后迟到的节点与阶段事件不改变公开过程", () => {
    const done = run({ status: "done" });
    const lateNode = event({
      kind: "node",
      message_id: "a-1",
      node: "persist_result",
      status: "started",
    });
    const lateStage = event({
      kind: "stage",
      message_id: "a-1",
      stage: "model_generation",
      status: "active",
    });
    expect(applyRunProgress(done, lateNode)).toBe(done);
    expect(applyRunProgress(done, lateStage)).toBe(done);

    const otherMessage = event({
      kind: "stage",
      message_id: "other",
      stage: "public_search",
      status: "active",
    });
    const streaming = run();
    expect(applyRunProgress(streaming, otherMessage)).toBe(streaming);
  });

  it("错误事件保留已生成正文与公开投影，并换算真实耗时", () => {
    const current = run({
      content: "停止前正文",
      turnResult: {
        version: "turn-result-v1",
        outcome: "complete",
        outcome_label: "本轮已完成。",
        trust: "qualified",
        trust_label: "结果已通过核验。",
        capability_list: [],
        actual_module_id: null,
        requested_module_id: null,
        route_source: "ordinary_chat",
        delivered: [],
        blocked: [],
        gaps: [],
        recovery: null,
        wait_reason: null,
        task_id: null,
        task_version: null,
      },
    });
    const failed = activeRunFromError(
      current,
      {
        kind: "error",
        message_id: "a-1",
        error: {
          code: "stream_interrupted",
          message: "生成中断。",
          retryable: true,
        },
        thinking: null,
        duration_ms: 2400,
        web_search: null,
        arxiv_search: null,
        teaching: null,
      },
      "send"
    );
    expect(failed.status).toBe("error");
    expect(failed.content).toBe("停止前正文");
    expect(failed.errorText).toBe("生成中断。");
    expect(failed.turnResult?.outcome).toBe("complete");
  });
});
