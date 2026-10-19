import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { tiebaResearch } from "@/lib/chat-modules.test-support";
import type { ChatMessageView } from "@/lib/chat-message-view";

import { AssistantActions } from "./AssistantActions";

afterEach(cleanup);

function message(action: "retry_incomplete_stage" | "retry_run"): ChatMessageView {
  return {
    id: "assistant-1",
    role: "assistant",
    plainText: "",
    status: "done",
    tiebaResearch: tiebaResearch({
      recovery: {
        action,
        automatic: false,
        reuses_saved_evidence: true,
        failed_stage: action === "retry_incomplete_stage" ? "tieba.synthesize" : "tieba.read",
        scope_note: "服务端恢复范围。",
      },
    }),
  };
}

describe("AssistantActions tieba recovery labels", () => {
  it("局部重试使用服务端动作对应的按钮名", () => {
    const { unmount } = render(
      <AssistantActions message={message("retry_incomplete_stage")} onRetry={() => {}} />
    );

    expect(screen.getByRole("button", { name: "重试未完成阶段" })).toBeTruthy();
    unmount();

    render(<AssistantActions message={message("retry_run")} onRetry={() => {}} />);
    expect(screen.getByRole("button", { name: "重新运行" })).toBeTruthy();
  });
});
