import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatAttachmentProjection } from "@/lib/api";
import {
  chatAttachmentContentUrl,
  fetchChatAttachmentContent,
} from "@/lib/api";

import { AttachmentViewerPanel } from "./AttachmentViewerPanel";

vi.mock("@/lib/api", () => ({
  fetchChatAttachmentContent: vi.fn(),
  chatAttachmentContentUrl: (conversationId: string, objectId: string) =>
    `/api/chat/conversations/${conversationId}/attachments/${objectId}/download`,
  getAttachmentIngestion: vi.fn(),
}));

const NOW = "2026-10-18T00:00:00Z";

function attachment(
  overrides: Partial<ChatAttachmentProjection> = {}
): ChatAttachmentProjection {
  return {
    object_id: "obj-file",
    original_filename: "课程笔记.md",
    media_type: "text/markdown",
    content_length: 128,
    content_hash: "hash-file",
    conversation_id: "conv-1",
    message_id: "msg-user-1",
    status: "bound",
    ordinal: 1,
    ingestion_status: "ready",
    ingestion_error: null,
    adopted_from_conversation_id: "",
    adopted_from_object_id: "",
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function blobResponse(blob: Blob): Response {
  return { blob: async () => blob } as Response;
}

function renderPanel(value: ChatAttachmentProjection, onClose = vi.fn()) {
  return render(
    <AttachmentViewerPanel
      conversationId="conv-1"
      selection={{ messageId: "msg-user-1", attachment: value }}
      onClose={onClose}
    />
  );
}

beforeAll(() => {
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => "blob:preview-url"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
});

beforeEach(() => {
  vi.mocked(fetchChatAttachmentContent).mockReset();
  vi.mocked(URL.createObjectURL).mockClear();
  vi.mocked(URL.revokeObjectURL).mockClear();
});

afterEach(cleanup);

describe("AttachmentViewerPanel", () => {
  it("照片经账户授权的同源对象读取，显示预览并在关闭时释放临时 URL", async () => {
    vi.mocked(fetchChatAttachmentContent).mockResolvedValue(
      blobResponse(new Blob(["png"], { type: "image/png" }))
    );
    const { unmount } = renderPanel(
      attachment({
        object_id: "obj-photo",
        original_filename: "板书.png",
        media_type: "image/png",
        content_hash: "hash-photo",
      })
    );

    const image = await screen.findByTestId("attachment-viewer-image");
    expect(image.getAttribute("src")).toBe("blob:preview-url");
    expect(image.getAttribute("alt")).toContain("板书.png");
    expect(fetchChatAttachmentContent).toHaveBeenCalledWith(
      "conv-1",
      "obj-photo",
      expect.any(AbortSignal)
    );
    expect(
      screen.getByTestId("attachment-viewer-download").getAttribute("href")
    ).toBe(chatAttachmentContentUrl("conv-1", "obj-photo"));

    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-url");
  });

  it("Markdown 走既有安全渲染，不执行附件里的 HTML 或脚本", async () => {
    vi.mocked(fetchChatAttachmentContent).mockResolvedValue(
      blobResponse(
        new Blob(["# 热力学\n\n**熵增**\n\n<script>window.__pwned = true</script>"], {
          type: "text/markdown",
        })
      )
    );
    renderPanel(attachment());

    const preview = await screen.findByTestId("attachment-viewer-markdown");
    expect(within(preview).getByRole("heading", { level: 1 }).textContent).toContain(
      "热力学"
    );
    expect(within(preview).getByText("熵增").tagName).toBe("STRONG");
    expect(preview.querySelector("script")).toBeNull();
  });

  it("TXT 以纯文本显示，不解释为 HTML", async () => {
    vi.mocked(fetchChatAttachmentContent).mockResolvedValue(
      blobResponse(
        new Blob(["<img src=x onerror=alert(1)>\n第二行"], { type: "text/plain" })
      )
    );
    renderPanel(
      attachment({
        object_id: "obj-text",
        original_filename: "课堂记录.txt",
        media_type: "text/plain",
        content_hash: "hash-text",
      })
    );

    const preview = await screen.findByTestId("attachment-viewer-text");
    expect(preview.tagName).toBe("PRE");
    expect(preview.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(preview.querySelector("img")).toBeNull();
  });

  it("DOCX 等不支持格式给出说明和合法原件入口，不发起内容读取", async () => {
    renderPanel(
      attachment({
        object_id: "obj-docx",
        original_filename: "报告.docx",
        media_type:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        content_hash: "hash-docx",
      })
    );

    expect(screen.getByTestId("attachment-viewer-unsupported")).toBeTruthy();
    expect(screen.getByTestId("attachment-viewer-unsupported").textContent).toContain(
      "暂不支持"
    );
    expect(
      screen.getByTestId("attachment-viewer-download").getAttribute("href")
    ).toBe(chatAttachmentContentUrl("conv-1", "obj-docx"));
    expect(fetchChatAttachmentContent).not.toHaveBeenCalled();
  });

  it("读取失败可重试；图片解码失败也可回到原件入口", async () => {
    vi.mocked(fetchChatAttachmentContent)
      .mockRejectedValueOnce(new Error("附件内容读取失败，请稍后重试。"))
      .mockResolvedValueOnce(blobResponse(new Blob(["正文"], { type: "text/plain" })));
    renderPanel(
      attachment({
        object_id: "obj-retry",
        original_filename: "重试.txt",
        media_type: "text/plain",
        content_hash: "hash-retry",
      })
    );

    expect(await screen.findByTestId("attachment-viewer-error")).toBeTruthy();
    fireEvent.click(screen.getByTestId("attachment-viewer-retry"));
    expect((await screen.findByTestId("attachment-viewer-text")).textContent).toBe("正文");

    cleanup();
    vi.mocked(fetchChatAttachmentContent).mockResolvedValue(
      blobResponse(new Blob(["png"], { type: "image/png" }))
    );
    renderPanel(
      attachment({
        object_id: "obj-broken-image",
        original_filename: "坏图.png",
        media_type: "image/png",
        content_hash: "hash-broken",
      })
    );
    fireEvent.error(await screen.findByTestId("attachment-viewer-image"));
    expect(screen.getByTestId("attachment-viewer-error").textContent).toContain(
      "无法显示"
    );
    expect(screen.getByTestId("attachment-viewer-download")).toBeTruthy();
  });

  it("关闭按钮与 Esc 调用关闭；卸载中止未完成读取", async () => {
    let capturedSignal: AbortSignal | undefined;
    vi.mocked(fetchChatAttachmentContent).mockImplementation(
      (_conversationId, _objectId, signal) => {
        capturedSignal = signal;
        return new Promise<Response>(() => {});
      }
    );
    const onClose = vi.fn();
    const { unmount } = renderPanel(attachment(), onClose);

    await waitFor(() => expect(capturedSignal).toBeDefined());
    fireEvent.click(screen.getByTestId("attachment-viewer-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);

    expect(capturedSignal?.aborted).toBe(false);
    unmount();
    expect(capturedSignal?.aborted).toBe(true);
  });

  it("多文件按各自对象身份渲染序号与解析状态", async () => {
    vi.mocked(fetchChatAttachmentContent).mockResolvedValue(
      blobResponse(new Blob(["pdf"], { type: "application/pdf" }))
    );
    renderPanel(
      attachment({
        object_id: "obj-second",
        original_filename: "第二份.pdf",
        media_type: "application/pdf",
        content_hash: "hash-second",
        ordinal: 2,
        ingestion_status: "empty",
      })
    );

    expect(screen.getByTestId("attachment-viewer-meta").textContent).toContain("第 2 个");
    expect(screen.getByTestId("attachment-viewer-status")).toBeTruthy();
    expect(screen.getByTestId("attachment-viewer-ingestion-empty")).toBeTruthy();
  });
});
