import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  LearningEvidenceReference,
  LearningGoalReference,
  LearningReferencesProjection,
} from "@/lib/api";

import { LearningReferencesCard } from "./LearningReferencesCard";

const api = vi.hoisted(() => ({
  fetchLearningReferences: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

function goal(
  overrides: Partial<LearningGoalReference> = {}
): LearningGoalReference {
  return {
    goal_id: "goal-1",
    statement: "系统学习概率论",
    status: "active",
    version: 1,
    current_node_title: "条件概率",
    route_version: 1,
    updated_at: "2026-10-15T10:00:00Z",
    ...overrides,
  } as LearningGoalReference;
}

function evidence(
  overrides: Partial<LearningEvidenceReference> = {}
): LearningEvidenceReference {
  return {
    record_id: "record-1",
    record_version: 2,
    subject: "概率论",
    title: "互斥与独立",
    verdict: "independent_completion",
    state: "active",
    verified: true,
    pending_confirmation: false,
    source_available: true,
    uncertainty: "",
    user_note: "",
    updated_at: "2026-10-15T10:00:00Z",
    ...overrides,
  } as LearningEvidenceReference;
}

function projection(
  overrides: Partial<LearningReferencesProjection> = {}
): LearningReferencesProjection {
  return {
    owner: "study",
    contract_version: "learning-references-v1",
    goals: [goal()],
    evidences: [evidence()],
    ...overrides,
  } as LearningReferencesProjection;
}

beforeEach(() => {
  api.fetchLearningReferences.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("LearningReferencesCard", () => {
  it("列出学习目标与已核验判断，并注明依据实际作答", async () => {
    api.fetchLearningReferences.mockResolvedValue(projection());

    render(<LearningReferencesCard />);

    expect(await screen.findByText("系统学习概率论")).toBeTruthy();
    expect(screen.getByTestId("learning-goals").textContent).toContain(
      "当前知识点：条件概率"
    );
    expect(screen.getByTestId("learning-goals").textContent).toContain("进行中");
    const badge = screen.getByTestId("learning-evidence-badge");
    expect(badge.textContent).toBe("已核验");
    expect(screen.getByTestId("learning-evidences").textContent).toContain(
      "依据实际作答的核验结论：独立完成"
    );
  });

  it("纠正、待验证与来源不可读都不冒充已核验", async () => {
    api.fetchLearningReferences.mockResolvedValue(
      projection({
        goals: [],
        evidences: [
          evidence({
            state: "corrected",
            verified: false,
            user_note: "我当时只是看懂了，还没真正做对。",
          }),
          evidence({
            record_id: "record-2",
            verdict: "hinted_completion",
            verified: false,
            pending_confirmation: true,
            uncertainty: "只在单题上验证过",
          }),
          evidence({
            record_id: "record-3",
            verified: false,
            source_available: false,
            verdict: "unverified",
          }),
        ],
      })
    );

    render(<LearningReferencesCard />);

    const badges = await screen.findAllByTestId("learning-evidence-badge");
    expect(badges.map((item) => item.textContent)).toEqual([
      "已按你的说明纠正",
      "未核验",
      "未核验",
    ]);
    const list = screen.getByTestId("learning-evidences");
    expect(list.textContent).toContain("你的说明：我当时只是看懂了");
    expect(list.textContent).toContain("在提示下完成");
    expect(list.textContent).toContain("待进一步验证");
    expect(list.textContent).toContain("未能确认：只在单题上验证过");
    expect(list.textContent).toContain("作答来源已不可读");
  });

  it("学习模块删除后再次读取即不再展示", async () => {
    api.fetchLearningReferences.mockResolvedValueOnce(projection());

    render(<LearningReferencesCard />);
    expect(await screen.findByTestId("learning-evidences")).toBeTruthy();

    cleanup();
    api.fetchLearningReferences.mockResolvedValueOnce(
      projection({ goals: [], evidences: [] })
    );
    render(<LearningReferencesCard />);

    expect(
      await screen.findByTestId("learning-references-empty")
    ).toBeTruthy();
    expect(screen.queryByTestId("learning-evidences")).toBeNull();
  });

  it("学习模块未接通时如实说明，不显示为空学情", async () => {
    api.fetchLearningReferences.mockRejectedValue(
      Object.assign(new Error("学习领域尚未接通"), { status: 503 })
    );

    render(<LearningReferencesCard />);

    expect(
      await screen.findByTestId("learning-references-unavailable")
    ).toBeTruthy();
    expect(screen.queryByTestId("learning-references-empty")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("加载失败给出重试，重试成功恢复内容", async () => {
    api.fetchLearningReferences
      .mockRejectedValueOnce(new Error("网络不可用"))
      .mockResolvedValueOnce(projection());

    render(<LearningReferencesCard />);

    expect(await screen.findByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("系统学习概率论")).toBeTruthy();
  });
});
