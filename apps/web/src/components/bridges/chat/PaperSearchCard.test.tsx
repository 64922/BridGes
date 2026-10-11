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
    research_question: "Transformer 注意力机制如何支持序列建模",
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
        candidate_id: "arxiv:1706.03762",
        arxiv_id: "1706.03762",
        title: "Attention Is All You Need",
        authors: ["Ashish Vaswani"],
        published_year: 2017,
        source: "arxiv",
        abs_url: "https://arxiv.org/abs/1706.03762",
        pdf_url: "https://arxiv.org/pdf/1706.03762",
        read_scope: "abstract",
        primary_category: "cs.CL",
        full_text_available: true,
        relevance: "direct",
        research_group: "Transformer 注意力",
        source_abstract: "We propose the Transformer, a model relying entirely on attention.",
        role: "foundation",
        reason_zh: "奠基工作，先读这一篇。",
        match_basis: "标题与摘要命中 transformer/attention。",
        unverified: [],
      },
      {
        order: 2,
        candidate_id: "arxiv:2005.14165",
        arxiv_id: "2005.14165",
        title: "Language Models are Few-Shot Learners",
        authors: ["Tom B. Brown"],
        published_year: 2020,
        source: "arxiv",
        abs_url: "https://arxiv.org/abs/2005.14165",
        pdf_url: null,
        read_scope: "abstract",
        primary_category: "cs.CL",
        full_text_available: false,
        relevance: "partial",
        research_group: "语言模型",
        source_abstract: "We train a large language model for few-shot learning.",
        role: "recent",
        reason_zh: "较新研究，了解后续进展。",
        match_basis: "摘要命中 transformer 语境词。",
        unverified: ["未确认取得全文"],
      },
    ],
    requested_count: 5,
    condition_sources: [],
    evidence_notes: ["本轮实际返回 2 篇（目标 5–8 篇），不凑满篇数。"],
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
    expect(screen.getByTestId("paper-1-read-scope").textContent).toBe(
      "本轮只读取标题与摘要"
    );
    expect(screen.getByTestId("paper-1-read-scope").textContent).not.toContain(
      "本轮读取了全文"
    );
    expect(screen.queryByTestId("paper-2-pdf")).toBeNull();
    expect(screen.getByTestId("paper-2-no-full-text")).toBeTruthy();

    // 外部调用记录与证据边界
    expect(screen.getByTestId("paper-search-queries").textContent).toContain(
      "transformer attention"
    );
    expect(screen.getByTestId("paper-search-notes").textContent).toContain("目标 5–8 篇");
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

  it("展示完整候选的全文链接，同时把阅读范围留给重点推荐说明", () => {
    render(
      <PaperSearchCard
        search={projection({
          papers: [],
          requested_count: 0,
          candidates: [
            {
              candidate_id: "doi:10.5555/example",
              arxiv_id: null,
              doi: "10.5555/example",
              title: "CNN for few-shot image classification",
              authors: ["Jane Doe"],
              published_year: 2024,
              primary_source: "crossref",
              sources: ["crossref"],
              source_ids: ["doi:10.5555/example"],
              abs_url: "https://doi.org/10.5555/example",
              pdf_url: null,
              full_text_url: "https://example.org/paper.pdf",
              abstract: "A useful abstract.",
              relevance: "direct",
              research_group: "",
              match_evidence: [],
              discovery_paths: ["crossref:10.5555/example"],
            },
          ],
        })}
        streaming={false}
      />
    );

    const link = screen.getByTestId("candidate-doi:10.5555/example-pdf");
    expect(link.getAttribute("href")).toBe("https://example.org/paper.pdf");
    expect(link.textContent).toContain("本轮读取范围见重点推荐");
    expect(screen.getByText("摘要：A useful abstract.")).toBeTruthy();
    expect(screen.getByTestId("candidate-doi:10.5555/example-paths").textContent).toContain(
      "Crossref 检索（10.5555/example）"
    );
  });

  it("展示硬条件并保留用户原话", () => {
    render(
      <PaperSearchCard
        search={projection({
          condition_sources: [
            { kind: "year", value: "2023:", source_phrase: "近三年" },
            { kind: "source", value: "openalex", source_phrase: "只查 OpenAlex" },
          ],
        })}
        streaming={false}
      />
    );

    const conditions = screen.getByTestId("paper-search-conditions").textContent ?? "";
    expect(conditions).toContain("年份 2023 年起");
    expect(conditions).toContain("原话：“近三年”");
    expect(conditions).toContain("来源 OpenAlex");
  });

  it("如实标注候选来自 OpenAlex 引用扩展", () => {
    render(
      <PaperSearchCard
        search={projection({
          papers: [],
          requested_count: 0,
          candidates: [
            {
              candidate_id: "doi:10.5555/citing",
              arxiv_id: null,
              doi: "10.5555/citing",
              title: "A citing paper",
              authors: [],
              published_year: 2024,
              primary_source: "openalex",
              sources: ["openalex"],
              source_ids: ["openalex:W123"],
              abs_url: "https://openalex.org/W123",
              pdf_url: null,
              full_text_url: null,
              abstract: "",
              relevance: "exploratory",
              research_group: "",
              match_evidence: [],
              discovery_paths: ["citation:doi:10.5555/seed"],
            },
          ],
        })}
        streaming={false}
      />
    );

    expect(
      screen.getByTestId("candidate-doi:10.5555/citing-paths").textContent
    ).toContain("OpenAlex 引用扩展（种子 doi:10.5555/seed）");
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
