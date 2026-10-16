import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  OngoingEntryProjection,
  OngoingItemsProjection,
} from "@/lib/api";

import { OngoingItemsCard } from "./OngoingItemsCard";

const api = vi.hoisted(() => ({
  fetchOngoingItems: vi.fn(),
  updateOngoingItemState: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

function entry(
  overrides: Partial<OngoingEntryProjection> = {}
): OngoingEntryProjection {
  return {
    role: "progress",
    profile_item_id: "item-1",
    version: 1,
    text: "考研英语的阅读已经刷完一轮",
    goal_state: "active",
    validity_status: "unbounded",
    updated_at: "2026-10-15T10:00:00Z",
    source_message_ids: ["message-1"],
    ...overrides,
  } as OngoingEntryProjection;
}

function projection(
  overrides: Partial<OngoingItemsProjection> = {}
): OngoingItemsProjection {
  return {
    usage_enabled: true,
    notice: null,
    items: [
      {
        goal: entry({
          role: "goal",
          profile_item_id: "goal-1",
          text: "我在准备考研英语",
        }),
        progress: [entry()],
        pending_questions: [
          entry({
            role: "pending_question",
            profile_item_id: "item-2",
            text: "考研英语的冲刺班还没决定报不报",
          }),
        ],
        domain_references: [],
      },
    ],
    ...overrides,
  } as OngoingItemsProjection;
}

beforeEach(() => {
  api.fetchOngoingItems.mockReset();
  api.updateOngoingItemState.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("OngoingItemsCard", () => {
  it("列出目标、进展与待续问题，并标注来源数量", async () => {
    api.fetchOngoingItems.mockResolvedValue(projection());

    render(<OngoingItemsCard />);

    expect(await screen.findByText("我在准备考研英语")).toBeTruthy();
    const progress = screen.getByTestId("ongoing-progress");
    expect(progress.textContent).toContain("考研英语的阅读已经刷完一轮");
    const pending = screen.getByTestId("ongoing-pending");
    expect(pending.textContent).toContain("考研英语的冲刺班还没决定报不报");
    expect(pending.textContent).toContain("1 条对话来源");
    expect(
      screen.getByRole("button", { name: "暂停" })
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "完成" })).toBeTruthy();
  });

  it("空态与使用关闭提示如实展示，仍可管理事项", async () => {
    api.fetchOngoingItems.mockResolvedValue({
      usage_enabled: false,
      notice: "长期记忆使用已关闭：回答不会使用这些事项，管理操作仍然可用。",
      items: [],
    } as OngoingItemsProjection);

    render(<OngoingItemsCard />);

    expect(await screen.findByTestId("ongoing-empty")).toBeTruthy();
    expect(screen.getByTestId("ongoing-usage-notice").textContent).toContain(
      "管理操作仍然可用"
    );
  });

  it("暂停与继续按精确条目提交并在成功后展示结果", async () => {
    api.fetchOngoingItems.mockResolvedValue(projection());
    const pausedGoal = entry({
      role: "goal",
      profile_item_id: "goal-1",
      text: "我在准备考研英语",
      goal_state: "paused",
      version: 2,
    });
    api.updateOngoingItemState
      .mockResolvedValueOnce(
        projection({
          items: [
            {
              goal: pausedGoal,
              progress: [entry()],
              pending_questions: [],
              domain_references: [],
            },
          ],
        })
      )
      .mockResolvedValueOnce(projection());

    render(<OngoingItemsCard />);
    fireEvent.click(await screen.findByRole("button", { name: "暂停" }));

    expect(api.updateOngoingItemState).toHaveBeenCalledWith("goal-1", {
      action: "pause",
      version: 1,
    });
    expect(await screen.findByTestId("ongoing-action-notice")).toBeTruthy();
    expect(screen.getByTestId("ongoing-goal-state").textContent).toBe("已暂停");

    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    expect(api.updateOngoingItemState).toHaveBeenLastCalledWith("goal-1", {
      action: "resume",
      version: 2,
    });
    expect(await screen.findByRole("button", { name: "暂停" })).toBeTruthy();
  });

  it("完成需确认；确认后事项消失但结果有反馈", async () => {
    api.fetchOngoingItems.mockResolvedValue(projection());
    api.updateOngoingItemState.mockResolvedValue(
      projection({ items: [] })
    );
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<OngoingItemsCard />);
    fireEvent.click(await screen.findByRole("button", { name: "完成" }));

    expect(confirm).toHaveBeenCalled();
    expect(api.updateOngoingItemState).toHaveBeenCalledWith("goal-1", {
      action: "complete",
      version: 1,
    });
    expect(await screen.findByTestId("ongoing-empty")).toBeTruthy();
    expect(screen.getByTestId("ongoing-action-notice").textContent).toContain(
      "已完成"
    );
  });

  it("取消确认时不提交", async () => {
    api.fetchOngoingItems.mockResolvedValue(projection());
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<OngoingItemsCard />);
    fireEvent.click(await screen.findByRole("button", { name: "完成" }));

    expect(api.updateOngoingItemState).not.toHaveBeenCalled();
  });

  it("失败时回读真实状态并给出重试入口", async () => {
    api.fetchOngoingItems
      .mockRejectedValueOnce(new Error("网络不可用"))
      .mockResolvedValueOnce(projection());

    render(<OngoingItemsCard />);

    expect(await screen.findByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("我在准备考研英语")).toBeTruthy();
  });

  it("领域引用未接通时如实标注，解析成功后展示领域状态", async () => {
    api.fetchOngoingItems.mockResolvedValue(
      projection({
        items: [
          {
            goal: entry({
              role: "goal",
              profile_item_id: "goal-1",
              text: "我在准备考研英语",
            }),
            progress: [],
            pending_questions: [],
            domain_references: [
              {
                domain: "learning",
                reference_id: "lp-1",
                resolved: false,
                display: null,
                state_code: null,
              },
            ],
          },
        ],
      })
    );

    render(<OngoingItemsCard />);

    expect(await screen.findByTestId("ongoing-domain")).toBeTruthy();
    expect(screen.getByTestId("ongoing-domain").textContent).toContain(
      "尚未接通"
    );

    cleanup();
    api.fetchOngoingItems.mockResolvedValue(
      projection({
        items: [
          {
            goal: entry({
              role: "goal",
              profile_item_id: "goal-1",
              text: "我在准备考研英语",
            }),
            progress: [],
            pending_questions: [],
            domain_references: [
              {
                domain: "learning",
                reference_id: "lp-1",
                resolved: true,
                display: "考研英语学习项",
                state_code: "active",
              },
            ],
          },
        ],
      })
    );

    render(<OngoingItemsCard />);

    expect((await screen.findByTestId("ongoing-domain")).textContent).toContain(
      "所属领域进展：考研英语学习项"
    );
  });
});
