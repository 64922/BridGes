import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatMessageView } from "@/lib/chat-message-view";

import { MessageList } from "./MessageList";
import type {
  ChatAttachmentProjection,
  LearningResourcesProjection,
  PaperSearchProjection,
  TiebaResearchProjection,
} from "@/lib/api";

afterEach(cleanup);

const paperProjection: PaperSearchProjection = {
  status: "success",
  original_phrase: "Transformer",
  normalized_term: "Transformer",
  expansions: [],
  confidence: 0.9,
  context_label: "机器学习中的 Transformer 结构",
  research_question: "Transformer 的研究问题",
  queries: [],
  final_query: "transformer",
  papers: [
    {
      order: 1,
      candidate_id: "arxiv:1706.03762",
      arxiv_id: "1706.03762",
      title: "Attention Is All You Need",
      authors: ["Ashish Vaswani"],
      published_year: 2017,
      source: "arxiv",
      sources: ["arxiv"],
      abs_url: "https://arxiv.org/abs/1706.03762",
      pdf_url: "https://arxiv.org/pdf/1706.03762",
      read_scope: "abstract",
      full_text_available: true,
      relevance: "direct",
      research_group: "Transformer 注意力",
      source_abstract: "We propose the Transformer.",
      role: "foundation",
      reason_zh: "奠基工作，先读这一篇。",
      match_basis: "标题与摘要命中 transformer。",
      unverified: [],
    },
  ],
  requested_count: 3,
  evidence_notes: [],
  pending: null,
  searched_at: null,
  error_code: null,
  error_message: null,
  retryable: false,
};

const resourcesProjection: LearningResourcesProjection = {
  status: "success",
  original_phrase: "深度学习",
  normalized_term: "深度学习",
  expansions: ["deep learning"],
  confidence: 0.9,
  goal: null,
  level_label: "零基础入门",
  level_basis: "你说了「零基础」",
  queries: [],
  final_query: "深度学习 deep learning",
  items: [],
  requested_books: 2,
  requested_videos: 3,
  needs_practice_project: false,
  path_verified: false,
  parallel_limit: 2,
  evidence_notes: [],
  pending: null,
  searched_at: null,
  error_code: null,
  error_message: null,
  retryable: false,
};

function userMessage(overrides: Partial<ChatMessageView> = {}): ChatMessageView {
  return {
    id: "user-1",
    role: "user",
    plainText: "帮我找 Transformer 的论文",
    ...overrides,
  };
}

function assistantMessage(overrides: Partial<ChatMessageView> = {}): ChatMessageView {
  return {
    id: "assistant-1",
    role: "assistant",
    plainText: "回答",
    ...overrides,
  };
}

it("贴吧结构化结果只展示一次，空态正文保留原因而不重复导出正文", () => {
  const tieba: TiebaResearchProjection = {
    status: "empty", topic: "国庆节 假期", original_question: "我想了解国庆节假期的讨论",
    question_kind: "mixed", parallel_evidence: false, official_check_requested: true, retryable: false,
    forum: "华东交通大学吧", topic_terms: ["国庆节", "假期"],
    time_filter: { applied: false, recent_count: 0, historical_count: 0, unknown_count: 0, note: "未指定年份" },
    empty_reason: "没有取得相关讨论",
  };
  render(<MessageList conversationId="conversation-1" messages={[assistantMessage({
    status: "done", tiebaResearch: tieba,
    plainText: "完整导出正文",
  })]} />);
  expect(screen.getAllByTestId("tieba-answer")).toHaveLength(1);
  expect(screen.queryByText("完整导出正文")).toBeNull();
  expect(screen.getByText(/没有取得相关讨论/)).toBeTruthy();
});

describe("MessageList 附件卡片（V2 Issue 05/06）", () => {
  function attachment(
    overrides: Partial<ChatAttachmentProjection> = {}
  ): ChatAttachmentProjection {
    return {
      object_id: "obj-file",
      original_filename: "统计讲义.pdf",
      media_type: "application/pdf",
      content_length: 2048,
      content_hash: "hash",
      conversation_id: "conversation-1",
      message_id: "user-1",
      status: "bound",
      ordinal: 1,
      ingestion_status: "ready",
      ingestion_error: null,
      adopted_from_conversation_id: "",
      adopted_from_object_id: "",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      ...overrides,
    };
  }

  it("文件附件渲染为文档卡片：文件名、类型、大小、解析状态与下载", () => {
    render(
      <MessageList
        conversationId="conversation-1"
        messages={[userMessage({ attachments: [attachment()] })]}
      />
    );

    const card = screen.getByTestId("message-file-card");
    expect(card.textContent).toContain("统计讲义.pdf");
    expect(card.textContent).toContain("PDF");
    expect(card.textContent).toContain("2 KB");
    expect(screen.getByTestId("ingestion-status-ready").textContent).toContain("已解析，可引用");
    const download = screen.getByRole("link", { name: /下载原件/ }) as HTMLAnchorElement;
    expect(download.getAttribute("href")).toBe(
      "/api/chat/conversations/conversation-1/attachments/obj-file/download"
    );
    // 文件不当图片渲染。
    expect(screen.queryByAltText("用户发送的照片：统计讲义.pdf")).toBeNull();
  });

  it("解析失败的文件附件在卡片里给出中文原因", () => {
    render(
      <MessageList
        conversationId="conversation-1"
        messages={[
          userMessage({
            attachments: [
              attachment({
                ingestion_status: "error",
                ingestion_error: "解析失败：DOCX 文件结构损坏。",
              }),
            ],
          }),
        ]}
      />
    );

    expect(screen.getByTestId("ingestion-status-error").textContent).toContain("解析失败");
    expect(screen.getByText(/DOCX 文件结构损坏/)).toBeTruthy();
  });

  it("照片附件仍渲染缩略图", () => {
    render(
      <MessageList
        conversationId="conversation-1"
        messages={[
          userMessage({
            attachments: [
              attachment({
                object_id: "obj-photo",
                original_filename: "板书.png",
                media_type: "image/png",
                ingestion_status: "none",
              }),
            ],
          }),
        ]}
      />
    );

    expect(screen.getByAltText("用户发送的照片：板书.png")).toBeTruthy();
    expect(screen.queryByTestId("message-file-card")).toBeNull();
  });

  it("同名附件以对象身份区分，不按文件名合并或替换", () => {
    const onOpenAttachment = vi.fn();
    render(
      <MessageList
        conversationId="conversation-1"
        messages={[
          userMessage({
            attachments: [
              attachment({ object_id: "obj-first", original_filename: "讲义.pdf" }),
              attachment({ object_id: "obj-second", original_filename: "讲义.pdf" }),
            ],
          }),
        ]}
        onOpenAttachment={onOpenAttachment}
      />
    );

    expect(screen.getAllByTestId("message-file-card")).toHaveLength(2);
    fireEvent.click(screen.getByTestId("attachment-open-obj-second"));
    expect(onOpenAttachment).toHaveBeenCalledWith(
      "user-1",
      expect.objectContaining({ object_id: "obj-second", original_filename: "讲义.pdf" }),
      expect.any(HTMLButtonElement)
    );
  });

  it("点击附件入口把原消息与对象身份交给预览面板", () => {
    const onOpenAttachment = vi.fn();
    render(
      <MessageList
        conversationId="conversation-1"
        messages={[
          userMessage({
            attachments: [
              attachment(),
              attachment({
                object_id: "obj-photo",
                original_filename: "板书.png",
                media_type: "image/png",
                ingestion_status: "none",
              }),
            ],
          }),
        ]}
        onOpenAttachment={onOpenAttachment}
      />
    );

    fireEvent.click(screen.getByTestId("attachment-open-obj-file"));
    expect(onOpenAttachment).toHaveBeenCalledWith(
      "user-1",
      expect.objectContaining({ object_id: "obj-file" }),
      expect.any(HTMLButtonElement)
    );

    fireEvent.click(screen.getByTestId("attachment-open-obj-photo"));
    expect(onOpenAttachment).toHaveBeenLastCalledWith(
      "user-1",
      expect.objectContaining({ object_id: "obj-photo" }),
      expect.any(HTMLButtonElement)
    );
  });
});

describe("MessageList 模块标识与建议（V2 Issue 11）", () => {
  it("用户消息的模块标识只来自该条消息的持久化值", () => {
    render(
      <MessageList
        conversationId="conversation-1"
        messages={[
          userMessage({ id: "user-1", moduleId: "paper" }),
          // 同一会话中另一条普通消息：历史标识不被输入区的选择影响
          userMessage({ id: "user-2", moduleId: null }),
        ]}
      />
    );

    const labels = screen.getAllByTestId("user-module-label");
    expect(labels).toHaveLength(1);
    expect(labels[0].textContent).toContain("论文搜索");
  });

  it("建议按钮以该轮原文与模块 ID 回调宿主页面", () => {
    const onUseModuleSuggestion = vi.fn();
    render(
      <MessageList
        conversationId="conversation-1"
        onUseModuleSuggestion={onUseModuleSuggestion}
        messages={[
          assistantMessage({
            moduleSuggestion: {
              module_id: "paper",
              label: "使用论文搜索",
              reason: "你看起来是在找论文，可以进入论文搜索模块。",
              text: "帮我找 Transformer 的论文",
              needs_disambiguation: true,
            },
          }),
        ]}
      />
    );

    const card = screen.getByTestId("module-suggestion");
    expect(card.textContent).toContain("可以进入论文搜索模块");
    expect(card.textContent).toContain("不会自动检索");

    fireEvent.click(screen.getByTestId("module-suggestion-use"));
    expect(onUseModuleSuggestion).toHaveBeenCalledWith("assistant-1", "paper");
  });

  it("论文推荐留在正文，只提供按需资料入口", () => {
    render(
      <MessageList
        conversationId="conversation-1"
        messages={[assistantMessage({ paperSearch: paperProjection })]}
      />
    );

    expect(screen.getByTestId("assistant-body")).toBeTruthy();
    expect(screen.getByTestId("paper-details-entry")).toBeTruthy();
    expect(screen.queryByTestId("paper-search-card-success")).toBeNull();
  });

  it("资料推荐结果卡渲染在该条助手消息内", () => {
    render(
      <MessageList
        conversationId="conversation-1"
        messages={[assistantMessage({ learningResources: resourcesProjection })]}
      />
    );

    expect(screen.getByTestId("resources-card-success")).toBeTruthy();
  });
});
