import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { VideoAssetProjection, VideoTaskProjection } from "@/lib/api";
import { ApiError, getVideoAsset, getVideoTask } from "@/lib/api";

import { VideoMedia } from "./VideoMedia";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getVideoTask: vi.fn(),
    getVideoAsset: vi.fn(),
    videoUrl: (conversationId: string, assetId: string, download = false) =>
      `/api/chat/conversations/${conversationId}/video-assets/${assetId}/video${
        download ? "?download=1" : ""
      }`,
  };
});

const NOW = "2026-10-18T00:00:00Z";

function asset(overrides: Partial<VideoAssetProjection> = {}): VideoAssetProjection {
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

function task(overrides: Partial<VideoTaskProjection> = {}): VideoTaskProjection {
  return {
    task_id: "video-task-1",
    prompt: "一段海浪视频",
    synthetic_media: true,
    model_id: "wan2.7-t2v-2026-06-12",
    status: "succeeded",
    error_code: null,
    error_message: null,
    retryable: false,
    asset_id: "video-asset-1",
    result_object_id: "object-video-1",
    deleted: false,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(getVideoTask).mockReset();
  vi.mocked(getVideoAsset).mockReset();
  vi.mocked(getVideoTask).mockImplementation(async (_conversationId, taskId) =>
    task({ task_id: taskId })
  );
  vi.mocked(getVideoAsset).mockResolvedValue(asset());
});

afterEach(cleanup);

describe("VideoMedia（工单 14）", () => {
  it("成功时嵌入视频但绝不自动播放，保留控件、说明与下载", async () => {
    const onOpenPreview = vi.fn();
    render(
      <VideoMedia
        conversationId="conv-1"
        task={task()}
        onOpenPreview={onOpenPreview}
      />
    );

    const video = await screen.findByTestId("video-media-preview");
    expect(video.tagName).toBe("VIDEO");
    expect(video.hasAttribute("autoplay")).toBe(false);
    expect((video as HTMLVideoElement).autoplay).toBe(false);
    expect(video.hasAttribute("controls")).toBe(true);
    expect(video.getAttribute("preload")).toBe("none");
    expect(video.getAttribute("src")).toContain("/video-assets/video-asset-1/video");
    expect(screen.getByTestId("video-media-description").textContent).toContain(
      "海浪拍打礁石"
    );
    expect(screen.getByTestId("video-media-prompt").textContent).toContain(
      "一段海浪视频"
    );
    expect(screen.getByTestId("video-media-download").getAttribute("href")).toContain(
      "?download=1"
    );

    fireEvent.click(screen.getByTestId("video-media-details"));
    expect(onOpenPreview).toHaveBeenCalledTimes(1);
  });

  it("资产已删除或跨账户拒绝时不显示预览与下载", async () => {
    vi.mocked(getVideoAsset).mockRejectedValue(
      new ApiError("视频资产不存在。", 404)
    );
    render(<VideoMedia conversationId="conv-1" task={task()} />);

    const error = await screen.findByTestId("video-media-asset-error");
    expect(error.textContent).toContain("已删除或当前账户无权访问");
    expect(screen.queryByTestId("video-media-preview")).toBeNull();
    expect(screen.queryByTestId("video-media-download")).toBeNull();
    expect(screen.queryByTestId("video-media-retry")).toBeNull();
  });

  it("暂时读取失败可重试，成功后才显示视频", async () => {
    vi.mocked(getVideoAsset)
      .mockRejectedValueOnce(new ApiError("视频服务暂不可用。", 503))
      .mockResolvedValueOnce(asset());
    render(<VideoMedia conversationId="conv-1" task={task()} />);

    expect((await screen.findByTestId("video-media-asset-error")).textContent).toContain(
      "HTTP 503"
    );
    expect(screen.queryByTestId("video-media-preview")).toBeNull();
    fireEvent.click(screen.getByTestId("video-media-retry"));
    expect(await screen.findByTestId("video-media-preview")).toBeTruthy();
  });

  it("播放失败保留说明与下载，并允许重新加载", async () => {
    render(<VideoMedia conversationId="conv-1" task={task()} />);
    const video = await screen.findByTestId("video-media-preview");
    fireEvent.error(video);

    expect((await screen.findByTestId("video-media-preview-error")).textContent).toContain(
      "视频无法播放"
    );
    expect(screen.getByTestId("video-media-prompt").textContent).toContain(
      "一段海浪视频"
    );
    expect(screen.getByTestId("video-media-download")).toBeTruthy();

    fireEvent.click(screen.getByTestId("video-media-preview-retry"));
    expect(await screen.findByTestId("video-media-preview")).toBeTruthy();
  });

  it("失败状态如实呈现且不提供已退役的恢复按钮", async () => {
    vi.mocked(getVideoTask).mockResolvedValue(
      task({ status: "failed", error_message: "视频生成失败，请稍后重试。" })
    );
    render(
      <VideoMedia
        conversationId="conv-1"
        task={task({ status: "failed", error_message: "视频生成失败，请稍后重试。" })}
      />
    );

    expect((await screen.findByTestId("video-media-status")).textContent).toBe("失败");
    expect(screen.getByTestId("video-media-error").textContent).toContain(
      "视频生成失败"
    );
    expect(screen.getByText(/视频生成已退役/)).toBeTruthy();
    expect(screen.queryByTestId("video-media-preview")).toBeNull();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
  });

  it("停止状态显示已停止与迟到结果不保存的说明", async () => {
    vi.mocked(getVideoTask).mockResolvedValue(task({ status: "cancelled" }));
    render(<VideoMedia conversationId="conv-1" task={task({ status: "cancelled" })} />);

    expect((await screen.findByTestId("video-media-status")).textContent).toBe("已停止");
    expect(screen.getByTestId("video-media-stopped").textContent).toContain(
      "迟到的云端结果不会保存"
    );
    expect(screen.queryByTestId("video-media-preview")).toBeNull();
  });

  it("删除的任务不显示旧缓存或下载", async () => {
    vi.mocked(getVideoTask).mockResolvedValue(task({ deleted: true }));
    render(<VideoMedia conversationId="conv-1" task={task({ deleted: true })} />);

    expect(await screen.findByTestId("video-media-deleted")).toBeTruthy();
    expect(screen.getByText("该视频已删除。")).toBeTruthy();
    expect(screen.queryByTestId("video-media-preview")).toBeNull();
    expect(screen.queryByTestId("video-media-download")).toBeNull();
  });
});
