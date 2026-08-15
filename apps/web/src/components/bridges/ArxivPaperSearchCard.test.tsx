import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ArxivSearchProjection } from "@/lib/api";

import { ArxivPaperSearchCard } from "./ArxivPaperSearchCard";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const baseError: ArxivSearchProjection = {
  status: "error",
  trigger_reason: "用户明确要求搜索论文",
  query_summary: "Transformer",
  papers: [],
  searched_at: "2026-08-14T00:00:00Z",
  error_code: "arxiv_rate_limit",
  error_message: "arXiv 请求过于频繁，请稍后重试。",
  upstream_status: "http_429",
  can_retry: true,
  can_cancel: false,
  cache_hit: false,
  stale: false,
  retry_after_seconds: 5,
  attempt_count: 0,
};

describe("ArxivPaperSearchCard retry countdown", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it("disables the retry button and shows the remaining seconds during cooldown", () => {
    render(
      <ArxivPaperSearchCard search={baseError} streaming={false} onRetry={() => {}} />
    );

    const button = screen.getByTestId("arxiv-search-retry") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.textContent).toContain("5 秒后可用");

    fireEvent.click(button);
    expect(button.disabled).toBe(true); // 冷却期内点击不触发重试
  });

  it("re-enables the button and restores the label after the countdown expires", () => {
    const onRetry = vi.fn();
    render(
      <ArxivPaperSearchCard search={baseError} streaming={false} onRetry={onRetry} />
    );

    act(() => {
      vi.advanceTimersByTime(5000);
    });

    const button = screen.getByTestId("arxiv-search-retry") as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(button.getAttribute("aria-disabled")).toBe("false");
    expect(button.textContent).toContain("重试论文搜索");
    expect(button.textContent).not.toContain("秒后可用");

    fireEvent.click(button);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders an enabled retry button when there is no cooldown", () => {
    const onRetry = vi.fn();
    render(
      <ArxivPaperSearchCard
        search={{ ...baseError, retry_after_seconds: null }}
        streaming={false}
        onRetry={onRetry}
      />
    );

    const button = screen.getByTestId("arxiv-search-retry") as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(button.textContent).toContain("重试论文搜索");

    fireEvent.click(button);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("ArxivPaperSearchCard stale annotation", () => {
  const successSearch: ArxivSearchProjection = {
    status: "success",
    trigger_reason: "用户明确要求搜索论文",
    query_summary: "Transformer",
    papers: [
      {
        citation_id: "arxiv-1",
        arxiv_id: "2401.12345v2",
        title: "Attention Is All You Need",
        authors: ["Vaswani"],
        published_at: "2017-06-12T00:00:00Z",
        abs_url: "https://arxiv.org/abs/2401.12345v2",
        pdf_url: "https://arxiv.org/pdf/2401.12345v2",
        abstract: "We propose the Transformer.",
        summary_zh: "论文摘要：…",
        relevance_basis: "与确认查询「Transformer」的相关依据…",
        learning_advice_zh: "建议先阅读摘要…",
      },
    ],
    searched_at: "2026-08-15T00:00:00Z",
    error_code: null,
    error_message: null,
    upstream_status: null,
    can_retry: false,
    can_cancel: false,
    cache_hit: false,
    stale: false,
    retry_after_seconds: null,
    attempt_count: 2,
  };

  it("marks stale results with the not-fresh badge", () => {
    render(
      <ArxivPaperSearchCard
        search={{ ...successSearch, stale: true }}
        streaming={false}
        onRetry={() => {}}
      />
    );

    const badge = screen.getByTestId("arxiv-search-stale");
    expect(badge.textContent).toContain("结果可能不是最新");
  });

  it("hides the badge for fresh results", () => {
    render(
      <ArxivPaperSearchCard
        search={{ ...successSearch, stale: false }}
        streaming={false}
        onRetry={() => {}}
      />
    );

    expect(screen.queryByTestId("arxiv-search-stale")).toBeNull();
  });
});
