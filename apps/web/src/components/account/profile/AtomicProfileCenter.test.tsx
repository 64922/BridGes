import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AtomicProfileItemProjection } from "@/lib/api";

import { AtomicProfileCenter } from "./AtomicProfileCenter";

const api = vi.hoisted(() => ({
  listAtomicProfileItems: vi.fn(),
  modifyAtomicProfileItem: vi.fn(),
  deleteAtomicProfileItem: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

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

beforeEach(() => {
  api.listAtomicProfileItems.mockReset();
  api.modifyAtomicProfileItem.mockReset();
  api.deleteAtomicProfileItem.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AtomicProfileCenter", () => {
  it("renders a flat list with per-row edit and delete, and no category grouping", async () => {
    api.listAtomicProfileItems.mockResolvedValue([
      item(),
      item({ profile_item_id: "item-2", text: "我喜欢看天体物理科普", version: 3 }),
    ]);

    render(<AtomicProfileCenter />);

    const rows = await screen.findAllByTestId("atomic-item");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("我在准备雅思考试");
    // 无类别：页面不出现任何旧四维标签。
    for (const label of ["学业情况", "感兴趣的知识", "兴趣爱好", "阶段目标"]) {
      expect(screen.queryByText(label)).toBeNull();
    }
    for (const row of rows) {
      expect(row.textContent).toContain("修改");
      expect(row.textContent).toContain("删除");
    }
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
});
