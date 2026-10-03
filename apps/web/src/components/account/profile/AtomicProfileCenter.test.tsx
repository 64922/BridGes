import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AtomicProfileItemEvidenceProjection,
  AtomicProfileItemProjection,
} from "@/lib/api";

import { AtomicProfileCenter } from "./AtomicProfileCenter";

const api = vi.hoisted(() => ({
  listAtomicProfileItems: vi.fn(),
  modifyAtomicProfileItem: vi.fn(),
  deleteAtomicProfileItem: vi.fn(),
  fetchAtomicProfileItemEvidence: vi.fn(),
  submitAtomicProfileItemFeedback: vi.fn(),
}));

const router = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("@/lib/api", () => api);
vi.mock("next/navigation", () => ({ useRouter: () => router }));

function item(overrides: Partial<AtomicProfileItemProjection> = {}) {
  return {
    profile_item_id: "item-1",
    text: "我在准备雅思考试",
    version: 1,
    updated_at: "2026-09-01T10:00:00Z",
    user_edited_at: null,
    source_message_ids: ["message-1"],
    write_origin: "automatic",
    ...overrides,
  } as AtomicProfileItemProjection;
}

function evidence(
  overrides: Partial<AtomicProfileItemEvidenceProjection> = {}
): AtomicProfileItemEvidenceProjection {
  return {
    profile_item_id: "item-1",
    version: 1,
    text: "我在准备雅思考试",
    write_origin: "automatic",
    user_edited_at: null,
    updated_at: "2026-09-01T10:00:00Z",
    fact_scope: "long_term",
    goal_state: "active",
    valid_from: "2026-09-01T00:00:00Z",
    valid_until: "2026-09-08T00:00:00Z",
    validity_phrase: "下周",
    validity_status: "active",
    evidence_quote: "我计划下周考雅思",
    evidence_quote_status: "recorded",
    sources: [
      {
        message_id: "message-1",
        status: "available",
        conversation_id: "conversation-9",
        created_at: "2026-09-01T10:00:00Z",
      },
    ],
    feedback: [],
    ...overrides,
  } as AtomicProfileItemEvidenceProjection;
}

beforeEach(() => {
  api.listAtomicProfileItems.mockReset();
  api.modifyAtomicProfileItem.mockReset();
  api.deleteAtomicProfileItem.mockReset();
  api.fetchAtomicProfileItemEvidence.mockReset();
  api.submitAtomicProfileItemFeedback.mockReset();
  router.push.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AtomicProfileCenter", () => {
  it("反馈完成后重新读取，忽略重展开时取得的旧反馈列表", async () => {
    api.listAtomicProfileItems.mockResolvedValue([item()]);
    let resolveOld!: (value: AtomicProfileItemEvidenceProjection) => void;
    let resolveFeedback!: (value: unknown) => void;
    api.fetchAtomicProfileItemEvidence
      .mockResolvedValueOnce(evidence())
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValue(evidence());
    api.submitAtomicProfileItemFeedback.mockImplementationOnce(
      () => new Promise((resolve) => { resolveFeedback = resolve; })
    );
    render(<AtomicProfileCenter />);
    fireEvent.click(await screen.findByText("查看依据"));
    await screen.findByText("事实记错");
    fireEvent.click(screen.getByText("事实记错"));
    fireEvent.click(screen.getByText("收起依据"));
    fireEvent.click(screen.getByText("查看依据"));
    await act(async () => { resolveFeedback({
      feedback_id: "feedback-1", profile_item_id: "item-1", kind: "fact_wrong",
      effect: "suggest_fact_correction", message: "已记录反馈", note: null,
      created_at: "2026-10-02T10:00:00Z",
    }); });
    await screen.findByTestId("atomic-feedback-notice");
    await act(async () => { resolveOld(evidence()); });
    expect(screen.getByTestId("atomic-evidence").textContent).toContain("已反馈「事实记错」");
  });

  it("重新展开后忽略较早请求返回的已删除原话", async () => {
    api.listAtomicProfileItems.mockResolvedValue([item()]);
    let resolveOld!: (value: AtomicProfileItemEvidenceProjection) => void;
    api.fetchAtomicProfileItemEvidence
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce(evidence({
        evidence_quote: null,
        evidence_quote_status: "source_unavailable",
        sources: [{ message_id: "message-1", status: "deleted" }],
      }));

    render(<AtomicProfileCenter />);
    fireEvent.click(await screen.findByText("查看依据"));
    fireEvent.click(screen.getByText("收起依据"));
    fireEvent.click(screen.getByText("查看依据"));
    await screen.findByText("来源消息已删除或不可读，保存的原话不再展示。");
    await act(async () => { resolveOld(evidence()); });

    expect(screen.queryByText("我计划下周考雅思")).toBeNull();
    expect(screen.getByTestId("atomic-evidence").textContent).toContain("已删除");
  });

  it("renders a flat list with per-row edit and delete, and no category grouping", async () => {
    api.listAtomicProfileItems.mockResolvedValue([
      item(),
      item({
        profile_item_id: "item-2",
        text: "我喜欢看天体物理科普",
        version: 3,
        source_message_ids: ["message-2", "message-3"],
      }),
    ]);

    render(<AtomicProfileCenter />);

    const rows = await screen.findAllByTestId("atomic-item");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("我在准备雅思考试");
    // 来源只说明条数，不把数量当作全部依据：可得性在展开后逐条核对。
    expect(rows[0].textContent).toContain("有 1 条对话来源");
    expect(rows[1].textContent).toContain("有 2 条对话来源");
    // 无类别：页面不出现任何旧四维标签。
    for (const label of ["学业情况", "感兴趣的知识", "兴趣爱好", "阶段目标"]) {
      expect(screen.queryByText(label)).toBeNull();
    }
    for (const row of rows) {
      expect(row.textContent).toContain("查看依据");
      expect(row.textContent).toContain("修改");
      expect(row.textContent).toContain("删除");
    }
  });

  it("expands one item's real evidence on demand and locates the source", async () => {
    api.listAtomicProfileItems.mockResolvedValue([item()]);
    api.fetchAtomicProfileItemEvidence.mockResolvedValue(evidence());

    render(<AtomicProfileCenter />);
    fireEvent.click(await screen.findByText("查看依据"));

    await waitFor(() =>
      expect(api.fetchAtomicProfileItemEvidence).toHaveBeenCalledWith("item-1")
    );
    const panel = await screen.findByTestId("atomic-evidence");
    expect(panel.textContent).toContain("我计划下周考雅思");
    expect(panel.textContent).toContain("适用范围：长期适用");
    expect(panel.textContent).toContain("原文说「下周」");
    expect(panel.textContent).toContain("当前有效");
    // 来源可访问时给出「查看原文」定位。
    fireEvent.click(screen.getByText("查看原文"));
    expect(router.push).toHaveBeenCalledWith(
      "/chat/conversation-9?message=message-1"
    );

    // 再次聚焦同一个按钮即可折叠，修改/删除始终仍在。
    fireEvent.click(screen.getByText("收起依据"));
    expect(screen.queryByTestId("atomic-evidence")).toBeNull();
    expect(screen.getByText("修改")).toBeTruthy();
    expect(screen.getByText("删除")).toBeTruthy();

    // 重新展开会重新读取：来源可能在两次展开之间被删除或不可读。
    fireEvent.click(screen.getByText("查看依据"));
    await waitFor(() =>
      expect(api.fetchAtomicProfileItemEvidence).toHaveBeenCalledTimes(2)
    );
  });

  it("reports a deleted source honestly and invalidates the saved quote", async () => {
    api.listAtomicProfileItems.mockResolvedValue([item()]);
    // 即使服务端仍带着保存时的原话，没有可读来源也不展示派生副本。
    api.fetchAtomicProfileItemEvidence.mockResolvedValue(
      evidence({
        evidence_quote_status: "source_unavailable",
        sources: [
          {
            message_id: "message-1",
            status: "deleted",
            conversation_id: null,
            created_at: null,
          },
        ],
      })
    );

    render(<AtomicProfileCenter />);
    fireEvent.click(await screen.findByText("查看依据"));

    const panel = await screen.findByTestId("atomic-evidence");
    expect(panel.textContent).toContain("已删除");
    expect(panel.textContent).toContain("保存的原话不再展示");
    expect(panel.textContent).not.toContain("我计划下周考雅思");
    expect(screen.queryByText("查看原文")).toBeNull();
  });

  it("labels an unknown validity window as unknown instead of long-term valid", async () => {
    api.listAtomicProfileItems.mockResolvedValue([item()]);
    api.fetchAtomicProfileItemEvidence.mockResolvedValue(
      evidence({
        valid_from: null,
        valid_until: null,
        validity_phrase: null,
        validity_status: "unbounded",
      })
    );

    render(<AtomicProfileCenter />);
    fireEvent.click(await screen.findByText("查看依据"));

    const panel = await screen.findByTestId("atomic-evidence");
    expect(panel.textContent).toContain("没有明确期限");
    expect(panel.textContent).not.toContain("长期有效");
    expect(panel.textContent).toContain("先与你确认");
  });

  it("records four feedback kinds without deleting still-correct facts", async () => {
    api.listAtomicProfileItems.mockResolvedValue([item()]);
    api.fetchAtomicProfileItemEvidence.mockResolvedValue(evidence());
    api.submitAtomicProfileItemFeedback.mockImplementation(
      async (_itemId: string, request: { kind: string }) => ({
        feedback_id: `feedback-${request.kind}`,
        profile_item_id: "item-1",
        kind: request.kind,
        effect: "no_fact_change",
        message: `已记录反馈：${request.kind}`,
        note: null,
        created_at: "2026-10-02T10:00:00Z",
      })
    );

    render(<AtomicProfileCenter />);
    fireEvent.click(await screen.findByText("查看依据"));
    await screen.findByTestId("atomic-evidence");

    for (const label of ["事实记错", "信息过期", "范围不适用", "回答没执行偏好"]) {
      fireEvent.click(screen.getByText(label));
      await waitFor(() =>
        expect(api.submitAtomicProfileItemFeedback).toHaveBeenCalledWith("item-1", {
          kind: expect.any(String),
        })
      );
    }
    expect(api.submitAtomicProfileItemFeedback).toHaveBeenCalledTimes(4);
    // 反馈绝不自动删除事实：列表行仍在，删除仍要用户自己确认。
    const rows = screen.getAllByTestId("atomic-item");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("我在准备雅思考试");
    expect(api.deleteAtomicProfileItem).not.toHaveBeenCalled();
    expect(await screen.findByTestId("atomic-feedback-notice")).toBeTruthy();
  });

  it("keeps evidence failures retryable and refuses to render them as real", async () => {
    api.listAtomicProfileItems.mockResolvedValue([item()]);
    api.fetchAtomicProfileItemEvidence.mockRejectedValue(
      new Error("这条信息的依据暂时无法加载，请重试。")
    );

    render(<AtomicProfileCenter />);
    fireEvent.click(await screen.findByText("查看依据"));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("依据暂时无法加载");

    api.fetchAtomicProfileItemEvidence.mockResolvedValue(evidence());
    fireEvent.click(screen.getByText("重试"));
    await waitFor(() =>
      expect(api.fetchAtomicProfileItemEvidence).toHaveBeenCalledTimes(2)
    );
  });

  it("edits inline with save and cancel, sending the read version", async () => {
    api.listAtomicProfileItems.mockResolvedValue([item()]);
    api.modifyAtomicProfileItem.mockResolvedValue(
      item({ text: "我每周三晚上跑步", version: 2, write_origin: "user" })
    );

    render(<AtomicProfileCenter />);
    fireEvent.click(await screen.findByText("修改"));

    const editor = screen.getByLabelText("长期信息内容");
    expect((editor as HTMLTextAreaElement).value).toBe("我在准备雅思考试");
    fireEvent.change(editor, { target: { value: "我每周三晚上跑步" } });

    // 取消先走一遍：不触发保存，列表回到展示态。
    fireEvent.click(screen.getByText("取消"));
    expect(api.modifyAtomicProfileItem).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("长期信息内容")).toBeNull();

    fireEvent.click(screen.getByText("修改"));
    fireEvent.change(screen.getByLabelText("长期信息内容"), {
      target: { value: "我每周三晚上跑步" },
    });
    fireEvent.click(screen.getByText("保存"));

    await waitFor(() =>
      expect(api.modifyAtomicProfileItem).toHaveBeenCalledWith("item-1", {
        text: "我每周三晚上跑步",
        version: 1,
      })
    );
    await waitFor(() =>
      expect(screen.getByTestId("atomic-item").textContent).toContain(
        "我每周三晚上跑步"
      )
    );
  });

  it("asks for confirmation before deleting and drops the row after success", async () => {
    api.listAtomicProfileItems.mockResolvedValue([item()]);
    api.deleteAtomicProfileItem.mockResolvedValue(undefined);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<AtomicProfileCenter />);
    fireEvent.click(await screen.findByText("删除"));

    expect(confirmSpy).toHaveBeenCalled();
    expect(api.deleteAtomicProfileItem).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByText("删除"));

    await waitFor(() =>
      expect(api.deleteAtomicProfileItem).toHaveBeenCalledWith("item-1", 1)
    );
    await waitFor(() => expect(screen.queryAllByTestId("atomic-item")).toHaveLength(0));
  });

  it("explains the extraction boundary in the empty state", async () => {
    api.listAtomicProfileItems.mockResolvedValue([]);

    render(<AtomicProfileCenter />);

    const empty = await screen.findByTestId("atomic-empty");
    expect(empty.textContent).toContain("不会推断人格或心理状态");
    expect(empty.textContent).toContain("能引用原话");
  });

  it("keeps the edit failure message and reloads the list on conflict", async () => {
    api.listAtomicProfileItems.mockResolvedValue([item()]);
    api.modifyAtomicProfileItem.mockRejectedValue(new Error("版本冲突，请刷新后重试。"));

    render(<AtomicProfileCenter />);
    fireEvent.click(await screen.findByText("修改"));
    fireEvent.change(screen.getByLabelText("长期信息内容"), {
      target: { value: "改过的内容" },
    });
    fireEvent.click(screen.getByText("保存"));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("版本冲突");
    await waitFor(() => expect(api.listAtomicProfileItems).toHaveBeenCalledTimes(2));
  });

  it("shows a retryable failure state instead of an empty profile", async () => {
    // 结构漂移被修复前，画像接口返回 5xx：页面必须显示「服务失败 + 重试」，
    // 不能把失败渲染成「还没有长期信息」，否则用户会以为历史信息真的不存在。
    api.listAtomicProfileItems.mockRejectedValue(
      new Error("持久化不可用，当前实例拒绝数据读写。")
    );

    render(<AtomicProfileCenter />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("持久化不可用");
    expect(screen.queryByTestId("atomic-empty")).toBeNull();
    expect(screen.queryByTestId("atomic-item")).toBeNull();

    // 重试成功后回到正常列表，不残留失败态。
    api.listAtomicProfileItems.mockResolvedValue([item()]);
    fireEvent.click(screen.getByText("重试"));

    const rows = await screen.findAllByTestId("atomic-item");
    expect(rows).toHaveLength(1);
    expect(screen.queryByTestId("atomic-empty")).toBeNull();
  });
});
