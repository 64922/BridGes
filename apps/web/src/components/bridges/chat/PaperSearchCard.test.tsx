import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PaperSearchCard } from "./PaperSearchCard";
import type { PaperSearchProjection } from "@/lib/api";

afterEach(cleanup);

function projection(
  overrides: Partial<PaperSearchProjection> = {}
): PaperSearchProjection {
  return {
    status: "success",
    original_phrase: "Transformer",
    normalized_term: "Transformer",
    expansions: ["attention"],
    confidence: 0.8,
    context_label: "机器学习中的 Transformer 结构",
    queries: [
      {
        source: "arxiv",
        query: "transformer attention",
        status: "success",
        evidence_count: 4,
        retrieved_at: "2026-09-20T10:00:00Z",
        retryable: false,
      },
    ],
    final_query: "transformer attention",
    papers: [
      {
        order: 1,
        arxiv_id: "1706.03762",
        title: "Attention Is All You Need",
        authors: ["Ashish Vaswani"],
        published_year: 2017,
        source: "arxiv",
        abs_url: "https://arxiv.org/abs/1706.03762",
        pdf_url: "https://arxiv.org/pdf/1706.03762",
        primary_category: "cs.CL",
        full_text_available: true,
        role: "foundation",
        reason_zh: "奠基工作，先读这一篇。",
        match_basis: "标题与摘要命中 transformer/attention。",
        unverified: [],
      },
      {
        order: 2,
        arxiv_id: "2005.14165",
        title: "Language Models are Few-Shot Learners",
        authors: ["Tom B. Brown"],
        published_year: 2020,
        source: "arxiv",
        abs_url: "https://arxiv.org/abs/2005.14165",
        pdf_url: null,
        primary_category: "cs.CL",
        full_text_available: false,
        role: "recent",
        reason_zh: "较新研究，了解后续进展。",
        match_basis: "摘要命中 transformer 语境词。",
        unverified: ["未确认取得全文"],
      },
    ],
    requested_count: 5,
    evidence_notes: ["本轮实际返回 2 篇（目标 3–5 篇），不凑满篇数。"],
    pending: null,
    searched_at: "2026-09-20T10:00:01Z",
    error_code: null,
    error_message: null,
    retryable: false,
    ...overrides,
  };
}

describe("PaperSearchCard（V2 Issue 11）", () => {
  it("成功结果呈现原词、实际查询词、阅读顺序、链接与证据边界", () => {
    render(<PaperSearchCard search={projection()} streaming={false} />);

    expect(screen.getByTestId("paper-search-card-success")).toBeTruthy();
    expect(screen.getByText(/原始术语/).textContent).toContain("Transformer");
    expect(screen.getByText(/实际查询词/).textContent).toContain("transformer attention");
    expect(screen.getByTestId("paper-search-count").textContent).toContain("实际 2 篇");

    const results = screen.getByTestId("paper-search-results");
    expect(results.textContent).toContain("Attention Is All You Need");
    expect(results.textContent).toContain("奠基工作");
    // 阅读顺序：第 1 篇在正文里先出现
    expect(results.textContent?.indexOf("Attention Is All You Need")).toBeLessThan(
      results.textContent?.indexOf("Language Models are Few-Shot Learners") ?? 0
    );

    // 真实链接：摘要页始终可点，全文只在确认取得时提供
    expect(screen.getByTestId("paper-1-abs").getAttribute("href")).toBe(
      "https://arxiv.org/abs/1706.03762"
    );
    expect(screen.getByTestId("paper-1-pdf")).toBeTruthy();
    expect(screen.queryByTestId("paper-2-pdf")).toBeNull();
    expect(screen.getByTestId("paper-2-no-full-text")).toBeTruthy();

    // 外部调用记录与证据边界
    expect(screen.getByTestId("paper-search-queries").textContent).toContain(
      "transformer attention"
    );
    expect(screen.getByTestId("paper-search-notes").textContent).toContain("目标 3–5 篇");
  });

  it("澄清状态在同一消息内显示那一个问题", () => {
    render(
      <PaperSearchCard
        search={projection({
          status: "clarification",
          papers: [],
          requested_count: 0,
          pending: {
            module_id: "paper",
            kind: "clarification",
            question: "「Transformer」可能指机器学习结构或电力变压器，你想要哪一类论文？",
            origin_message_id: "assistant-1",
            context: {},
            created_at: "2026-09-20T10:00:00Z",
          },
        })}
        streaming={false}
      />
    );

    const card = screen.getByTestId("paper-search-card-clarification");
    expect(card.textContent).toContain("机器学习结构或电力变压器");
    // 未选择语境时不显示任何论文（不猜、不凑）
    expect(screen.queryByTestId("paper-search-results")).toBeNull();
  });

  it("失败状态显示原因与错误码，可重试时给出重试入口", () => {
    const onRetry = vi.fn();
    render(
      <PaperSearchCard
        search={projection({
          status: "error",
          papers: [],
          requested_count: 0,
          error_code: "paper_search_timeout",
          error_message: "arXiv 检索超时，查询已记录，可重试。",
          retryable: true,
        })}
        streaming={false}
        onRetry={onRetry}
      />
    );

    const card = screen.getByTestId("paper-search-card-error");
    expect(card.getAttribute("role")).toBe("alert");
    expect(card.textContent).toContain("arXiv 检索超时");
    expect(card.textContent).toContain("paper_search_timeout");

    fireEvent.click(screen.getByTestId("paper-search-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("停止状态如实显示已停止并保留查询记录", () => {
    render(
      <PaperSearchCard
        search={projection({
          status: "stopped",
          papers: [],
          requested_count: 0,
          retryable: false,
        })}
        streaming={false}
      />
    );

    expect(screen.getByTestId("paper-search-card-stopped").textContent).toContain(
      "已停止论文检索"
    );
    expect(screen.getByTestId("paper-search-queries").textContent).toContain(
      "transformer attention"
    );
  });

  it("不把检索内部日志（缓存命中/上游次数/冷却）显示给用户", () => {
    render(
      <PaperSearchCard
        search={projection({
          queries: [
            {
              source: "arxiv",
              query: "transformer attention",
              status: "success",
              evidence_count: 4,
              retrieved_at: "2026-09-20T10:00:00Z",
              retryable: false,
              detail: "命中未过期的进程内缓存；上游请求 2 次",
            },
          ],
        })}
        streaming={false}
      />
    );

    const queries = screen.getByTestId("paper-search-queries").textContent ?? "";
    expect(queries).toContain("transformer attention");
    expect(queries).not.toContain("进程内缓存");
    expect(queries).not.toContain("上游请求");
  });

  it("澄清说明指向可见可移除的模块标签（等待状态恢复的真实行为）", () => {
    render(
      <PaperSearchCard
        search={projection({
          status: "clarification",
          papers: [],
          requested_count: 0,
          pending: {
            module_id: "paper",
            kind: "clarification",
            question: "你想找哪一个？",
            origin_message_id: "assistant-1",
            context: {},
            created_at: "2026-09-20T10:00:00Z",
          },
        })}
        streaming={false}
      />
    );

    const card = screen.getByTestId("paper-search-card-clarification").textContent ?? "";
    expect(card).toContain("论文搜索");
    expect(card).toContain("模块标签");
  });
});
