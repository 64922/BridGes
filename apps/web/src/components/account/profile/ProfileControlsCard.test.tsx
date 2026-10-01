import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ProfileAccountControlsProjection } from "@/lib/api";

import { ProfileControlsCard } from "./ProfileControlsCard";

const api = vi.hoisted(() => ({
  fetchProfileControls: vi.fn(),
  updateProfileControls: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

function controls(
  overrides: Partial<ProfileAccountControlsProjection> = {}
): ProfileAccountControlsProjection {
  return {
    controls_version: "profile-controls-v1",
    recording_enabled: true,
    usage_enabled: true,
    usage_control_version: 0,
    usage_updated_at: null,
    ...overrides,
  } as ProfileAccountControlsProjection;
}

beforeEach(() => {
  api.fetchProfileControls.mockReset();
  api.updateProfileControls.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ProfileControlsCard", () => {
  it("renders two independent switches with copy that explains each meaning", async () => {
    api.fetchProfileControls.mockResolvedValue(controls());

    render(<ProfileControlsCard />);

    const recording = await screen.findByRole("switch", { name: "自动记录" });
    const usage = screen.getByRole("switch", { name: "回答使用长期信息" });
    expect(recording.getAttribute("aria-checked")).toBe("true");
    expect(usage.getAttribute("aria-checked")).toBe("true");
    // 文案分开解释：关闭记录不阻止主动管理；关闭使用不删除信息。
    expect(screen.getByText(/仍然可以手动记住、修改、忘掉和删除/)).toBeTruthy();
    expect(screen.getByText(/信息全部保留，随时可以重新开启/)).toBeTruthy();
  });

  it("toggles only the targeted control and applies the returned state", async () => {
    api.fetchProfileControls.mockResolvedValue(controls());
    api.updateProfileControls.mockResolvedValue(
      controls({ usage_enabled: false, usage_control_version: 1 })
    );

    render(<ProfileControlsCard />);
    fireEvent.click(await screen.findByRole("switch", { name: "回答使用长期信息" }));

    await waitFor(() =>
      expect(api.updateProfileControls).toHaveBeenCalledWith({ usage_enabled: false })
    );
    await waitFor(() => {
      const recording = screen.getByRole("switch", { name: "自动记录" });
      expect(recording.getAttribute("aria-checked")).toBe("true");
      const usage = screen.getByRole("switch", { name: "回答使用长期信息" });
      expect(usage.getAttribute("aria-checked")).toBe("false");
    });
  });

  it("reports failures honestly and reloads the real state instead of faking success", async () => {
    api.fetchProfileControls.mockResolvedValue(controls());
    api.updateProfileControls.mockRejectedValue(new Error("设置未能保存"));

    render(<ProfileControlsCard />);
    fireEvent.click(await screen.findByRole("switch", { name: "自动记录" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("设置未能保存");
    // 失败后重拉真实状态：开关不被渲染成已切换。
    await waitFor(() => expect(api.fetchProfileControls).toHaveBeenCalledTimes(2));
    const recording = screen.getByRole("switch", { name: "自动记录" });
    expect(recording.getAttribute("aria-checked")).toBe("true");
  });
});
