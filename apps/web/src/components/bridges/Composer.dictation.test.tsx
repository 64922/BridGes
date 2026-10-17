import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Composer } from "./Composer";
import { transcribeDictation } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  transcribeDictation: vi.fn(),
  uploadChatAttachmentDraft: vi.fn(),
  listChatAttachmentDrafts: vi.fn().mockResolvedValue([]),
  removeChatAttachmentDraft: vi.fn().mockResolvedValue(undefined),
  chatAttachmentDraftContentUrl: (objectId: string) =>
    `/api/chat/attachment-drafts/${objectId}/content`,
}));

class FakeMediaRecorder {
  state: "inactive" | "recording" = "inactive";
  mimeType = "audio/webm";
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.ondataavailable?.({
      data: new Blob([new Uint8Array([1, 2, 3])], { type: "audio/webm" }),
    });
    this.onstop?.();
  }
}

const getUserMedia = vi.fn();

let originalMediaRecorder: PropertyDescriptor | undefined;
let originalMediaDevices: PropertyDescriptor | undefined;

beforeEach(() => {
  originalMediaRecorder = Object.getOwnPropertyDescriptor(window, "MediaRecorder");
  originalMediaDevices = Object.getOwnPropertyDescriptor(navigator, "mediaDevices");
  Object.defineProperty(window, "MediaRecorder", {
    configurable: true,
    writable: true,
    value: FakeMediaRecorder,
  });
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
  getUserMedia.mockResolvedValue({
    getTracks: () => [{ stop: vi.fn() }],
  });
  vi.mocked(transcribeDictation).mockResolvedValue({
    status: "success",
    transcript: "这是听写结果",
    retryable: false,
    created_at: "2026-01-01T00:00:00Z",
  } as never);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  if (originalMediaRecorder) {
    Object.defineProperty(window, "MediaRecorder", originalMediaRecorder);
  } else {
    delete (window as unknown as { MediaRecorder?: unknown }).MediaRecorder;
  }
  if (originalMediaDevices) {
    Object.defineProperty(navigator, "mediaDevices", originalMediaDevices);
  } else {
    delete (navigator as unknown as { mediaDevices?: unknown }).mediaDevices;
  }
});

describe("Composer 听写", () => {
  it("不可用或尚未建立会话时禁用入口并说明原因，不申请麦克风", () => {
    const { rerender } = render(
      <Composer variant="new-chat" onSend={vi.fn()} asr={{ available: true }} />
    );
    const button = screen.getByRole("button", { name: "开始听写" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText("发送首条消息后可使用听写")).toBeTruthy();

    rerender(
      <Composer
        conversationId="c-1"
        onSend={vi.fn()}
        asr={{ available: false, reason: "语音转写能力暂不可用，请稍后重试。" }}
      />
    );
    expect((screen.getByRole("button", { name: "开始听写" }) as HTMLButtonElement).disabled).toBe(
      true
    );
    expect(screen.getByText("语音转写能力暂不可用，请稍后重试。")).toBeTruthy();
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("开始、停止录音后回填可编辑文本，不自动发送", async () => {
    const onSend = vi.fn();
    render(<Composer conversationId="c-1" onSend={onSend} />);

    fireEvent.click(screen.getByRole("button", { name: "开始听写" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "停止录音并转写" })).toBeTruthy()
    );
    expect(getUserMedia).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "停止录音并转写" }));
    await waitFor(() =>
      expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("这是听写结果")
    );

    expect(transcribeDictation).toHaveBeenCalledTimes(1);
    const [conversationId, audio, duration] = vi.mocked(transcribeDictation).mock.calls[0];
    expect(conversationId).toBe("c-1");
    expect(audio).toBeInstanceOf(Blob);
    expect(typeof duration).toBe("number");
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByText("已转写，可编辑后发送")).toBeTruthy();
  });

  it("转写失败保留同一段音频并可原样重试", async () => {
    vi.mocked(transcribeDictation)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({
        status: "success",
        transcript: "重试后的文本",
        retryable: false,
        created_at: "2026-01-01T00:00:00Z",
      } as never);
    const onSend = vi.fn();
    render(<Composer conversationId="c-1" onSend={onSend} />);

    fireEvent.click(screen.getByRole("button", { name: "开始听写" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "停止录音并转写" })).toBeTruthy()
    );
    fireEvent.click(screen.getByRole("button", { name: "停止录音并转写" }));

    await waitFor(() => expect(screen.getByText(/网络异常，转写失败/)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "重试转写" }));

    await waitFor(() =>
      expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("重试后的文本")
    );
    expect(transcribeDictation).toHaveBeenCalledTimes(2);
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("取消录音不提交转写", async () => {
    render(<Composer conversationId="c-1" onSend={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "开始听写" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "取消录音" })).toBeTruthy()
    );
    fireEvent.click(screen.getByRole("button", { name: "取消录音" }));

    expect(transcribeDictation).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "停止录音并转写" })).toBeNull();
  });
});
