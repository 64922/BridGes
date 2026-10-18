import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { paperSearch } from "@/lib/chat-modules.test-support";
import type { PaperRecommendation, PaperSearchProjection } from "@/lib/api";

import { PaperDetailsEntry } from "./PaperDetailsEntry";
import { PaperDetailsPanel } from "./PaperDetailsPanel";

afterEach(cleanup);

const NOW = "2026-10-18T08:00:00Z";

function recommendation(
  overrides: Partial<PaperRecommendation> = {}
): PaperRecommendation {
  return {
    order: 1,
    candidate_id: "arxiv:1706.03762",
    arxiv_id: "1706.03762",
    title: "Attention Is All You Need",
    authors: ["Ashish Vaswani"],
    published_year: 2017,
    source: "arxiv",
    sources: ["arxiv"],
    abs_url: "https://arxiv.org/abs/1706.03762",
    pdf_url: null,
    full_text_url: null,
    read_scope: "abstract",
    full_text_available: false,
    primary_category: "cs.CL",
    relevance: "direct",
    research_group: "Transformer 注意力",
    source_abstract: "We propose the Transformer, a model relying entirely on attention.",
    role: "foundation",
    reason_zh: "奠基工作，先读这一篇。",
    match_basis: "标题与摘要命中 transformer/attention。",
    match_evidence: [
      {
        requirement: "Transformer",
        source: "title",
        quote: "Attention Is All You Need",
      },
    ],
    summary_zh: "这篇论文提出完全依赖注意力机制的 Transformer。",
    summary_evidence: {
      requirement: "source_summary",
      source: "abstract",
      quote: "a model relying entirely on attention",
    },
    unverified: ["未通读全文"],
    ...overrides,
  };
}

function projection(overrides: Partial<PaperSearchProjection> = {}): PaperSearchProjection {
  return paperSearch({
    normalized_term: "Transformer",
    original_phrase: "Transformer",
    final_query: "transformer attention",
    context_label: "机器学习中的 Transformer 结构",
    research_question: "Transformer 注意力机制如何支持序列建模",
    selected_sources: ["arxiv"],
    searched_at: NOW,
    condition_sources: [
      { kind: "year", value: "2023:", source_phrase: "近三年" },
      { kind: "source", value: "arxiv", source_phrase: "只看 arXiv" },
    ],
    queries: [
      {
        source: "arxiv",
        query: "transformer attention",
        status: "success",
        evidence_count: 1,
        retrieved_at: NOW,
        retryable: false,
      },
      {
        source: "openalex",
        query: "transformer citations",
        status: "timeout",
        evidence_count: 0,
        retrieved_at: NOW,
        retryable: true,
        error_message: "OpenAlex 超时",
      },
    ],
    papers: [recommendation()],
    candidates: [
      {
        candidate_id: "arxiv:1706.03762",
        arxiv_id: "1706.03762",
        doi: null,
        title: "Attention Is All You Need",
        authors: ["Ashish Vaswani"],
        published_year: 2017,
        primary_source: "arxiv",
        sources: ["arxiv"],
        source_ids: ["1706.03762"],
        abs_url: "https://arxiv.org/abs/1706.03762",
        pdf_url: null,
        full_text_url: null,
        abstract: "A real abstract returned by the source.",
        relevance: "direct",
        research_group: "Transformer 注意力",
        match_evidence: [
          {
            requirement: "Transformer",
            source: "abstract",
            quote: "relying entirely on attention",
          },
        ],
        discovery_paths: ["arxiv:1706.03762"],
      },
    ],
    citation_edges: [
      {
        citing_candidate_id: "arxiv:1706.03762",
        cited_candidate_id: "doi:10.5555/seed",
        citing_title: "Attention Is All You Need",
        cited_title: "Seed paper",
        source: "openalex",
      },
    ],
    evidence_notes: ["本轮实际返回 1 篇（目标 3 篇），不凑满篇数。"],
    requested_count: 3,
    ...overrides,
  });
}

describe("论文正文入口与资料面板（Issue 09）", () => {
  it("部分来源失败时在正文旁说明，并且入口打开同轮投影", () => {
    const onOpen = vi.fn();
    render(<PaperDetailsEntry search={projection()} onOpen={onOpen} />);

    expect(screen.getByTestId("paper-source-notice").textContent).toContain(
      "部分来源未成功返回"
    );
    const button = screen.getByTestId("paper-details-open");
    expect(button.textContent).toContain("查看论文依据、摘要与检索过程");

    fireEvent.click(button);
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen.mock.calls[0][0]).toBe(button);
  });

  it("没有可核对记录时不显示空入口", () => {
    const { container } = render(
      <PaperDetailsEntry
        search={paperSearch({ status: "clarification", queries: [], evidence_notes: [] })}
      />
    );
    expect(container.innerHTML).toBe("");
  });

  it("检索中不提供基于不完整快照的资料入口", () => {
    const { container } = render(
      <PaperDetailsEntry search={paperSearch({ ...projection(), status: "searching" })} />
    );
    expect(container.innerHTML).toBe("");
  });

  it("资料面板展示条件原话、候选、查询记录与引用关系", () => {
    const onSelectPaper = vi.fn();
    render(
      <PaperDetailsPanel
        selection={{ messageId: "a-1", search: projection(), paperId: null }}
        onClose={vi.fn()}
        onSelectPaper={onSelectPaper}
      />
    );

    expect(screen.getByTestId("paper-details-panel")).toBeTruthy();
    expect(screen.getByLabelText("关闭论文资料面板")).toBeTruthy();
    expect(screen.getByTestId("paper-search-overview")).toBeTruthy();
    expect(screen.getByTestId("paper-search-conditions").textContent).toContain(
      "年份 2023 年起"
    );
    expect(screen.getByTestId("paper-search-conditions").textContent).toContain(
      "原话：“近三年”"
    );
    expect(screen.getByTestId("paper-search-conditions").textContent).toContain(
      "来源 arXiv"
    );

    expect(screen.getByTestId("paper-search-recommendations").textContent).toContain(
      "Attention Is All You Need"
    );
    const select = screen.getByTestId("paper-panel-select-1");
    fireEvent.click(select);
    expect(onSelectPaper).toHaveBeenCalledWith("arxiv:1706.03762");

    fireEvent.click(screen.getByText("完整候选（1）"));
    expect(screen.getByText("A real abstract returned by the source.")).toBeTruthy();
    expect(screen.getByTestId("candidate-arxiv:1706.03762-paths").textContent).toContain(
      "arXiv 检索（1706.03762）"
    );
    expect(screen.getByTestId("paper-search-queries").textContent).toContain(
      "transformer attention"
    );
    expect(screen.getByTestId("paper-search-citations").textContent).toContain(
      "Attention Is All You Need → Seed paper"
    );
    expect(screen.getByTestId("paper-search-notes").textContent).toContain(
      "本轮实际返回 1 篇"
    );
  });

  it("论文详情如实区分摘要读取、不可得全文与原始摘要", () => {
    const onSelectPaper = vi.fn();
    render(
      <PaperDetailsPanel
        selection={{
          messageId: "a-1",
          search: projection(),
          paperId: "arxiv:1706.03762",
        }}
        onClose={vi.fn()}
        onSelectPaper={onSelectPaper}
      />
    );

    expect(screen.getByTestId("paper-detail-read-scope").textContent).toBe(
      "本轮只读取标题与摘要"
    );
    expect(screen.getByTestId("paper-detail-read-scope").textContent).not.toContain(
      "本轮读取了全文"
    );
    expect(screen.getByTestId("paper-detail-no-fulltext").textContent).toContain(
      "来源未返回或未核实全文链接"
    );
    expect(screen.queryByTestId("paper-detail-fulltext")).toBeNull();
    expect(screen.getByTestId("paper-detail-summary").textContent).toContain(
      "完全依赖注意力机制"
    );
    expect(screen.getByTestId("paper-detail-1-match-evidence").textContent).toContain(
      "Attention Is All You Need"
    );
    expect(screen.getByTestId("paper-detail-unverified").textContent).toContain("未通读全文");
    fireEvent.click(screen.getByText("来源返回的原始摘要"));
    expect(screen.getByTestId("paper-detail-abstract").textContent).toContain(
      "relying entirely on attention"
    );

    fireEvent.click(screen.getByTestId("paper-detail-back"));
    expect(onSelectPaper).toHaveBeenCalledWith(null);
  });

  it("Esc 关闭资料面板", () => {
    const onClose = vi.fn();
    render(
      <PaperDetailsPanel
        selection={{ messageId: "a-1", search: projection(), paperId: null }}
        onClose={onClose}
        onSelectPaper={vi.fn()}
      />
    );

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
