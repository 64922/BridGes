import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { TurnResultProjection } from "@/lib/api";
import { MessageList, type ChatMessage } from "./MessageList";

afterEach(cleanup);

describe("工单38部分交付正文", () => {
  function message(outcome: "partial" | "failed"): ChatMessage {
    const result: TurnResultProjection = {
      version: "turn-result-v1", outcome,
      outcome_label: outcome === "partial" ? "本轮先交付有效部分，其余未完成。" : "本轮未完成。",
      delivered: outcome === "partial" ? [{
        module_id: "study", label: "复盘反馈", state: "success", trust: "qualified", detail: "",
      }] : [],
    };
    return {
      id: "assistant-38", role: "assistant", status: "error",
      plainText: "回答正确。反馈已提交。",
      content: <p>回答正确。反馈已提交。</p>,
      errorText: "本节学习总结未完成，请重试总结。", turnResult: result,
    };
  }

  it("总结失败仍呈现已提交反馈正文及独立错误说明", () => {
    render(<MessageList messages={[message("partial")]} />);
    expect(screen.getByText("回答正确。反馈已提交。")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("本节学习总结未完成");
  });

  it("未交付失败不把候选正文当有效结果展示", () => {
    render(<MessageList messages={[message("failed")]} />);
    expect(screen.queryByText("回答正确。反馈已提交。")).toBeNull();
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});
