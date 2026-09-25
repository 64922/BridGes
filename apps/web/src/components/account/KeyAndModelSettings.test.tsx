import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchCredentialSettings: vi.fn(),
  fetchModelSettings: vi.fn(),
  replaceModelConfiguration: vi.fn(),
  replaceQwenCredential: vi.fn(),
  replaceTavilyCredential: vi.fn(),
  replaceAmapWebServiceCredential: vi.fn(),
  replaceAmapBrowserMapCredential: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

import { KeyAndModelSettings } from "./KeyAndModelSettings";

const configuredStatus = {
  configured: true,
  last_validated_at: "2026-09-24T00:00:00Z",
  error: null,
};

const modelSettings = {
  model_id: "qwen-approved-chat",
  source: "factory",
  capabilities: {
    text: true,
    image: true,
    tool_calling: true,
    structured_output: true,
  },
  context_window: 1_000_000,
  max_input_tokens: 999_000,
  metadata_version: "bailian-model-list-v1",
  revision: 0,
  validated_at: null,
  credential_configured: true,
  last_validation: null,
  error: null,
};

function formOf(input: HTMLElement): HTMLFormElement {
  const form = input.closest("form");
  if (!form) throw new Error("输入框不在表单内");
  return form;
}

afterEach(() => {
  cleanup();
});

describe("KeyAndModelSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.fetchCredentialSettings.mockResolvedValue({
      qwen: configuredStatus,
      tavily: configuredStatus,
      amap: {
        web_service: configuredStatus,
        browser_map: configuredStatus,
      },
    });
    api.fetchModelSettings.mockResolvedValue(modelSettings);
    api.replaceTavilyCredential.mockResolvedValue(configuredStatus);
  });

  it("never prefills stored secrets and clears a successful replacement", async () => {
    let resolveSave: ((value: typeof configuredStatus) => void) | undefined;
    api.replaceTavilyCredential.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSave = resolve;
      })
    );
    render(<KeyAndModelSettings />);

    const tavilyInput = await screen.findByLabelText(/Tavily API Key/);
    const qwenInput = screen.getByLabelText(/Qwen API Key/);
    const routeKeyInput = screen.getByLabelText(/高德 Web 服务 Key/);
    const jsKeyInput = screen.getByLabelText(/高德 JS API Key（Web 平台）/);
    const securityCodeInput = screen.getByLabelText(/高德 JS API 安全码/);
    for (const input of [tavilyInput, qwenInput, routeKeyInput, jsKeyInput, securityCodeInput]) {
      expect(input).toHaveProperty("value", "");
      expect(input).toHaveProperty("type", "password");
    }

    fireEvent.change(tavilyInput, { target: { value: "new-tavily-candidate" } });
    fireEvent.submit(formOf(tavilyInput));

    await waitFor(() =>
      expect(api.replaceTavilyCredential).toHaveBeenCalledWith("new-tavily-candidate")
    );
    expect(await screen.findByText("正在验证并保存…")).toBeTruthy();
    resolveSave?.(configuredStatus);
    await waitFor(() => expect(tavilyInput).toHaveProperty("value", ""));
    expect(await screen.findByText("Tavily 凭据已验证并保存。")).toBeTruthy();
  });

  it("clears the Qwen input after a verified replacement and reloads the model card", async () => {
    api.replaceQwenCredential.mockResolvedValue(configuredStatus);
    render(<KeyAndModelSettings />);

    const qwenInput = await screen.findByLabelText(/Qwen API Key/);
    fireEvent.change(qwenInput, { target: { value: "sk-new-qwen-candidate" } });
    fireEvent.submit(formOf(qwenInput));

    await waitFor(() =>
      expect(api.replaceQwenCredential).toHaveBeenCalledWith("sk-new-qwen-candidate")
    );
    await waitFor(() => expect(qwenInput).toHaveProperty("value", ""));
    expect(await screen.findByText("Qwen 凭据已验证并保存。")).toBeTruthy();
    // 密钥可用性变化后主模型卡片重新读取配置。
    await waitFor(() => expect(api.fetchModelSettings.mock.calls.length).toBeGreaterThan(1));
  });

  it("keeps the old credential and explains the order when the candidate key is rejected", async () => {
    api.replaceQwenCredential.mockRejectedValue(
      new Error(
        "百炼拒绝了该 Qwen 密钥（鉴权失败）；请先在「Qwen 凭据」中更换密钥，再验证主模型 ID。"
      )
    );
    api.fetchCredentialSettings.mockResolvedValue({
      qwen: { ...configuredStatus, configured: false, last_validated_at: null },
      tavily: configuredStatus,
      amap: { web_service: configuredStatus, browser_map: configuredStatus },
    });
    render(<KeyAndModelSettings />);

    const qwenInput = await screen.findByLabelText(/Qwen API Key/);
    // 未配置时字段附近说明操作顺序。
    expect(await screen.findByText(/请先在本页「Qwen 凭据」中输入密钥并验证保存/)).toBeTruthy();
    fireEvent.change(qwenInput, { target: { value: "sk-rejected" } });
    fireEvent.submit(formOf(qwenInput));

    expect(await screen.findByText(/百炼拒绝了该 Qwen 密钥/)).toBeTruthy();
    // 失败后不写入成功状态，状态仍为未配置，输入框保留候选值供修正。
    expect(screen.queryByText("Qwen 凭据已验证并保存。")).toBeNull();
    expect(screen.getAllByTestId("credential-status")[0].textContent).toBe("未配置");
    expect(qwenInput).toHaveProperty("value", "sk-rejected");
  });
});
