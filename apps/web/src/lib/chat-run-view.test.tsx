import { describe, expect, it } from "vitest";

import type { ChatMessageView } from "@/lib/chat-message-view";

import type { ActiveRun, PendingUserMessage } from "./chat-run";
import { composeConversationMessages } from "./chat-run-view";

function userMessage(id: string, text: string): ChatMessageView {
  return {
    id,
    role: "user",
    plainText: text,
    body: <p>{text}</p>,
  };
}

function assistantMessage(
  id: string,
  text: string,
  status?: ChatMessageView["status"]
): ChatMessageView {
  return {
    id,
    role: "assistant",
    plainText: text,
    body: <p>{text}</p>,
    status,
  };
}

function run(overrides: Partial<ActiveRun> = {}): ActiveRun {
  return {
    messageId: "a-2",
    content: "",
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

const pending: PendingUserMessage = {
  id: "u-2",
  text: "新问题",
  attachments: [],
};

describe("会话线程合成", () => {
  it("发送中的运行接管同 id 历史项并保留唯一用户气泡与助手正文", () => {
    const base = [
      userMessage("u-1", "旧问题"),
      assistantMessage("a-1", "旧回答", "done"),
      userMessage("u-2", "新问题"),
      assistantMessage("a-2", ""),
    ];
    const messages = composeConversationMessages(
      base,
      run({ content: "流式正文" }),
      pending
    );
    expect(messages.map((message) => message.id)).toEqual([
      "u-1",
      "a-1",
      "u-2",
      "a-2",
    ]);
    expect(messages.filter((message) => message.id === "a-2")).toHaveLength(1);
    expect(messages[messages.length - 1].plainText).toBe("流式正文");
    expect(messages[messages.length - 1].status).toBe("streaming");
  });

  it("恢复中的运行接管历史流式项，不重复答案", () => {
    const base = [
      userMessage("u-1", "问题"),
      assistantMessage("a-1", "已有正文", "streaming"),
    ];
    const messages = composeConversationMessages(
      base,
      run({ messageId: "a-1", content: "已有正文", kind: "resume" }),
      null
    );
    expect(messages).toHaveLength(2);
    expect(messages.filter((message) => message.id === "a-1")).toHaveLength(1);
    expect(messages[1].plainText).toBe("已有正文");
  });

  it("错误运行保留待发送用户气泡与错误状态", () => {
    const base = [userMessage("u-1", "旧问题"), assistantMessage("a-1", "旧回答", "done")];
    const messages = composeConversationMessages(
      base,
      run({ status: "error", content: "部分正文", errorText: "生成中断。" }),
      pending
    );
    expect(messages.map((message) => message.id)).toEqual([
      "u-1",
      "a-1",
      "u-2",
      "a-2",
    ]);
    expect(messages[3].status).toBe("error");
    expect(messages[3].errorText).toBe("生成中断。");
    expect(messages[3].plainText).toBe("部分正文");
  });

  it("终态交接到权威历史前保持正文且不再显示流式光标", () => {
    const base = [userMessage("u-1", "问题")];
    const messages = composeConversationMessages(
      base,
      run({ status: "done", content: "完整回答" }),
      pending
    );
    const assistant = messages[messages.length - 1];
    expect(assistant.plainText).toBe("完整回答");
    expect(assistant.status).toBeUndefined();
    expect(assistant.body).toBeUndefined();
  });
});
