import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ProfileOverviewProjection } from "@/lib/api";

import { ProfileOverviewCard } from "./ProfileOverviewCard";
import { emitProfileDataChanged, onProfileItemLocate } from "./profile-events";

const api = vi.hoisted(() => ({
  fetchProfileOverview: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

function overview(
  overrides: Partial<ProfileOverviewProjection> = {}
): ProfileOverviewProjection {
  return {
    state: "current",
    usage_enabled: true,
    notice: null,
    statements: [
      {
        statement_id: "overview-aaaa",
        topic: "学习与考试",
        text: "我计划下周考雅思；雅思考试在十月",
        segments: [
          { profile_item_id: "item-1", version: 2, text: "我计划下周考雅思" },
          { profile_item_id: "item-2", version: 1, text: "雅思考试在十月" },
        ],
      },
      {
        statement_id: "overview-bbbb",
        topic: null,
        text: "我养了一只猫叫团子",
        segments: [
          { profile_item_id: "item-3", version: 1, text: "我养了一只猫叫团子" },
        ],
      },
    ],
    generated_at: "2026-10-02T10:00:00Z",
    ...overrides,
  } as ProfileOverviewProjection;
}

beforeEach(() => {
  api.fetchProfileOverview.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ProfileOverviewCard", () => {
  it("按主题成组展示概览陈述，段落可点击定位", async () => {
    const calls: string[] = [];
    const unsubscribe = onProfileItemLocate((itemId) => calls.push(itemId));
    api.fetchProfileOverview.mockResolvedValue(overview());

    render(<ProfileOverviewCard />);
    await screen.findByTestId("overview-statements");

    const statements = screen.getAllByTestId("overview-statement");
    expect(statements).toHaveLength(2);
    expect(statements[0].textContent).toContain("学习与考试");
    expect(statements[0].textContent).toContain(
      "我计划下周考雅思；雅思考试在十月"
    );
    // 无主题条目的陈述沿用中性标签，不被丢弃。
    expect(statements[1].textContent).toContain("其他信息");
    expect(screen.getByTestId("overview-generated-at").textContent).toContain(
      "最近整理于"
    );

    fireEvent.click(screen.getByText("雅思考试在十月"));
    expect(calls).toEqual(["item-2"]);
    expect(screen.getByTestId("overview-located")).toBeTruthy();
    unsubscribe();
  });

  it("无主题分组时也保留全部陈述", async () => {
    api.fetchProfileOverview.mockResolvedValue(
      overview({
        statements: [
          {
            statement_id: "overview-cccc",
            topic: null,
            text: "住在一楼",
            segments: [
              { profile_item_id: "item-9", version: 1, text: "住在一楼" },
            ],
          },
        ],
      })
    );

    render(<ProfileOverviewCard />);
    const statements = await screen.findAllByTestId("overview-statement");
    expect(statements).toHaveLength(1);
    expect(statements[0].textContent).toContain("其他信息");
    expect(statements[0].textContent).toContain("住在一楼");
  });

  it("使用关闭时仍展示概览，并如实说明回答不再使用", async () => {
    api.fetchProfileOverview.mockResolvedValue(
      overview({
        usage_enabled: false,
        notice: "已关闭回答使用：概览仍可查看与管理，但回答不再读取这些内容。",
      })
    );

    render(<ProfileOverviewCard />);
    const notice = await screen.findByTestId("overview-notice");
    expect(notice.textContent).toContain("回答不再读取");
    expect(screen.getByTestId("overview-statements")).toBeTruthy();
  });

  it("部分可用时给出提示并展示仍可保证的陈述", async () => {
    api.fetchProfileOverview.mockResolvedValue(
      overview({
        state: "partial",
        notice: "部分内容正在整理：以下陈述已在整理前取值，其余整理完成后更新。",
      })
    );

    render(<ProfileOverviewCard />);
    expect((await screen.findByTestId("overview-notice")).textContent).toContain(
      "部分内容正在整理"
    );
    expect(screen.getAllByTestId("overview-statement")).toHaveLength(2);
  });

  it("整理中时不回退旧内容，提供重新加载", async () => {
    api.fetchProfileOverview.mockResolvedValue(
      overview({
        state: "rebuilding",
        notice: "概览正在整理，稍后会自动更新。",
        statements: [],
        generated_at: null,
      })
    );

    render(<ProfileOverviewCard />);
    const rebuilding = await screen.findByTestId("overview-rebuilding");
    expect(rebuilding.textContent).toContain("概览正在整理");
    expect(screen.queryByTestId("overview-statements")).toBeNull();

    api.fetchProfileOverview.mockResolvedValue(overview());
    fireEvent.click(screen.getByText("重新加载"));
    await screen.findByTestId("overview-statements");
  });

  it("空画像时展示空态说明", async () => {
    api.fetchProfileOverview.mockResolvedValue(
      overview({ state: "empty", statements: [], generated_at: null })
    );

    render(<ProfileOverviewCard />);
    expect((await screen.findByTestId("overview-empty")).textContent).toContain(
      "还没有可以概括的长期信息"
    );
    expect(screen.queryByTestId("overview-statements")).toBeNull();
  });

  it("加载失败时如实报错并可重试", async () => {
    api.fetchProfileOverview.mockRejectedValueOnce(new Error("概览暂时不可用"));
    api.fetchProfileOverview.mockResolvedValue(overview());

    render(<ProfileOverviewCard />);
    expect((await screen.findByText("概览暂时不可用")).textContent).toContain(
      "概览暂时不可用"
    );

    fireEvent.click(screen.getByText("重试"));
    await screen.findByTestId("overview-statements");
  });

  it("条目或事项变化后立即失效重读概览", async () => {
    api.fetchProfileOverview.mockResolvedValue(overview());

    render(<ProfileOverviewCard />);
    await screen.findByTestId("overview-statements");
    expect(api.fetchProfileOverview).toHaveBeenCalledTimes(1);

    api.fetchProfileOverview.mockResolvedValue(
      overview({
        statements: [
          {
            statement_id: "overview-dddd",
            topic: "健康",
            text: "对花生过敏",
            segments: [
              { profile_item_id: "item-4", version: 1, text: "对花生过敏" },
            ],
          },
        ],
      })
    );
    await act(async () => {
      emitProfileDataChanged();
    });

    expect(api.fetchProfileOverview).toHaveBeenCalledTimes(2);
    await waitFor(() => {
      expect(screen.getAllByTestId("overview-statement")).toHaveLength(1);
    });
    expect(screen.getByTestId("overview-statements").textContent).toContain(
      "对花生过敏"
    );
  });
});
