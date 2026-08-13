import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ContextNoteProjection } from "@/lib/api";

import { ContextNoteCard } from "./ContextNoteCard";

afterEach(() => {
  cleanup();
});

const baseNote: ContextNoteProjection = {
  state: "ready",
  profile_enabled: true,
  mode: "companion",
  used_at: "2026-08-10T00:00:00Z",
  profile_item_count: 2,
  material_categories: ["知识库材料"],
  note: "本轮回答参考了 2 条你已授权的用户背景信息，仅用于当前任务。",
};

describe("ContextNoteCard", () => {
  it("keeps the accessible name and visible heading on the same source", () => {
    render(
      <ContextNoteCard
        note={baseNote}
        streaming={false}
        conversationId="conversation-1"
        messageId="message-1"
      />
    );

    const card = screen.getByTestId("context-note-card");
    const heading = screen.getByText("本次上下文说明（已授权用户背景使用情况）");
    expect(card.getAttribute("aria-label")).toBe(heading.textContent);

    fireEvent.click(screen.getByRole("button"));
    expect(card.textContent).toContain("仅用于当前任务");
    expect(card.textContent).toContain("知识库材料");
  });

  it.each([
    ["ready", "已使用授权用户背景信息"],
    ["empty", "未匹配到相关授权用户背景信息"],
    ["off", "本轮未使用授权用户背景信息"],
    ["error", "暂时无法整理授权用户背景信息"],
  ] as const)("labels the %s state with its source", (state, label) => {
    render(
      <ContextNoteCard
        note={{ ...baseNote, state, note: "状态说明" }}
        streaming={false}
        conversationId="conversation-1"
        messageId="message-1"
      />
    );

    expect(screen.getByText(label)).toBeTruthy();
  });
});
