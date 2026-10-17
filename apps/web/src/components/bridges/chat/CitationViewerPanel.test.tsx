import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CitationDetailProjection, CitationProjection } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  getCitationDetail: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getCitationDetail: mocks.getCitationDetail };
});

import { ApiError } from "@/lib/api";

import { CitationViewerPanel } from "./CitationViewerPanel";

const citation: CitationProjection = {
  citation_id: "cit-1",
  source_layer: "knowledge_base",
  object_id: "obj-1",
  filename: "课程笔记.md",
  media_type: "text/markdown",
  page_number: 3,
  section_title: "熵增原理",
  snippet: "熵在孤立系统中不会减少。",
  rank: 1,
};

function detail(overrides: Partial<CitationDetailProjection> = {}): CitationDetailProjection {
  return {
    citation,
    access_status: "accessible",
    access_message: "原文可访问。",
    download_url: "/knowledge-base/materials/obj-1/download",
    ...overrides,
  };
}

function renderPanel(options: {
  onClose?: () => void;
  citationOverride?: CitationProjection;
  selection?: { messageId: string; citation: CitationProjection } | null;
} = {}) {
  return (
    <CitationViewerPanel
      conversationId="conv-1"
      selection={
        options.selection === undefined
          ? { messageId: "msg-1", citation: options.citationOverride ?? citation }
          : options.selection
      }
      onClose={options.onClose ?? (() => undefined)}
    />
  );
}

afterEach(() => {
  cleanup();
  mocks.getCitationDetail.mockReset();
});

describe("引用资料面板", () => {
  it("默认不渲染；打开后读取详情并保持独立焦点和关闭入口", async () => {
    mocks.getCitationDetail.mockResolvedValue(detail());
    const onClose = vi.fn();
    const { rerender } = render(renderPanel({ onClose, selection: null }));
    expect(screen.queryByTestId("citation-viewer")).toBeNull();

    rerender(renderPanel({ onClose }));
    expect(screen.getByTestId("citation-viewer-loading")).toBeTruthy();
    expect((await screen.findByTestId("citation-viewer-snippet")).textContent).toContain(
      "熵在孤立系统中不会减少。"
    );
    expect(screen.getByTestId("citation-viewer-location").textContent).toContain("第 3 页");
    expect(screen.getByTestId("citation-viewer-location").textContent).toContain("熵增原理");
    expect(screen.getByTestId("citation-viewer-status").textContent).toContain("原文可访问");
    expect(screen.getByTestId("citation-open-1").getAttribute("href")).toBe(
      "/knowledge-base/materials/obj-1/download"
    );
    expect(mocks.getCitationDetail).toHaveBeenCalledWith(
      "conv-1",
      "msg-1",
      "cit-1",
      expect.any(AbortSignal)
    );
    expect(document.activeElement).toBe(screen.getByTestId("citation-viewer"));

    fireEvent.click(screen.getByTestId("citation-viewer-close"));
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("读取失败给出可重试错误，重试成功后显示片段", async () => {
    mocks.getCitationDetail
      .mockRejectedValueOnce(new ApiError("temporary", 503))
      .mockResolvedValueOnce(detail());
    render(renderPanel());

    expect((await screen.findByTestId("citation-viewer-error")).textContent).toContain(
      "资料读取失败"
    );
    expect(screen.queryByTestId("citation-viewer-snippet")).toBeNull();

    fireEvent.click(screen.getByTestId("citation-viewer-retry"));
    expect((await screen.findByTestId("citation-viewer-snippet")).textContent).toContain(
      "熵在孤立系统中不会减少。"
    );
    expect(mocks.getCitationDetail).toHaveBeenCalledTimes(2);
  });

  it("引用不存在时显示无资料状态", async () => {
    mocks.getCitationDetail.mockRejectedValueOnce(new ApiError("not found", 404));
    render(renderPanel());
    expect((await screen.findByTestId("citation-viewer-missing")).textContent).toContain(
      "没有可显示的资料"
    );
  });

  it("原文删除与授权变化显示各自状态，且不保留敏感片段", async () => {
    mocks.getCitationDetail.mockResolvedValueOnce(
      detail({
        access_status: "deleted",
        access_message: "原文已删除，无法打开。",
        download_url: null,
      })
    );
    render(renderPanel());

    expect((await screen.findByTestId("citation-viewer-status")).textContent).toContain(
      "原文已删除"
    );
    expect(screen.getByTestId("citation-viewer-unavailable").textContent).toContain(
      "原文已删除，无法打开。"
    );
    expect(screen.queryByTestId("citation-viewer-snippet")).toBeNull();
    expect(screen.queryByTestId("citation-open-1")).toBeNull();

    cleanup();
    mocks.getCitationDetail.mockReset();
    mocks.getCitationDetail.mockResolvedValueOnce(
      detail({
        access_status: "permission_changed",
        access_message: "附件已从消息中移除，授权已变化，无法打开原文。",
        download_url: null,
      })
    );
    render(renderPanel());

    expect((await screen.findByTestId("citation-viewer-status")).textContent).toContain(
      "授权已变化"
    );
    expect(screen.getByTestId("citation-viewer-unavailable").textContent).toContain(
      "附件已从消息中移除，授权已变化"
    );
    expect(screen.queryByTestId("citation-viewer-snippet")).toBeNull();
    expect(screen.queryByTestId("citation-open-1")).toBeNull();
  });

  it("可访问但没有片段时显示空资料状态", async () => {
    mocks.getCitationDetail.mockResolvedValueOnce(
      detail({ citation: { ...citation, snippet: "   " } })
    );
    render(renderPanel());
    expect((await screen.findByTestId("citation-viewer-empty")).textContent).toContain(
      "没有可读取的原文片段"
    );
  });

  it("拒绝 javascript 与协议相对地址，不生成可点击入口", async () => {
    mocks.getCitationDetail.mockResolvedValueOnce(
      detail({ download_url: "javascript:alert(1)" })
    );
    render(renderPanel());
    await screen.findByTestId("citation-viewer-snippet");
    expect(screen.queryByTestId("citation-open-1")).toBeNull();
  });

  it("快速切换引用时忽略旧请求的迟到结果", async () => {
    let resolveOld: ((value: CitationDetailProjection) => void) | undefined;
    const oldDetail = detail({
      citation: { ...citation, citation_id: "cit-old", snippet: "旧资料片段" },
    });
    const newDetail = detail({
      citation: {
        ...citation,
        citation_id: "cit-new",
        filename: "新资料.md",
        snippet: "当前资料片段",
      },
    });

    mocks.getCitationDetail
      .mockImplementationOnce(
        () =>
          new Promise<CitationDetailProjection>((resolve) => {
            resolveOld = resolve;
          })
      )
      .mockResolvedValueOnce(newDetail);

    const { rerender } = render(
      renderPanel({ citationOverride: { ...citation, citation_id: "cit-old" } })
    );
    rerender(renderPanel({ citationOverride: newDetail.citation }));

    expect(await screen.findByText("当前资料片段")).toBeTruthy();
    await act(async () => {
      resolveOld?.(oldDetail);
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(screen.queryByText("旧资料片段")).toBeNull();
    });
    expect(screen.getByText("当前资料片段")).toBeTruthy();
  });

  it("面板卸载时中止未完成请求，账户切换不会带回旧详情", () => {
    let signal: AbortSignal | undefined;
    mocks.getCitationDetail.mockImplementationOnce(
      (
        _conversationId: string,
        _messageId: string,
        _citationId: string,
        requestSignal: AbortSignal
      ) => {
        signal = requestSignal;
        return new Promise<CitationDetailProjection>(() => undefined);
      }
    );

    const { unmount } = render(renderPanel());
    expect(signal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
  });
});
