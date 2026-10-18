import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ImageAssetProjection,
  ImageVersionProjection,
  VideoAssetProjection,
} from "@/lib/api";
import {
  ApiError,
  fetchImageVersionContent,
  fetchVideoContent,
  getImageAsset,
  getVideoAsset,
} from "@/lib/api";
import type { ChatMediaSelection } from "@/lib/chat-media";

import { MediaViewerPanel } from "./MediaViewerPanel";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getImageAsset: vi.fn(),
    getVideoAsset: vi.fn(),
    fetchImageVersionContent: vi.fn(),
    fetchVideoContent: vi.fn(),
    imageVersionUrl: (
      conversationId: string,
      assetId: string,
      versionId: string,
      download = false
    ) =>
      `/api/chat/conversations/${conversationId}/image-assets/${assetId}/versions/${versionId}/image${
        download ? "?download=1" : ""
      }`,
    videoUrl: (conversationId: string, assetId: string, download = false) =>
      `/api/chat/conversations/${conversationId}/video-assets/${assetId}/video${
        download ? "?download=1" : ""
      }`,
  };
});

const NOW = "2026-10-18T00:00:00Z";

function version(overrides: Partial<ImageVersionProjection> = {}): ImageVersionProjection {
  return {
    version_id: "version-1",
    asset_id: "asset-1",
    parent_version_id: null,
    kind: "generate",
    prompt: "一座桥的素描",
    model_id: "qwen-image-2.0-pro-2026-06-22",
    object_id: "object-1",
    media_type: "image/png",
    content_length: 68,
    synthetic: true,
    created_at: NOW,
    ...overrides,
  };
}

function imageAsset(overrides: Partial<ImageAssetProjection> = {}): ImageAssetProjection {
  return {
    asset_id: "asset-1",
    alt_text: "桥的素描",
    alt_text_source: "model",
    current_version_id: "version-1",
    version_count: 1,
    synthetic: true,
    versions: [version()],
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function videoAsset(overrides: Partial<VideoAssetProjection> = {}): VideoAssetProjection {
  return {
    asset_id: "video-asset-1",
    synthetic_media: true,
    description: "海浪拍打礁石的合成视频。",
    description_source: "prompt",
    object_id: "object-video-1",
    prompt: "一段海浪视频",
    model_id: "wan2.7-t2v-2026-06-12",
    cloud_task_id: "cloud-task-1",
    media_type: "video/mp4",
    content_length: 1024,
    deleted: false,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function blobResponse(blob: Blob): Response {
  return { blob: async () => blob } as Response;
}

function imageSelection(
  overrides: Partial<ChatMediaSelection> = {}
): ChatMediaSelection {
  return {
    conversationId: "conv-1",
    messageId: "message-1",
    kind: "image",
    taskId: "image-task-1",
    assetId: "asset-1",
    resultVersionId: "version-1",
    ...overrides,
  };
}

function videoSelection(
  overrides: Partial<ChatMediaSelection> = {}
): ChatMediaSelection {
  return {
    conversationId: "conv-1",
    messageId: "message-1",
    kind: "video",
    taskId: "video-task-1",
    assetId: "video-asset-1",
    resultVersionId: null,
    ...overrides,
  };
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
  vi.mocked(getImageAsset).mockReset();
  vi.mocked(getVideoAsset).mockReset();
  vi.mocked(fetchImageVersionContent).mockReset();
  vi.mocked(fetchVideoContent).mockReset();
  vi.mocked(URL.createObjectURL).mockClear();
  vi.mocked(URL.revokeObjectURL).mockClear();
  vi.mocked(getImageAsset).mockResolvedValue(imageAsset());
  vi.mocked(getVideoAsset).mockResolvedValue(videoAsset());
  vi.mocked(fetchImageVersionContent).mockImplementation(async () =>
    blobResponse(new Blob(["png"], { type: "image/png" }))
  );
  vi.mocked(fetchVideoContent).mockImplementation(async () =>
    blobResponse(new Blob(["webm"], { type: "video/webm" }))
  );
});

afterEach(cleanup);

describe("MediaViewerPanel（工单 14）", () => {
  it("图片面板按任务版本读取授权字节，切换版本时释放旧 object URL", async () => {
    vi.mocked(URL.createObjectURL)
      .mockReturnValueOnce("blob:version-one")
      .mockReturnValueOnce("blob:version-two");
    vi.mocked(getImageAsset).mockResolvedValue(
      imageAsset({
        current_version_id: "version-2",
        version_count: 2,
        versions: [
          version({ version_id: "version-1" }),
          version({ version_id: "version-2", kind: "edit", prompt: "改成夜景" }),
        ],
      })
    );
    const onClose = vi.fn();
    const { unmount } = render(
      <MediaViewerPanel
        conversationId="conv-1"
        selection={imageSelection()}
        onClose={onClose}
      />
    );

    const image = await screen.findByTestId("media-viewer-image");
    expect(image.getAttribute("src")).toBe("blob:version-one");
    expect(image.getAttribute("alt")).toBe("桥的素描");
    expect(fetchImageVersionContent).toHaveBeenCalledWith(
      "conv-1",
      "asset-1",
      "version-1",
      expect.any(AbortSignal)
    );
    expect(screen.getByTestId("media-viewer-alt-text").textContent).toContain(
      "桥的素描"
    );
    expect(screen.getByTestId("media-viewer-download").getAttribute("href")).toContain(
      "/versions/version-1/image?download=1"
    );

    fireEvent.click(screen.getAllByTestId("media-viewer-version-button")[1]);
    await waitFor(() =>
      expect(screen.getByTestId("media-viewer-image").getAttribute("src")).toBe(
        "blob:version-two"
      )
    );
    expect(fetchImageVersionContent).toHaveBeenLastCalledWith(
      "conv-1",
      "asset-1",
      "version-2",
      expect.any(AbortSignal)
    );
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:version-one");

    fireEvent.click(screen.getByTestId("media-viewer-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);

    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:version-two");
  });

  it("图片资产已删除或跨账户拒绝时明确说明且不保留下载", async () => {
    vi.mocked(getImageAsset).mockRejectedValue(
      new ApiError("图片资产不存在。", 404)
    );
    render(
      <MediaViewerPanel
        conversationId="conv-1"
        selection={imageSelection()}
        onClose={vi.fn()}
      />
    );

    const error = await screen.findByTestId("media-viewer-error");
    expect(error.textContent).toContain("已删除或当前账户无权访问");
    expect(screen.queryByTestId("media-viewer-download")).toBeNull();
    expect(screen.queryByTestId("media-viewer-retry")).toBeNull();
    expect(fetchImageVersionContent).not.toHaveBeenCalled();
  });

  it("图片字节读取失败可重试，成功后才显示媒体", async () => {
    vi.mocked(fetchImageVersionContent)
      .mockRejectedValueOnce(new ApiError("图片读取失败。", 503))
      .mockImplementationOnce(async () =>
        blobResponse(new Blob(["png"], { type: "image/png" }))
      );
    render(
      <MediaViewerPanel
        conversationId="conv-1"
        selection={imageSelection()}
        onClose={vi.fn()}
      />
    );

    expect((await screen.findByTestId("media-viewer-error")).textContent).toContain(
      "图片读取失败"
    );
    fireEvent.click(screen.getByTestId("media-viewer-retry"));
    expect(await screen.findByTestId("media-viewer-image")).toBeTruthy();
  });

  it("视频面板不自动播放，关闭或卸载时释放 URL 并暂停播放器", async () => {
    const pause = vi
      .spyOn(HTMLMediaElement.prototype, "pause")
      .mockImplementation(() => {});
    const onClose = vi.fn();
    const { unmount } = render(
      <MediaViewerPanel
        conversationId="conv-1"
        selection={videoSelection()}
        onClose={onClose}
      />
    );

    const video = await screen.findByTestId("media-viewer-video");
    expect(video.tagName).toBe("VIDEO");
    expect(video.hasAttribute("autoplay")).toBe(false);
    expect(video.hasAttribute("controls")).toBe(true);
    expect(video.getAttribute("src")).toBe("blob:preview-url");
    expect(screen.getByTestId("media-viewer-description").textContent).toContain(
      "海浪拍打礁石"
    );
    expect(screen.getByTestId("media-viewer-download").getAttribute("href")).toContain(
      "?download=1"
    );

    fireEvent.click(screen.getByTestId("media-viewer-close"));
    expect(onClose).toHaveBeenCalledTimes(1);

    unmount();
    expect(pause).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-url");
    pause.mockRestore();
  });

  it("视频资产已删除或跨账户拒绝时不保留预览或下载", async () => {
    vi.mocked(getVideoAsset).mockRejectedValue(
      new ApiError("视频资产不存在。", 404)
    );
    render(
      <MediaViewerPanel
        conversationId="conv-1"
        selection={videoSelection()}
        onClose={vi.fn()}
      />
    );

    const error = await screen.findByTestId("media-viewer-error");
    expect(error.textContent).toContain("已删除或当前账户无权访问");
    expect(screen.queryByTestId("media-viewer-download")).toBeNull();
    expect(fetchVideoContent).not.toHaveBeenCalled();
  });

  it("未选择媒体时不渲染面板", () => {
    render(
      <MediaViewerPanel conversationId="conv-1" selection={null} onClose={vi.fn()} />
    );
    expect(screen.queryByTestId("media-viewer")).toBeNull();
  });
});
