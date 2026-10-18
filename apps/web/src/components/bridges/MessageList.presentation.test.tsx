import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { copyTextToClipboard } from "@/lib/clipboard";
import type {
  CareerPlanningProjection,
  ChatAttachmentProjection,
  ContextNoteProjection,
  HumanizerResultProjection,
  ImageTaskProjection,
  RetrievalRoundProjection,
  TurnResultProjection,
  VideoTaskProjection,
} from "@/lib/api";
import {
  careerPlan,
  commuteRoute,
  githubProjects,
  learningResources,
  paperSearch,
  tiebaResearch,
} from "@/lib/chat-modules.test-support";

import { MessageList } from "./MessageList";
import type { ChatMessageView } from "@/lib/chat-message-view";

const NOW = "2026-10-17T08:00:00Z";

vi.mock("@/lib/clipboard", () => ({
  copyTextToClipboard: vi.fn(async () => true),
}));

afterEach(cleanup);

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
  message_id: "assistant-1",
  conversation_id: "conversation-1",
  use_knowledge_base: true,
  sufficiency: "sufficient",
  layers: [],
  citations: [
    {
      citation_id: "cite-1",
      source_layer: "knowledge_base",
      object_id: "obj-1",
      filename: "讲义.pdf",
      media_type: "application/pdf",
      page_number: null,
      section_title: null,
      snippet: "引用片段",
      rank: 1,
    },
  ],
  note: null,
  created_at: NOW,
};

const humanizer: HumanizerResultProjection = {
  task_id: "task-1",
  skill_id: "bridges-humanizer",
  skill_version: "1.0.0",
  path: "rewrite",
  genre: null,
  contract: {
    path: "rewrite",
    source_text: "原文",
    allow_assumptions: false,
    allow_first_person: false,
  },
  status: "done",
  repair_attempts: 0,
  writing_call_count: 1,
  process_state: "done",
  output: {
    final_text: "历史人味化正文。",
    edits: [],
    fact_check: [],
    open_questions: [],
    quality_status: "ok",
  },
  article: {
    projection_version: "1",
    audit_version: "1",
    delivery_status: "delivered",
    material_state: "sufficient",
    final_text: "历史人味化正文。",
    fidelity: {
      passed: true,
      blocking_count: 0,
      needs_confirmation_count: 0,
      items: [],
    },
    style_review: {
      finding_count: 0,
      warning_count: 0,
      suggestion_count: 0,
      items: [],
    },
    revision: null,
    evidence: [],
    confirmations: [],
  },
};

const careerPlanning: CareerPlanningProjection = {
  plan_id: "plan-1",
  intent: "历史生涯规划",
  route_contract: null,
  status: "error",
  profile_enabled: false,
  profile_used: false,
  verified_at: NOW,
  output: null,
  clarification: null,
  evidence_sources: [],
  review: null,
  process_state: "error",
  process_steps: [],
  error_code: "career_planning_failed",
  error_message: "历史规划未完成。",
  run_id: null,
  run_lock_refs: [],
  created_at: NOW,
};

const imageTask: ImageTaskProjection = {
  task_id: "img-1",
  attempt_number: 1,
  kind: "generate",
  prompt: "画一只猫",
  status: "failed",
  error_code: "image_failed",
  error_message: "图片生成失败。",
  retryable: false,
  deleted: false,
  synthetic: true,
  created_at: NOW,
  updated_at: NOW,
};

const videoTask: VideoTaskProjection = {
  task_id: "video-1",
  prompt: "一段海浪视频",
  synthetic_media: true,
  status: "failed",
  error_code: "video_failed",
  error_message: "视频生成失败。",
  retryable: false,
  deleted: false,
  created_at: NOW,
  updated_at: NOW,
};

const cancelledResult: TurnResultProjection = {
  version: "turn-result-v1",
  outcome: "cancelled",
  outcome_label: "本轮已停止，不会自动继续。",
  trust: null,
  trust_label: null,
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
};

const partialResult: TurnResultProjection = {
  ...cancelledResult,
  outcome: "partial",
  outcome_label: "本轮先交付有效部分，其余未完成。",
  trust: "evidence_bound",
  trust_label: "结果已附证据，仍有未完成核验的范围。",
  delivered: [
    { module_id: "paper", label: "论文检索", state: "success", trust: "qualified", detail: "" },
  ],
  blocked: [
    { module_id: "tieba", label: "贴吧搜集", state: "error", trust: "evidence_bound", detail: "接口失败" },
  ],
  gaps: ["贴吧来源未读取"],
};

const needsInputResult: TurnResultProjection = {
  ...cancelledResult,
  outcome: "needs_input",
  outcome_label: "等待你补充信息后继续；任务保持待输入。",
  wait_reason: "请补充出发地点",
};

function assistant(overrides: Partial<ChatMessageView> = {}): ChatMessageView {
  return {
    id: "assistant-1",
    role: "assistant",
    plainText: "回答正文",
    ...overrides,
  };
}

describe("消息呈现边界（工单 02）", () => {
  it("上下文、学习、论文、贴吧、通勤、检索与模块建议仍在同一条助手消息内", () => {
    render(
      <MessageList
        conversationId="conversation-1"
        messages={[
          assistant({
            contextNote,
            paperSearch: paperSearch({
              queries: [
                {
                  source: "arxiv",
                  query: "transformer",
                  status: "success",
                  evidence_count: 1,
                  retrieved_at: NOW,
                  retryable: false,
                },
              ],
            }),
            tiebaResearch: tiebaResearch(),
            learningResources: learningResources(),
            commuteRoute: commuteRoute(),
            careerPlan: careerPlan(),
            githubProjects: githubProjects(),
            moduleSuggestion: {
              module_id: "paper",
              label: "使用论文搜索",
              reason: "你看起来在找论文。",
              text: "帮我找论文",
              needs_disambiguation: false,
            },
            retrieval,
          }),
          assistant({ id: "assistant-2" }),
        ]}
      />
    );

    expect(screen.getAllByTestId("public-process")).toHaveLength(2);
    const process = screen.getAllByTestId("public-process")[0];
    expect(within(process).queryByTestId("public-process-details")).toBeNull();
    fireEvent.click(within(process).getByTestId("public-process-toggle"));
    expect(within(process).getByTestId("context-note-details").textContent).toContain(
      "本轮使用了 1 条已授权背景信息"
    );
    expect(screen.getByTestId("paper-details-entry")).toBeTruthy();
    expect(screen.queryByTestId("paper-search-card-success")).toBeNull();
    expect(screen.getByTestId("tieba-research-card-success")).toBeTruthy();
    expect(screen.getByTestId("resources-card-success")).toBeTruthy();
    expect(screen.getByTestId("commute-route-card-success")).toBeTruthy();
    expect(screen.getByTestId("career-plan-card-success")).toBeTruthy();
    expect(screen.getByTestId("github-projects-card-success")).toBeTruthy();
    expect(screen.getByTestId("module-suggestion")).toBeTruthy();
    expect(screen.getByTestId("retrieval-citations-card")).toBeTruthy();
    expect(screen.getByText("回答正文")).toBeTruthy();
  });

  it("历史尝试、停止正文、空正文与操作行保持原行为", () => {
    render(
      <MessageList
        messages={[
          assistant({
            id: "assistant-stopped",
            plainText: "已生成的部分正文",
            status: "stopped",
            turnResult: cancelledResult,
            previousAttempts: [
              { attemptNumber: 1, status: "error", errorMessage: "旧失败" },
            ],
          }),
          assistant({ id: "assistant-empty", plainText: "" }),
        ]}
      />
    );

    expect(screen.getByText("此问题的前 1 次尝试")).toBeTruthy();
    expect(screen.getByText(/第 1 次尝试（失败）：旧失败/)).toBeTruthy();
    expect(screen.getByText("已生成的部分正文")).toBeTruthy();
    expect(screen.getByTestId("delivery-status")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getAllByRole("toolbar", { name: "消息操作" })).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /朗读/ })).toBeNull();
  });

  it("错误、待补充与部分失败直接可见，不依赖展开公开过程", () => {
    render(
      <MessageList
        conversationId="conversation-1"
        messages={[
          assistant({
            id: "assistant-partial",
            plainText: "已交付的正文",
            status: "error",
            errorText: "部分来源读取失败。",
            turnResult: partialResult,
            thinking: {
              steps: ["检索论文"],
              evidence: [],
              tools: [],
              quality: [],
              seconds: null,
            },
          }),
          assistant({ id: "assistant-needs-input", turnResult: needsInputResult }),
        ]}
      />
    );

    expect(screen.getByRole("alert").textContent).toContain("部分来源读取失败");
    expect(screen.getByText("已交付的正文")).toBeTruthy();
    const results = screen.getAllByTestId("delivery-status");
    expect(results[0].textContent).toContain("本轮先交付有效部分");
    expect(results[1].textContent).toContain("请补充出发地点");
    expect(screen.getAllByTestId("public-process-toggle")).toHaveLength(2);
    expect(screen.queryByTestId("public-process-details")).toBeNull();
  });

  it("流式正文不继承历史消息的宽松行高", () => {
    render(
      <MessageList
        messages={[assistant({ id: "assistant-stream", status: "streaming" })]}
      />
    );

    expect(screen.getByText("回答正文").style.lineHeight).toBe("");
  });

  it("空正文带附件时仍保留附件与正文的间距", () => {
    const attachment: ChatAttachmentProjection = {
      object_id: "obj-file",
      original_filename: "统计讲义.pdf",
      media_type: "application/pdf",
      content_length: 2048,
      content_hash: "hash",
      conversation_id: "conversation-1",
      message_id: "user-empty",
      status: "bound",
      ordinal: 1,
      ingestion_status: "ready",
      ingestion_error: null,
      adopted_from_conversation_id: "",
      adopted_from_object_id: "",
      created_at: NOW,
      updated_at: NOW,
    };
    render(
      <MessageList
        messages={[
          {
            id: "user-empty",
            role: "user",
            plainText: "",
            attachments: [attachment],
          },
        ]}
      />
    );

    expect(
      screen.getByRole("list", { name: "消息中的附件" }).style.marginBottom
    ).toBe("var(--space-2)");
  });

  it("复制与已有反馈行为继续可用", async () => {
    render(<MessageList messages={[assistant()]} />);

    fireEvent.click(screen.getByRole("button", { name: "复制" }));
    expect(await screen.findByText("已复制")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "回答有帮助" }));
    expect(screen.getByText("已在当前页面标记为有帮助")).toBeTruthy();
  });

  it("助手正文按连续 Markdown 渲染，复制保留完整原文", async () => {
    const markdown = [
      "## 结论",
      "",
      "**先看重点**，再看代码：",
      "",
      "```ts",
      "const answer = 42;",
      "```",
    ].join("\n");

    render(<MessageList messages={[assistant({ plainText: markdown })]} />);

    expect(screen.getByRole("heading", { level: 2, name: "结论" })).toBeTruthy();
    expect(screen.getByText("先看重点").tagName).toBe("STRONG");
    expect(screen.getByText("const answer = 42;").closest("pre")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "复制" }));
    expect(copyTextToClipboard).toHaveBeenCalledWith(markdown);
  });

  it("用户消息仍按纯文本处理，助手正文不套统一大卡片", () => {
    render(
      <MessageList
        messages={[
          {
            id: "user-markdown",
            role: "user",
            plainText: "**这不是标题**",
          },
          assistant({ plainText: "## 这是助手标题" }),
        ]}
      />
    );

    expect(screen.getByText("**这不是标题**").tagName).toBe("P");
    expect(screen.queryByRole("heading", { name: "这不是标题" })).toBeNull();
    expect(
      screen.getByRole("heading", { level: 2, name: "这是助手标题" })
    ).toBeTruthy();

    const article = screen.getByTestId("assistant-message");
    expect(article.style.backgroundColor).toBe("");
    expect(article.style.border).toBe("");
  });

  it("退役能力的历史结果仍可读，但不新增启动入口", () => {
    render(
      <MessageList
        conversationId="conversation-1"
        messages={[
          assistant({
            humanizer,
            careerPlanning,
            image: imageTask,
            video: videoTask,
          }),
        ]}
      />
    );

    expect(screen.getByTestId("humanizer-result-card")).toBeTruthy();
    expect(screen.getByTestId("career-result-card")).toBeTruthy();
    expect(screen.getByText(/图片生成已退役/)).toBeTruthy();
    expect(screen.getByText(/视频生成已退役/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
  });
});
