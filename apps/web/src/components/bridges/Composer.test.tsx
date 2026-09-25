import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Composer } from "./Composer";
import {
  listChatAttachmentDrafts,
  removeChatAttachmentDraft,
  uploadChatAttachmentDraft,
} from "@/lib/api";

const PLACEHOLDER = "输入消息，开始日常对话";

vi.mock("@/lib/api", () => ({
  transcribeDictation: vi.fn(),
  uploadChatAttachmentDraft: vi.fn(),
  listChatAttachmentDrafts: vi.fn().mockResolvedValue([]),
  removeChatAttachmentDraft: vi.fn().mockResolvedValue(undefined),
  chatAttachmentDraftContentUrl: (objectId: string) => `/api/chat/attachment-drafts/${objectId}/content`,
}));

function draftProjection(objectId: string, filename: string, mediaType = "image/png") {
  return {
    object_id: objectId,
    original_filename: filename,
    media_type: mediaType,
    content_length: 1024,
    content_hash: "hash",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function pngFile(name = "截图.png", size = 100): File {
  const file = new File([new Uint8Array(size)], name, { type: "image/png" });
  return file;
}

function renderComposer(onSend = vi.fn().mockResolvedValue(true)) {
  render(<Composer onSend={onSend} />);
  return onSend;
}

function pickFiles(files: File[]) {
  const composer = screen.getByTestId("composer");
  const fileInput = composer.querySelector('input[type="file"]') as HTMLInputElement;
  if (!fileInput) throw new Error("缺少隐藏的文件选择 input");
  Object.defineProperty(fileInput, "files", { value: files, configurable: true });
  fireEvent.change(fileInput);
}

async function waitForThumb(filename: string) {
  return await waitFor(() => screen.getAllByText(filename)[0]);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.mocked(listChatAttachmentDrafts).mockResolvedValue([]);
  vi.mocked(removeChatAttachmentDraft).mockResolvedValue(undefined);
});

describe("Composer 输入框", () => {
  it.each(["conversation", "new-chat"] as const)(
    "%s 变体显示日常对话占位文案",
    (variant) => {
      render(<Composer variant={variant} onSend={vi.fn()} />);

      expect(screen.getByRole("textbox").getAttribute("placeholder")).toBe(PLACEHOLDER);
    }
  );
});

describe("Composer 照片附件（Issue 05）", () => {
  beforeEach(() => {
    vi.mocked(uploadChatAttachmentDraft).mockImplementation(async (file: Blob) => {
      const name = (file as File).name ?? "图片.png";
      return draftProjection(`obj-${name}`, name);
    });
  });

  it("选择图片后上传为草稿，缩略图显示文件名、类型与页序", async () => {
    renderComposer();

    pickFiles([pngFile("合同扫描.png")]);
    await waitForThumb("合同扫描.png");

    expect(screen.getByText("PNG")).toBeTruthy();
    expect(screen.getByText("第 1 张")).toBeTruthy();
    expect(uploadChatAttachmentDraft).toHaveBeenCalledTimes(1);
  });

  it("纯附件（无文字）即可发送，onSend 收到按序 attachmentIds", async () => {
    const onSend = renderComposer();

    pickFiles([pngFile("a.png"), pngFile("b.png", 200)]);
    await waitForThumb("b.png");

    const send = screen.getByRole("button", { name: "发送消息" }) as HTMLButtonElement;
    expect(send.disabled).toBe(false);
    fireEvent.click(send);

    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
    expect(onSend).toHaveBeenCalledWith("", ["obj-a.png", "obj-b.png"]);
  });

  it("粘贴图片文件会创建草稿", async () => {
    renderComposer();

    const textarea = screen.getByRole("textbox");
    fireEvent.paste(textarea, { clipboardData: { files: [pngFile("粘贴.png")] } });

    await waitForThumb("粘贴.png");
  });

  it("拖入图片文件会创建草稿", async () => {
    renderComposer();

    const composer = screen.getByTestId("composer");
    fireEvent.drop(composer, { dataTransfer: { files: [pngFile("拖入.png")] } });

    await waitForThumb("拖入.png");
  });

  it("不支持的类型显示中文原因，且不清空其他草稿", async () => {
    renderComposer();

    pickFiles([pngFile("第一张.png")]);
    await waitForThumb("第一张.png");

    pickFiles([new File([new Uint8Array(10)], "说明.pdf", { type: "application/pdf" })]);

    await waitFor(() => screen.getByText(/暂不支持该文件类型/));
    expect(screen.getAllByText("第一张.png").length).toBeGreaterThan(0);
    // 被拒绝的文件不会创建草稿。
    expect(uploadChatAttachmentDraft).toHaveBeenCalledTimes(1);
  });

  it("超过 10MB 的图片显示中文原因", async () => {
    renderComposer();

    pickFiles([pngFile("太大.png", 11 * 1024 * 1024)]);

    await waitFor(() => screen.getByText(/超过 10 MB/));
    expect(uploadChatAttachmentDraft).not.toHaveBeenCalled();
  });

  it("上移/下移按钮调整页序且键盘可达", async () => {
    renderComposer();

    pickFiles([pngFile("one.png"), pngFile("two.png", 200)]);
    await waitForThumb("two.png");

    fireEvent.click(screen.getByRole("button", { name: "将 two.png 上移" }));

    const list = screen.getByTestId("composer-attachments");
    const text = list.textContent ?? "";
    expect(text.indexOf("two.png")).toBeLessThan(text.indexOf("one.png"));
    // 边界禁用：首张不可再上移，末张不可再下移。
    expect(
      (screen.getByRole("button", { name: "将 two.png 上移" }) as HTMLButtonElement).disabled
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "将 one.png 下移" }) as HTMLButtonElement).disabled
    ).toBe(true);
    // 下移可把顺序还原。
    fireEvent.click(screen.getByRole("button", { name: "将 two.png 下移" }));
    const restored = (screen.getByTestId("composer-attachments").textContent ?? "");
    expect(restored.indexOf("one.png")).toBeLessThan(restored.indexOf("two.png"));
  });

  it("移除按钮删除草稿并调用后端删除", async () => {
    renderComposer();

    pickFiles([pngFile("要删的.png")]);
    await waitForThumb("要删的.png");

    fireEvent.click(screen.getByRole("button", { name: "移除图片 要删的.png" }));

    await waitFor(() => expect(screen.queryByText("要删的.png")).toBeNull());
    expect(removeChatAttachmentDraft).toHaveBeenCalledWith("obj-要删的.png");
  });

  it("挂载时恢复账户既有草稿", async () => {
    vi.mocked(listChatAttachmentDrafts).mockResolvedValue([
      draftProjection("obj-old", "旧照片.png"),
    ]);

    renderComposer();

    await waitForThumb("旧照片.png");
  });

  it("发送失败时保留文字与附件等待重试", async () => {
    const onSend = vi.fn().mockRejectedValue(new Error("发送失败"));

    render(<Composer onSend={onSend} />);
    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "看看这张" } });
    pickFiles([pngFile("保留.png")]);
    await waitForThumb("保留.png");

    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));

    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("看看这张");
    expect(screen.getAllByText("保留.png").length).toBeGreaterThan(0);
  });

  it("发送成功后清空文字与附件", async () => {
    const onSend = vi.fn().mockResolvedValue(true);
    render(<Composer onSend={onSend} />);
    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "带图消息" } });
    pickFiles([pngFile("成功.png")]);
    await waitForThumb("成功.png");

    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));

    await waitFor(() => expect(screen.queryByText("成功.png")).toBeNull());
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("");
  });
});
