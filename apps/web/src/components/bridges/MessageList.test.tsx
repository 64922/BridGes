import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MessageList, type ChatMessage } from "./MessageList";
import type { LearningResourcesProjection, PaperSearchProjection } from "@/lib/api";

afterEach(cleanup);

const paperProjection: PaperSearchProjection = {
  status: "success",
  original_phrase: "Transformer",
  normalized_term: "Transformer",
  expansions: [],
  confidence: 0.9,
  context_label: "机器学习中的 Transformer 结构",
  queries: [],
  final_query: "transformer",
  papers: [],
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
  evidence_notes: [],
  pending: null,
  searched_at: null,
  error_code: null,
  error_message: null,
  retryable: false,
};

function userMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "user-1",
    role: "user",
    plainText: "帮我找 Transformer 的论文",
    content: <p>帮我找 Transformer 的论文</p>,
    ...overrides,
  };
}

function assistantMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "assistant-1",
    role: "assistant",
    plainText: "回答",
    content: <p>回答</p>,
    ...overrides,
  };
}

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

  it("论文模块结果卡渲染在该条助手消息内", () => {
    render(
      <MessageList
        conversationId="conversation-1"
        messages={[assistantMessage({ paperSearch: paperProjection })]}
      />
    );

    expect(screen.getByTestId("paper-search-card-success")).toBeTruthy();
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
