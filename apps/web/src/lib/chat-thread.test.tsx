import { isValidElement } from "react";
import { describe, expect, it } from "vitest";

import { buildThreadMessages } from "./chat-thread";
import {
  careerPlan,
  commuteRoute,
  githubProjects,
  learningResources,
  paperSearch,
  tiebaResearch,
} from "./chat-modules.test-support";
import type {
  ArxivSearchProjection,
  ChatAttachmentProjection,
  ChatMessageProjection,
  ContextNoteProjection,
  ImageTaskProjection,
  ModuleSuggestionProjection,
  RetrievalRoundProjection,
  TurnResultProjection,
  VideoTaskProjection,
} from "@/lib/api";

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

  it("完整服务端投影跨层保留正文、来源、模块与媒体字段", () => {
    const paper = paperSearch();
    const tieba = tiebaResearch();
    const career = careerPlan();
    const resources = learningResources();
    const commute = commuteRoute();
    const github = githubProjects();
    const contextNote: ContextNoteProjection = {
      state: "ready",
      profile_enabled: true,
      mode: "companion",
      used_at: NOW,
      profile_item_count: 1,
      material_categories: [],
      note: "本轮使用了 1 条已授权背景信息。",
    };
    const retrieval: RetrievalRoundProjection = {
      round_id: "r-1",
      message_id: "a-full",
      conversation_id: "c-1",
      use_knowledge_base: true,
      sufficiency: "sufficient",
      layers: [],
      citations: [],
      note: null,
      created_at: NOW,
    };
    const arxiv: ArxivSearchProjection = {
      status: "success",
      trigger_reason: "需要论文",
      query_summary: "transformer",
      papers: [],
      searched_at: null,
      error_code: null,
      error_message: null,
      upstream_status: null,
      can_retry: false,
      can_cancel: false,
      cache_hit: false,
      stale: false,
      retry_after_seconds: null,
      attempt_count: 0,
    };
    const suggestion: ModuleSuggestionProjection = {
      module_id: "paper",
      label: "使用论文搜索",
      reason: "你看起来在找论文。",
      text: "帮我找论文",
      needs_disambiguation: false,
    };
    const image: ImageTaskProjection = {
      task_id: "img-1",
      attempt_number: 1,
      kind: "generate",
      prompt: "画一只猫",
      status: "failed",
      retryable: false,
      deleted: false,
      synthetic: true,
      created_at: NOW,
      updated_at: NOW,
    };
    const video: VideoTaskProjection = {
      task_id: "video-1",
      prompt: "一段海浪视频",
      synthetic_media: true,
      status: "failed",
      retryable: false,
      deleted: false,
      created_at: NOW,
      updated_at: NOW,
    };
    const attachment: ChatAttachmentProjection = {
      object_id: "obj-file",
      original_filename: "统计讲义.pdf",
      media_type: "application/pdf",
      content_length: 2048,
      content_hash: "hash",
      conversation_id: "c-1",
      message_id: "u-1",
      status: "bound",
      ordinal: 1,
      ingestion_status: "ready",
      ingestion_error: null,
      adopted_from_conversation_id: "",
      adopted_from_object_id: "",
      created_at: NOW,
      updated_at: NOW,
    };

    const items = buildThreadMessages([
      message({
        message_id: "u-1",
        role: "user",
        content: "请结合资料回答",
        module_id: "paper",
        skill: { task_id: "skill-1" },
        attachments: [attachment],
      }),
      message({
        message_id: "a-full",
        content: "完整回答",
        context_note: contextNote,
        paper_search: paper,
        tieba_research: tieba,
        career_plan: career,
        learning_resources: resources,
        commute_route: commute,
        github_projects: github,
        retrieval,
        arxiv_search: arxiv,
        module_suggestion: suggestion,
        image,
        video,
      }),
    ]);

    expect(items).toHaveLength(2);
    expect(items[0]).toMatchObject({
      id: "u-1",
      role: "user",
      plainText: "请结合资料回答",
      moduleId: "paper",
      skill: { task_id: "skill-1" },
      attachments: [attachment],
    });
    expect(items[1]).toMatchObject({
      id: "a-full",
      role: "assistant",
      plainText: "完整回答",
      contextNote,
      paperSearch: paper,
      tiebaResearch: tieba,
      careerPlan: career,
      learningResources: resources,
      commuteRoute: commute,
      githubProjects: github,
      retrieval,
      arxivSearch: arxiv,
      moduleSuggestion: suggestion,
      image,
      video,
    });
    const body = items[1].body;
    expect(isValidElement(body)).toBe(true);
    if (isValidElement(body)) {
      expect(
        (body.props as { style?: { lineHeight?: string } }).style?.lineHeight
      ).toBe("var(--line-height-relaxed)");
    }
  });
});
