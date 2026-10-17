import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RetrievalRoundProjection } from "@/lib/api";

import { MessageList } from "./MessageList";

const retrieval: RetrievalRoundProjection = {
  round_id: "round-1",
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
      page_number: 2,
      section_title: "第一章",
      snippet: "引用片段",
      rank: 1,
    },
  ],
  note: null,
  created_at: "2026-10-17T08:00:00Z",
};

afterEach(cleanup);

describe("来源入口", () => {
  it("点击真实来源把消息身份、引用与触发按钮交给资料面板", () => {
    const onOpenCitation = vi.fn();
    render(
      <MessageList
        conversationId="conversation-1"
        messages={[
          {
            id: "assistant-1",
            role: "assistant",
            plainText: "结论见 [1]。",
            retrieval,
          },
        ]}
        onOpenCitation={onOpenCitation}
      />
    );

    const trigger = screen.getByTestId("citation-item-1");
    fireEvent.click(trigger);
    expect(onOpenCitation).toHaveBeenCalledWith(
      "assistant-1",
      retrieval.citations?.[0],
      trigger
    );
  });

  it("正文中的文字编号保持纯文本，不会变成可打开引用", () => {
    render(
      <MessageList
        conversationId="conversation-1"
        messages={[
          {
            id: "assistant-1",
            role: "assistant",
            plainText: "结论见 [1]。",
            retrieval,
          },
        ]}
      />
    );

    expect(screen.getByText("结论见 [1]。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /\[1\]/ })).toBeNull();
  });
});
