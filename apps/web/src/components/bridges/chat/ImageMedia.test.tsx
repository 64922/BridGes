import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ImageAssetProjection,
  ImageTaskProjection,
  ImageVersionProjection,
} from "@/lib/api";
import { ApiError, getImageAsset, getImageTask } from "@/lib/api";

import { ImageMedia } from "./ImageMedia";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getImageTask: vi.fn(),
    getImageAsset: vi.fn(),
    imageVersionUrl: (
      conversationId: string,
      assetId: string,
      versionId: string,
      download = false
    ) =>
      `/api/chat/conversations/${conversationId}/image-assets/${assetId}/versions/${versionId}/image${
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

function asset(overrides: Partial<ImageAssetProjection> = {}): ImageAssetProjection {
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

function task(overrides: Partial<ImageTaskProjection> = {}): ImageTaskProjection {
  return {
    task_id: "image-task-1",
    attempt_number: 1,
    kind: "generate",
    prompt: "一座桥的素描",
    model_id: "qwen-image-2.0-pro-2026-06-22",
    status: "succeeded",
    error_code: null,
    error_message: null,
    retryable: false,
    asset_id: "asset-1",
    result_version_id: "version-1",
    deleted: false,
    synthetic: true,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function renderMedia(
  value: ImageTaskProjection,
  props: Partial<Parameters<typeof ImageMedia>[0]> = {}
) {
  return render(
    <ImageMedia conversationId="conv-1" task={value} {...props} />
  );
}

beforeEach(() => {
  vi.mocked(getImageTask).mockReset();
  vi.mocked(getImageAsset).mockReset();
  vi.mocked(getImageTask).mockImplementation(async (_conversationId, taskId) =>
    task({ task_id: taskId })
  );
  vi.mocked(getImageAsset).mockResolvedValue(asset());
});

afterEach(cleanup);

describe("ImageMedia（工单 14）", () => {
  it("成功且资产可读取时显示真实缩略图、替代文本、下载与详情入口", async () => {
    const onOpenPreview = vi.fn();
    renderMedia(task(), { onOpenPreview });

    const preview = await screen.findByTestId("image-media-preview");
    expect(preview.getAttribute("src")).toContain("/versions/version-1/image");
    expect(preview.getAttribute("alt")).toBe("桥的素描");
    expect(screen.getByTestId("image-media-alt-text").textContent).toContain("桥的素描");
    expect(screen.getByTestId("image-media-prompt").textContent).toContain(
      "一座桥的素描"
    );
    expect(screen.getByTestId("image-media-download").getAttribute("href")).toContain(
      "/versions/version-1/image?download=1"
    );

    fireEvent.click(screen.getByTestId("image-media-open"));
    expect(onOpenPreview).toHaveBeenCalledTimes(1);
    expect(onOpenPreview.mock.calls[0][0]).toBeInstanceOf(HTMLButtonElement);
  });

  it("优先显示该任务产出的版本，而不是资产后来的当前版本", async () => {
    vi.mocked(getImageAsset).mockResolvedValue(
      asset({
        current_version_id: "version-2",
        version_count: 2,
        versions: [
          version({ version_id: "version-1" }),
          version({ version_id: "version-2", kind: "edit", prompt: "改成夜景" }),
        ],
      })
    );
    renderMedia(task());

    const preview = await screen.findByTestId("image-media-preview");
    expect(preview.getAttribute("src")).toContain("/versions/version-1/image");
  });

  it("资产已删除或无权限时不显示成功缩略图或下载假链接", async () => {
    vi.mocked(getImageAsset).mockRejectedValue(
      new ApiError("图片资产不存在。", 404)
    );
    renderMedia(task());

    const error = await screen.findByTestId("image-media-asset-error");
    expect(error.textContent).toContain("已删除或当前账户无权访问");
    expect(screen.queryByTestId("image-media-preview")).toBeNull();
    expect(screen.queryByTestId("image-media-download")).toBeNull();
    expect(screen.queryByTestId("image-media-retry")).toBeNull();
  });

  it("资产暂时读取失败可重试，重试成功后才显示媒体", async () => {
    vi.mocked(getImageAsset)
      .mockRejectedValueOnce(new ApiError("图片服务暂不可用。", 503))
      .mockResolvedValueOnce(asset());
    renderMedia(task());

    expect((await screen.findByTestId("image-media-asset-error")).textContent).toContain(
      "HTTP 503"
    );
    expect(screen.queryByTestId("image-media-preview")).toBeNull();

    fireEvent.click(screen.getByTestId("image-media-retry"));
    expect(await screen.findByTestId("image-media-preview")).toBeTruthy();
  });

  it("任务成功但资产没有可读版本时不显示缩略图与下载", async () => {
    vi.mocked(getImageAsset).mockResolvedValue(
      asset({ current_version_id: null, version_count: 0, versions: [] })
    );
    renderMedia(task({ result_version_id: null }));

    const unavailable = await screen.findByTestId("image-media-unavailable");
    expect(unavailable.textContent).toContain("没有可读取的版本");
    expect(screen.queryByTestId("image-media-preview")).toBeNull();
    expect(screen.queryByTestId("image-media-download")).toBeNull();
  });

  it("图片解码失败保留说明与合法下载，并允许重新显示", async () => {
    renderMedia(task());
    const preview = await screen.findByTestId("image-media-preview");

    fireEvent.error(preview);
    expect((await screen.findByTestId("image-media-preview-error")).textContent).toContain(
      "图片无法显示"
    );
    expect(screen.getByTestId("image-media-download")).toBeTruthy();
    expect(screen.getByTestId("image-media-prompt").textContent).toContain(
      "一座桥的素描"
    );

    fireEvent.click(screen.getByTestId("image-media-preview-retry"));
    expect(await screen.findByTestId("image-media-preview")).toBeTruthy();
  });

  it("失败状态如实呈现且不提供已退役的恢复按钮", async () => {
    vi.mocked(getImageTask).mockResolvedValue(
      task({ status: "failed", error_message: "图片生成失败，请稍后重试。" })
    );
    renderMedia(task({ status: "failed", error_message: "图片生成失败，请稍后重试。" }));

    expect((await screen.findByTestId("image-media-status")).textContent).toBe("失败");
    expect(screen.getByTestId("image-media-error").textContent).toContain(
      "图片生成失败"
    );
    expect(screen.getByText(/图片生成已退役/)).toBeTruthy();
    expect(screen.queryByTestId("image-media-preview")).toBeNull();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
  });

  it("停止状态显示已停止与迟到结果不保存的说明", async () => {
    vi.mocked(getImageTask).mockResolvedValue(task({ status: "cancelled" }));
    renderMedia(task({ status: "cancelled" }));

    expect((await screen.findByTestId("image-media-status")).textContent).toBe("已停止");
    expect(screen.getByTestId("image-media-stopped").textContent).toContain(
      "迟到的云端结果不会保存"
    );
    expect(screen.queryByTestId("image-media-download")).toBeNull();
  });

  it("进行中的任务先在正文旁显示真实状态，完成后只刷新一次消息", async () => {
    const onRefresh = vi.fn();
    vi.mocked(getImageTask).mockResolvedValue(task());
    const view = renderMedia(task({ status: "queued", result_version_id: null }), {
      onRefreshMessages: onRefresh,
    });

    expect(await screen.findByTestId("image-media-preview")).toBeTruthy();
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("image-media-status").textContent).toBe("已完成");

    // 父级刷新后同一任务再次渲染，不重复通知或插入第二个媒体。
    view.rerender(
      <ImageMedia conversationId="conv-1" task={task()} onRefreshMessages={onRefresh} />
    );
    expect(screen.getAllByTestId("image-media-preview")).toHaveLength(1);
    const callsAfterRerender = onRefresh.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(onRefresh).toHaveBeenCalledTimes(callsAfterRerender);
  });

  it("资产已删除的消息不显示旧缓存或下载", async () => {
    vi.mocked(getImageTask).mockResolvedValue(task({ deleted: true }));
    renderMedia(task({ deleted: true }));

    expect(await screen.findByTestId("image-media-deleted")).toBeTruthy();
    expect(screen.getByText("该图片已删除。")).toBeTruthy();
    expect(screen.queryByTestId("image-media-preview")).toBeNull();
    expect(screen.queryByTestId("image-media-download")).toBeNull();
  });
});
