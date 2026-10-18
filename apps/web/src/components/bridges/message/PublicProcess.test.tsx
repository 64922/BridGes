import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ContextNoteProjection, ImageTaskProjection } from "@/lib/api";
import type { ChatMessageView } from "@/lib/chat-message-view";

import { PublicProcess, publicProcessLabel } from "./PublicProcess";

afterEach(cleanup);

const contextNote: ContextNoteProjection = {
  state: "ready",
  profile_enabled: true,
  mode: "companion",
  used_at: "2026-10-17T08:00:00Z",
  profile_item_count: 1,
  material_categories: ["知识库材料"],
  note: "本轮使用了 1 条已授权背景信息，仅用于当前任务。",
};

function assistant(overrides: Partial<ChatMessageView> = {}): ChatMessageView {
  return {
    id: "assistant-1",
    role: "assistant",
    plainText: "回答正文",
    ...overrides,
  };
}

function expand() {
  fireEvent.click(screen.getByTestId("public-process-toggle"));
}

describe("单行公开过程（工单 06）", () => {
  it("默认收起；鼠标展开后显示公开步骤、来源与上下文说明", () => {
    render(
      <PublicProcess
        conversationId="conversation-1"
        message={assistant({
          thinking: {
            steps: ["理解问题", "检索资料"],
            evidence: ["采用 2 篇论文"],
            tools: [
              "arXiv 检索完成",
              "已参考 1 条你已授权的用户背景信息（仅限当前任务）",
            ],
            quality: ["引用已核验"],
            seconds: 4,
          },
          contextNote,
        })}
      />
    );

    expect(screen.getByTestId("public-process-label").textContent).toBe(
      "回答已生成 · 用时 4 秒"
    );
    expect(screen.getByTestId("public-process-toggle").getAttribute("aria-expanded")).toBe(
      "false"
    );
    expect(screen.queryByTestId("public-process-details")).toBeNull();

    expand();

    const details = screen.getByTestId("public-process-details");
    expect(details.textContent).toContain("公开步骤");
    expect(details.textContent).toContain("理解问题");
    expect(details.textContent).toContain("来源说明");
    expect(details.textContent).toContain("采用 2 篇论文");
    expect(details.textContent).toContain("工具进度");
    expect(details.textContent).not.toContain("已参考 1 条");
    expect(details.textContent).toContain("质量检查");
    expect(details.textContent).toContain("本次上下文说明");
    expect(details.textContent).toContain("仅用于当前任务");
    expect(details.textContent).toContain("知识库材料");
    expect(screen.getByRole("button", { name: "这次回答不合适" })).toBeTruthy();

    expand();
    expect(screen.queryByTestId("public-process-details")).toBeNull();
  });

  it("流式增量不自动展开，也不重置用户选择", () => {
    const { rerender } = render(
      <PublicProcess
        message={assistant({
          status: "streaming",
          node: {
            kind: "node",
            message_id: "assistant-1",
            node: "validate_turn",
            status: "started",
          },
        })}
      />
    );

    expect(screen.getByTestId("public-process-label").textContent).toBe("校验回合…");
    expect(screen.getByTestId("public-process-toggle").getAttribute("aria-expanded")).toBe(
      "false"
    );

    rerender(
      <PublicProcess
        message={assistant({
          status: "streaming",
          stage: {
            kind: "stage",
            message_id: "assistant-1",
            stage: "public_search",
            status: "active",
          },
        })}
      />
    );
    expect(screen.getByTestId("public-process-label").textContent).toBe("搜索公开来源…");
    expect(screen.queryByTestId("public-process-details")).toBeNull();

    expand();
    expect(screen.getByTestId("public-process-details")).toBeTruthy();

    rerender(
      <PublicProcess
        message={assistant({
          status: "streaming",
          thinking: {
            steps: ["收到增量"],
            evidence: [],
            tools: [],
            quality: [],
            seconds: null,
          },
        })}
      />
    );
    expect(screen.getByTestId("public-process-toggle").getAttribute("aria-expanded")).toBe(
      "true"
    );
    expect(screen.getByTestId("public-process-details").textContent).toContain("收到增量");

    rerender(
      <PublicProcess
        message={assistant({
          thinking: {
            steps: ["完成步骤"],
            evidence: [],
            tools: [],
            quality: ["回答已完整生成并保存"],
            seconds: 3,
          },
        })}
      />
    );
    expect(screen.getByTestId("public-process-label").textContent).toBe(
      "回答已生成 · 用时 3 秒"
    );
    expect(screen.getByTestId("public-process-toggle").getAttribute("aria-expanded")).toBe(
      "true"
    );
  });

  it.each([
    ["ready", "已使用授权用户背景信息"],
    ["empty", "未匹配到相关授权用户背景信息"],
    ["off", "本轮未使用授权用户背景信息"],
    ["error", "暂时无法整理授权用户背景信息"],
  ] as const)("上下文状态 %s 有明确文案", (state, label) => {
    render(
      <PublicProcess
        conversationId="conversation-1"
        message={assistant({ contextNote: { ...contextNote, state, note: "状态说明" } })}
      />
    );
    expand();
    const details = screen.getByTestId("public-process-details");
    expect(details.textContent).toContain(label);
    expect(details.textContent).toContain("状态说明");
    expect(screen.queryByText(/加载中/)).toBeNull();
    if (state === "ready") {
      expect(screen.getByRole("button", { name: "这次回答不合适" })).toBeTruthy();
    } else {
      expect(screen.queryByRole("button", { name: "这次回答不合适" })).toBeNull();
    }
  });

  it("生成中尚无披露时给出完成后的说明，历史无披露直接说明", () => {
    const { rerender } = render(
      <PublicProcess message={assistant({ status: "streaming" })} />
    );
    expand();
    expect(screen.getByTestId("public-process-details").textContent).toContain(
      "回答完成后可查看本轮上下文说明"
    );
    expect(
      screen.getByText("回答完成后可查看本轮上下文说明").style.color
    ).toBe("var(--color-accent-primary)");

    rerender(<PublicProcess message={assistant({ contextNote: null })} />);
    expect(screen.getByTestId("public-process-details").textContent).toContain(
      "本轮没有上下文披露"
    );
    expect(screen.queryByText(/加载中/)).toBeNull();
  });

  it("只渲染公开披露字段，不展示额外画像原文或内部推理", () => {
    const unsafe = {
      ...contextNote,
      profile_items: [{ content: "敏感画像原文" }],
      raw_reasoning: "内部推理原文",
    } as unknown as ContextNoteProjection;
    const message = assistant({
      thinking: {
        steps: ["公开步骤"],
        evidence: [],
        tools: [],
        quality: [],
        seconds: null,
        raw_chain_of_thought: "隐藏思维链",
      } as ChatMessageView["thinking"],
      contextNote: unsafe,
    });

    render(<PublicProcess conversationId="conversation-1" message={message} />);
    expand();
    const details = screen.getByTestId("public-process-details");
    expect(details.textContent).toContain("公开步骤");
    expect(details.textContent).not.toContain("敏感画像原文");
    expect(details.textContent).not.toContain("内部推理原文");
    expect(details.textContent).not.toContain("隐藏思维链");
  });

  it("缺耗时、失败与停止使用诚实的通用状态", () => {
    expect(publicProcessLabel(assistant())).toBe("回答已生成");
    expect(publicProcessLabel(assistant({ status: "error" }))).toBe("本轮生成失败");
    expect(publicProcessLabel(assistant({ status: "stopped" }))).toBe("本轮已停止");
    expect(
      publicProcessLabel(
        assistant({
          status: "streaming",
          stage: {
            kind: "stage",
            message_id: "assistant-1",
            stage: "unknown_stage",
            status: "active",
          },
        })
      )
    ).toBe("正在生成回答…");
    expect(
      publicProcessLabel(
        assistant({
          status: "streaming",
          stage: {
            kind: "stage",
            message_id: "assistant-1",
            stage: "model_generation",
            status: "done",
          },
        })
      )
    ).toBe("正在生成回答…");
  });

  it("论文模块的检索进度不再固定声称只查 arXiv", () => {
    expect(
      publicProcessLabel(
        assistant({
          status: "streaming",
          node: {
            kind: "node",
            message_id: "assistant-1",
            node: "paper.search",
            status: "started",
          },
        })
      )
    ).toBe("检索学术来源…");
  });

  it("媒体任务的真实状态进入统一过程入口", () => {
    const image: ImageTaskProjection = {
      task_id: "image-1",
      attempt_number: 1,
      kind: "generate",
      prompt: "一座桥的素描",
      status: "queued",
      error_code: null,
      error_message: null,
      retryable: false,
      asset_id: null,
      result_version_id: null,
      deleted: false,
      synthetic: true,
      created_at: "2026-10-18T00:00:00Z",
      updated_at: "2026-10-18T00:00:00Z",
    };
    render(
      <PublicProcess
        conversationId="conversation-1"
        message={assistant({ image })}
      />
    );

    expand();
    const media = screen.getByTestId("public-process-media");
    expect(media.textContent).toContain("图片生成 · 排队中");
    expect(media.textContent).toContain("一座桥的素描");
    expect(media.textContent).toContain("后台独立进行");
    expect(screen.queryByRole("button", { name: /重试/ })).toBeNull();
  });
});
