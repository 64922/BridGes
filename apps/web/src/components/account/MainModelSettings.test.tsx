import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchModelSettings: vi.fn(),
  replaceModelConfiguration: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

import { MainModelSettings } from "./MainModelSettings";

afterEach(() => {
  cleanup();
});

function formOf(input: HTMLElement): HTMLFormElement {
  const form = input.closest("form");
  if (!form) throw new Error("输入框不在表单内");
  return form;
}

const activeModel = {
  model_id: "qwen-approved-chat",
  source: "settings",
  capabilities: {
    text: true,
    image: true,
    tool_calling: true,
    structured_output: true,
  },
  context_window: 1_000_000,
  max_input_tokens: 999_000,
  metadata_version: "bailian-model-list-v1",
  revision: 3,
  validated_at: "2026-09-25T02:00:00Z",
  credential_configured: true,
  last_validation: null,
  error: null,
};

describe("MainModelSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.fetchModelSettings.mockResolvedValue(activeModel);
  });

  it("shows the effective model ID, capabilities and context length", async () => {
    render(<MainModelSettings />);

    expect((await screen.findByTestId("active-model-id")).textContent).toBe("qwen-approved-chat");
    expect(screen.getByText("1,000,000 tokens")).toBeTruthy();
    expect(screen.getByText("999,000 tokens")).toBeTruthy();
    for (const key of ["text", "image", "tool_calling", "structured_output"]) {
      expect(screen.getByTestId(`capability-${key}`).textContent).toContain("支持");
    }
    expect(screen.getByTestId("model-source").textContent).toBe("已验证配置");
    // 没有预设模型列表：只有手填输入框。
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("explains the operation order near the field and repeats it on failure", async () => {
    api.fetchModelSettings.mockResolvedValue({
      ...activeModel,
      source: "factory",
      credential_configured: false,
    });
    api.replaceModelConfiguration.mockRejectedValue(
      new Error(
        "当前没有可用的 Qwen 密钥：请先在本页「Qwen 凭据」中输入密钥并验证保存，再填写并验证主模型 ID。"
      )
    );
    render(<MainModelSettings />);

    const input = await screen.findByLabelText(/Qwen 主模型 ID/);
    expect(screen.getByTestId("model-credential-guidance").textContent).toContain(
      "请先在本页「Qwen 凭据」中输入密钥并验证保存"
    );

    fireEvent.change(input, { target: { value: "qwen-candidate" } });
    fireEvent.submit(formOf(input));

    expect(await screen.findByText(/当前没有可用的 Qwen 密钥/)).toBeTruthy();
    expect(input).toHaveProperty("value", "qwen-candidate");
  });

  it("keeps the old configuration and lists each capability failure reason", async () => {
    const failedReport = {
      model_id: "qwen-candidate",
      passed: false,
      capabilities: {
        text: true,
        image: false,
        tool_calling: true,
        structured_output: false,
      },
      context_window: 131_072,
      max_input_tokens: null,
      checks: [
        {
          capability: "image",
          label: "图片",
          source: "metadata",
          ok: false,
          message: "百炼元数据未声明图片能力。",
        },
        {
          capability: "structured_output",
          label: "结构化输出",
          source: "probe",
          ok: false,
          message: "结构化输出探测失败：服务端未按 JSON 对象返回。",
        },
      ],
      message: "真实能力探测未通过：结构化输出探测失败：服务端未按 JSON 对象返回。",
    };
    api.replaceModelConfiguration.mockRejectedValue(new Error(failedReport.message));
    api.fetchModelSettings
      .mockResolvedValueOnce(activeModel)
      .mockResolvedValue({ ...activeModel, last_validation: failedReport });
    render(<MainModelSettings />);

    const input = await screen.findByLabelText(/Qwen 主模型 ID/);
    fireEvent.change(input, { target: { value: "qwen-candidate" } });
    fireEvent.submit(formOf(input));

    // 失败原因与逐项证据：候选模型 ID、未通过的能力、具体中文原因。
    const report = await screen.findByTestId("model-validation-report");
    expect(report.textContent).toContain("最近一次验证：未通过");
    expect(report.textContent).toContain("qwen-candidate");
    expect(report.textContent).toContain("百炼元数据未声明图片能力。");
    expect(report.textContent).toContain("服务端未按 JSON 对象返回");
    // 候选模型声明的能力档案同样逐项列出（与生效配置区分）。
    expect(screen.getByTestId("candidate-capability-image").textContent).toContain("不支持");
    expect(screen.getByTestId("capability-image").textContent).toContain("支持");
    // 生效配置仍是原模型。
    expect(screen.getByTestId("active-model-id").textContent).toBe("qwen-approved-chat");
    expect(screen.queryByText("验证通过，已从下一条消息起使用新配置。")).toBeNull();
  });

  it("does not claim unsupported capabilities when the metadata query itself failed", async () => {
    const failedReport = {
      model_id: "qwen-missing-model",
      passed: false,
      capabilities: {
        text: false,
        image: false,
        tool_calling: false,
        structured_output: false,
      },
      context_window: null,
      max_input_tokens: null,
      checks: [],
      message: "百炼模型列表中没有精确匹配的模型 ID：qwen-missing-model。",
    };
    api.replaceModelConfiguration.mockRejectedValue(new Error(failedReport.message));
    api.fetchModelSettings
      .mockResolvedValueOnce(activeModel)
      .mockResolvedValue({ ...activeModel, last_validation: failedReport });
    render(<MainModelSettings />);

    const input = await screen.findByLabelText(/Qwen 主模型 ID/);
    fireEvent.change(input, { target: { value: "qwen-missing-model" } });
    fireEvent.submit(formOf(input));

    const report = await screen.findByTestId("model-validation-report");
    expect(report.textContent).toContain("没有精确匹配的模型 ID");
    // 元数据查询失败意味着能力「未核对」，不能替用户断言不支持。
    expect(screen.queryByTestId("candidate-capability-image")).toBeNull();
    // 生效配置的能力仍是原模型的能力。
    expect(screen.getByTestId("capability-image").textContent).toContain("支持");
  });

  it("reports the activation message from the server after a verified save", async () => {
    const activated = {
      ...activeModel,
      model_id: "qwen-candidate",
      revision: 4,
      last_validation: {
        model_id: "qwen-candidate",
        passed: true,
        capabilities: activeModel.capabilities,
        context_window: 1_000_000,
        max_input_tokens: 999_000,
        checks: [],
        message: "验证通过，已从下一条消息起使用新配置。",
      },
    };
    api.replaceModelConfiguration.mockResolvedValue(activated);
    render(<MainModelSettings />);

    const input = await screen.findByLabelText(/Qwen 主模型 ID/);
    fireEvent.change(input, { target: { value: "qwen-candidate" } });
    fireEvent.submit(formOf(input));

    await waitFor(() =>
      expect(api.replaceModelConfiguration).toHaveBeenCalledWith("qwen-candidate")
    );
    expect(
      await screen.findByText("验证通过，已从下一条消息起使用新配置。")
    ).toBeTruthy();
    await waitFor(() => expect(input).toHaveProperty("value", ""));
    expect(screen.getByTestId("active-model-id").textContent).toBe("qwen-candidate");
  });
});
