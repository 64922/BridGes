import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Composer } from "./Composer";
import {
  draftProjection,
  pickFiles,
  pngFile,
  waitForThumb,
} from "./composer-test-fixtures";
import {
  listChatAttachmentDrafts,
  removeChatAttachmentDraft,
  uploadChatAttachmentDraft,
} from "@/lib/api";

vi.mock("@/lib/api", () => ({
  transcribeDictation: vi.fn(),
  uploadChatAttachmentDraft: vi.fn(),
  listChatAttachmentDrafts: vi.fn().mockResolvedValue([]),
  removeChatAttachmentDraft: vi.fn().mockResolvedValue(undefined),
  chatAttachmentDraftContentUrl: (objectId: string) =>
    `/api/chat/attachment-drafts/${objectId}/content`,
}));

function renderComposer(onSend = vi.fn().mockResolvedValue(true)) {
  render(<Composer onSend={onSend} />);
  return onSend;
}

beforeEach(() => {
  vi.mocked(uploadChatAttachmentDraft).mockImplementation(
    async (file: Blob, filename: string) => {
      const name = (file as File).name ?? filename;
      return draftProjection(`obj-${name}`, name);
    }
  );
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.mocked(listChatAttachmentDrafts).mockResolvedValue([]);
  vi.mocked(removeChatAttachmentDraft).mockResolvedValue(undefined);
});

describe("Composer 发送合同", () => {
  it("Enter 发送，Shift+Enter 换行不发送", async () => {
    const onSend = renderComposer();
    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "按 Enter 发送" } });

    expect(fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true })).toBe(true);
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea, { key: "Enter" });
    await waitFor(() => expect(onSend).toHaveBeenCalledWith("按 Enter 发送", []));
  });

  it("中文输入法组合期间的 Enter 不误发送，确认后再次 Enter 才发送", async () => {
    const onSend = renderComposer();
    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "输入法内容" } });

    fireEvent.compositionStart(textarea);
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.compositionEnd(textarea);
    fireEvent.keyDown(textarea, { key: "Enter" });
    await waitFor(() => expect(onSend).toHaveBeenCalledWith("输入法内容", []));
  });

  it("空输入与纯空白不发送", () => {
    const onSend = renderComposer();
    const send = screen.getByRole("button", { name: "发送消息" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);

    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "   \n  " } });
    expect(send.disabled).toBe(true);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("发送未完成时双击只提交一次", async () => {
    let resolveSend!: (accepted: boolean) => void;
    const onSend = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          resolveSend = resolve;
        })
    );
    render(<Composer onSend={onSend} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "双击测试" } });

    const send = screen.getByRole("button", { name: "发送消息" });
    fireEvent.click(send);
    fireEvent.click(send);
    expect(onSend).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveSend(true);
    });
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveProperty("value", ""));
  });

  it("发送失败保留草稿，重发成功后清空", async () => {
    const onSend = vi
      .fn()
      .mockRejectedValueOnce(new Error("发送失败"))
      .mockResolvedValueOnce(true);
    render(<Composer onSend={onSend} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "失败重发" } });
    pickFiles([pngFile("重发.png")]);
    await waitForThumb("重发.png");

    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("失败重发");
    expect(screen.getAllByText("重发.png").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText("重发.png")).toBeNull());
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("");
  });

  it("同文本换附件后重发，请求身份使用新的附件集合", async () => {
    let attempt = 0;
    const onSend = vi.fn(async () => {
      attempt += 1;
      return attempt > 1;
    });
    render(<Composer onSend={onSend} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "同一句话" } });

    pickFiles([pngFile("旧附件.png")]);
    await waitForThumb("旧附件.png");
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    await waitFor(() =>
      expect(onSend).toHaveBeenNthCalledWith(1, "同一句话", ["obj-旧附件.png"])
    );

    fireEvent.click(screen.getByRole("button", { name: "移除附件 旧附件.png" }));
    pickFiles([pngFile("新附件.png")]);
    await waitForThumb("新附件.png");
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    await waitFor(() =>
      expect(onSend).toHaveBeenNthCalledWith(2, "同一句话", ["obj-新附件.png"])
    );
  });

  it("已绑定附件不会因迟到的草稿恢复重新出现，其他草稿仍可恢复", async () => {
    let resolveDrafts!: (rows: ReturnType<typeof draftProjection>[]) => void;
    vi.mocked(listChatAttachmentDrafts).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveDrafts = resolve;
        })
    );
    const onSend = renderComposer();
    pickFiles([pngFile("已绑定.png")]);
    await waitForThumb("已绑定.png");
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "先发送" } });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    await waitFor(() =>
      expect(onSend).toHaveBeenCalledWith("先发送", ["obj-已绑定.png"])
    );

    await act(async () => {
      resolveDrafts([
        draftProjection("obj-已绑定.png", "已绑定.png"),
        draftProjection("obj-other", "其他草稿.png"),
      ]);
    });

    await waitFor(() => expect(screen.getByText("其他草稿.png")).toBeTruthy());
    expect(screen.queryByText("已绑定.png")).toBeNull();
    expect(removeChatAttachmentDraft).not.toHaveBeenCalled();
  });

  it("纯文字发送不会丢弃在途恢复的其他账户草稿", async () => {
    let resolveDrafts!: (rows: ReturnType<typeof draftProjection>[]) => void;
    vi.mocked(listChatAttachmentDrafts).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveDrafts = resolve;
        })
    );
    const onSend = renderComposer();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "先发送" } });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    await waitFor(() => expect(onSend).toHaveBeenCalledWith("先发送", []));

    await act(async () => {
      resolveDrafts([draftProjection("obj-restored", "账户草稿.png")]);
    });

    await waitFor(() => expect(screen.getByText("账户草稿.png")).toBeTruthy());
  });

  it("首轮前模式入口位于输入区内，选择时回调新模式", () => {
    const onModeChange = vi.fn();
    render(
      <Composer
        onSend={vi.fn()}
        variant="new-chat"
        mode="companion"
        onModeChange={onModeChange}
      />
    );

    const composer = screen.getByTestId("composer");
    const modeToggle = screen.getByTestId("mode-toggle");
    expect(composer.contains(modeToggle)).toBe(true);
    fireEvent.click(within(modeToggle).getByRole("button", { name: "学习模式" }));
    expect(onModeChange).toHaveBeenCalledWith("study");
  });

  it("首轮创建中禁用模式切换，已有会话不提供模式入口", () => {
    const { rerender } = render(
      <Composer
        onSend={vi.fn()}
        variant="new-chat"
        mode="companion"
        onModeChange={vi.fn()}
        modeDisabled
      />
    );
    for (const button of within(screen.getByTestId("mode-toggle")).getAllByRole("button")) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    }

    rerender(<Composer onSend={vi.fn()} mode="study" />);
    expect(screen.queryByTestId("mode-toggle")).toBeNull();
  });

  it("建议预填与已有草稿以换行分隔，不粘连成一句", async () => {
    const { rerender } = render(<Composer onSend={vi.fn()} prefill={null} />);
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "原有草稿" } });

    rerender(
      <Composer
        onSend={vi.fn()}
        prefill={{ text: "帮我找几篇入门论文", nonce: 1 }}
      />
    );

    await waitFor(() => {
      expect(textarea.value).toBe("帮我找几篇入门论文\n原有草稿");
      expect(document.activeElement).toBe(textarea);
    });
  });

  it("切到学习模式时清除无效的日常模块选择", () => {
    const onModuleChange = vi.fn();
    const { rerender } = render(
      <Composer onSend={vi.fn()} mode="companion" moduleId="paper" onModuleChange={onModuleChange} />
    );
    expect(screen.getByTestId("composer-module-chip").textContent).toContain("论文搜索");

    rerender(
      <Composer onSend={vi.fn()} mode="study" moduleId="paper" onModuleChange={onModuleChange} />
    );

    expect(onModuleChange).toHaveBeenCalledWith(null);
    expect(screen.queryByTestId("composer-module-chip")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "添加功能或文件" }));
    expect(screen.getAllByRole("menuitem")).toHaveLength(1);
    expect(screen.getByRole("menuitem", { name: /添加照片和文件/ })).toBeTruthy();
  });
});
