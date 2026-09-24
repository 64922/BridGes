import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchCredentialSettings: vi.fn(),
  replaceTavilyCredential: vi.fn(),
  replaceAmapWebServiceCredential: vi.fn(),
  replaceAmapBrowserMapCredential: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

import { CredentialSettings } from "./CredentialSettings";

const configuredStatus = {
  configured: true,
  last_validated_at: "2026-09-24T00:00:00Z",
  error: null,
};

describe("CredentialSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.fetchCredentialSettings.mockResolvedValue({
      tavily: configuredStatus,
      amap: {
        web_service: configuredStatus,
        browser_map: configuredStatus,
      },
    });
    api.replaceTavilyCredential.mockResolvedValue(configuredStatus);
  });

  it("never prefills stored secrets and clears a successful replacement", async () => {
    let resolveSave: ((value: typeof configuredStatus) => void) | undefined;
    api.replaceTavilyCredential.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSave = resolve;
      })
    );
    render(<CredentialSettings />);

    const tavilyInput = await screen.findByLabelText(/Tavily API Key/);
    const routeKeyInput = screen.getByLabelText(/高德 Web 服务 Key/);
    const jsKeyInput = screen.getByLabelText(/高德 JS API Key（Web 平台）/);
    const securityCodeInput = screen.getByLabelText(/高德 JS API 安全码/);
    for (const input of [tavilyInput, routeKeyInput, jsKeyInput, securityCodeInput]) {
      expect(input).toHaveProperty("value", "");
    }

    fireEvent.change(tavilyInput, { target: { value: "new-tavily-candidate" } });
    fireEvent.click(screen.getAllByRole("button", { name: "验证并保存" })[0]);

    await waitFor(() =>
      expect(api.replaceTavilyCredential).toHaveBeenCalledWith("new-tavily-candidate")
    );
    expect(await screen.findByText("正在验证并保存…")).toBeTruthy();
    resolveSave?.(configuredStatus);
    await waitFor(() => expect(tavilyInput).toHaveProperty("value", ""));
    expect(await screen.findByText("Tavily 凭据已验证并保存。")).toBeTruthy();
  });
});
