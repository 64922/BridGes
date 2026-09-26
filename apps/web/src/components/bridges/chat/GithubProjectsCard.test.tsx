import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GithubProjectsCard } from "./GithubProjectsCard";
import type {
  GithubProjectsProjection,
  GithubRecommendation,
  GithubRejectedRepository,
} from "@/lib/api";

afterEach(cleanup);

function projection(
  overrides: Partial<GithubProjectsProjection> = {}
): GithubProjectsProjection {
  return {
    status: "success",
    scenario: "校园二手书交换平台",
    original_request: "我想做一个校园二手书交换平台，学生可以发布想卖的书",
    features: ["发布想卖的书"],
    tech_terms: [],
    whole_idea: true,
    component_terms: [],
    context_source: null,
    queries: [
      {
        source: "github_search",
        query: "校园二手书交换",
        status: "success",
        evidence_count: 4,
        retrieved_at: "2026-09-26T02:00:00Z",
        retryable: false,
      },
    ],
    recommendations: [],
    rejected: [],
    rate_limit: {
      limited: false,
      note: null,
    },
    evidence_boundary: ["API 元数据来自 GitHub 接口，README 是项目自述。"],
    empty_reason: null,
    retryable: false,
    error_code: null,
    error_message: null,
    completed_at: "2026-09-26T02:00:05Z",
    pending: null,
    ...overrides,
  };
}

const recommendation: GithubRecommendation = {
  rank: 1,
  full_name: "demo/bookswap",
  html_url: "https://github.com/demo/bookswap",
  coverage: "whole",
  covers_parts: [],
  coverage_note: "覆盖了你列出的全部 1 项要点，按整体项目呈现。",
  description: "校园二手书交换平台",
  topics: ["campus", "book-exchange"],
  language: "Python",
  feature_matches: [
    {
      feature: "发布想卖的书",
      matched: true,
      evidence_kind: "readme",
      matched_terms: ["发布"],
      evidence: "README 自述命中关键词「发布」：学生可以发布想卖的书。",
    },
  ],
  matched_feature_count: 1,
  evidence_kinds: ["metadata", "readme"],
  readme_status: "read",
  readme_url: "https://github.com/demo/bookswap/blob/main/README.md",
  readme_excerpt: "学生可以发布想卖的书。",
  files_read: [],
  implementation_checks: [],
  maintenance: {
    pushed_at: "2026-09-20T00:00:00Z",
    created_at: "2025-01-01T00:00:00Z",
    stars: 120,
    forks: 8,
    open_issues: 3,
    archived: false,
    is_fork: false,
    runnable_hints: ["package.json"],
    note: "最近推送在 0.2 个月前。star 只作辅助。",
  },
  license: {
    detected: true,
    spdx_id: "MIT",
    name: "MIT License",
    path: "LICENSE",
    license_url: null,
    file_read: true,
    excerpt: null,
    note: "已读取许可文件正文。",
  },
  reason_zh: "按功能匹配排在前面：1 项要点中命中 1 项，按整体项目呈现。",
  borrow_note: "可借鉴：README 自述里的整体功能划分（项目自述，本轮未读取实现文件，不对内部架构作断言）。",
  strengths: ["许可：MIT（已读取许可文件）。"],
  limitations: ["未读取实现文件，不对内部架构与代码质量作断言。"],
  insight_zh: null,
  retrieved_at: "2026-09-26T02:00:05Z",
};

describe("GithubProjectsCard（V2 Issue 16）", () => {
  it("没有投影时不渲染任何内容", () => {
    const { container } = render(
      <GithubProjectsCard projects={null} streaming={false} />
    );
    expect(container.firstChild).toBeNull();
  });

  it("成功结果卡逐项展示链接、覆盖面、功能匹配与维护许可证据", () => {
    render(
      <GithubProjectsCard
        projects={projection({ recommendations: [recommendation] })}
        streaming={false}
      />
    );
    expect(screen.getByTestId("github-projects-card-success")).toBeTruthy();
    expect(screen.getByTestId("github-projects-count").textContent).toContain("1 个仓库");
    const link = screen.getByTestId(
      "github-recommendation-demo/bookswap-link"
    ) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("https://github.com/demo/bookswap");
    expect(screen.getByTestId("github-recommendation-demo/bookswap-coverage").textContent).toBe(
      "整体项目"
    );
    // 功能匹配逐条展示，命中要带上依据等级与命中原词。
    expect(screen.getByTestId("github-feature-发布想卖的书").textContent).toContain("已覆盖");
    expect(screen.getByTestId("github-feature-发布想卖的书").textContent).toContain(
      "README 自述"
    );
    expect(screen.getByTestId("github-feature-发布想卖的书").textContent).toContain("发布");
    // 维护与许可证据是仓库身份信息之外的另一类证据，必须出现。
    expect(
      screen.getByTestId("github-recommendation-demo/bookswap-borrow").textContent
    ).toContain("借鉴角度");
  });

  it("未读取实现文件时给出「不对内部架构断言」的局限，不出现架构结论", () => {
    render(
      <GithubProjectsCard
        projects={projection({ recommendations: [recommendation] })}
        streaming={false}
      />
    );
    const limitations = screen.getByTestId(
      "github-recommendation-demo/bookswap-limitations"
    ).textContent;
    expect(limitations).toContain("未读取实现文件");
    expect(limitations).toContain("不对内部架构");
    // 骨架里不该出现「代码可自由复用」这类越界说法。
    expect(screen.queryByText(/可自由复用/)).toBeNull();
  });

  it("组件项目明确标出只覆盖哪一部分", () => {
    render(
      <GithubProjectsCard
        projects={projection({
          whole_idea: false,
          component_terms: ["登录认证组件"],
          recommendations: [
            {
              ...recommendation,
              coverage: "component",
              covers_parts: ["登录认证组件"],
              coverage_note: "你本轮要的是单个组件的公开实现，该仓库只覆盖这一部分，不代表完整产品。",
            },
          ],
        })}
        streaming={false}
      />
    );
    expect(screen.getByTestId("github-recommendation-demo/bookswap-coverage").textContent).toBe(
      "组件项目（只覆盖一部分）"
    );
    expect(screen.getByText(/只覆盖这一部分，不代表完整产品/)).toBeTruthy();
    expect(screen.getByText(/你本轮指名的组件/)).toBeTruthy();
  });

  it("等待澄清时展示问题，并说明回复会带着模块发出", () => {
    render(
      <GithubProjectsCard
        projects={projection({
          status: "clarification",
          scenario: "",
          pending: {
            module_id: "github",
            kind: "clarification",
            question: "你想找哪个项目或哪个功能的公开仓库？",
            origin_message_id: "a-1",
            context: {},
            created_at: "2026-09-26T02:00:00Z",
          },
        })}
        streaming={false}
      />
    );
    expect(screen.getByTestId("github-projects-clarification").textContent).toContain(
      "你想找哪个项目或哪个功能的公开仓库？"
    );
    expect(screen.getByTestId("github-projects-clarification").textContent).toContain(
      "输入框上方的模块标签可随时移除"
    );
  });

  it("空结果显示实际查询词与检索事实，不用记忆补造条目", () => {
    render(
      <GithubProjectsCard
        projects={projection({
          status: "empty",
          recommendations: [],
          empty_reason: "上游返回 3 条候选，但都没有取得证据，本轮不推荐。",
        })}
        streaming={false}
      />
    );
    expect(screen.getByTestId("github-projects-card-empty")).toBeTruthy();
    expect(screen.getByText(/上游返回 3 条候选，但都没有取得证据/)).toBeTruthy();
    const queries = screen.getByTestId("github-queries").textContent ?? "";
    expect(queries).toContain("校园二手书交换");
    // 来源按模块词汇表呈现，露内部键名或检索内部日志都算违规。
    expect(queries).toContain("GitHub 检索");
    expect(queries).not.toContain("github_search");
    expect(queries).not.toContain("进程内缓存");
  });

  it("仅取得元数据时标注降级，并列出被剔除的候选与理由", () => {
    const rejected: GithubRejectedRepository = {
      full_name: "someone/irrelevant",
      url: "https://github.com/someone/irrelevant",
      reason: "名称、简介与话题里都没有出现你整体想法的关键词。",
    };
    render(
      <GithubProjectsCard
        projects={projection({
          status: "metadata_only",
          recommendations: [recommendation],
          rejected: [rejected],
        })}
        streaming={false}
      />
    );
    expect(screen.getByTestId("github-projects-degraded").textContent).toBe("仅元数据");
    expect(screen.getByTestId("github-rejected").textContent).toContain("someone/irrelevant");
    expect(screen.getByTestId("github-rejected").textContent).toContain(
      "名称、简介与话题里都没有出现你整体想法的关键词"
    );
  });

  it("限流时只给「是否撞上 + 面向用户的说明」，不暴露剩余额度等内部计数", () => {
    render(
      <GithubProjectsCard
        projects={projection({
          status: "metadata_only",
          recommendations: [recommendation],
          rate_limit: {
            limited: true,
            note: "仓库读取额度已用尽，本轮只展示已取得的证据。",
          },
        })}
        streaming={false}
      />
    );
    const note = screen.getByTestId("github-rate-limit").textContent;
    expect(note).toContain("仓库读取额度已用尽");
    expect(note).not.toContain("检索剩余");
    expect(note).not.toContain("重置于");
  });

  it("失败时显示错误码与重试按钮，点击回调把重试请求交回上层", () => {
    const onRetry = vi.fn();
    render(
      <GithubProjectsCard
        projects={projection({
          status: "error",
          error_code: "github_unavailable",
          error_message: "GitHub 接口暂时不可用。",
          retryable: true,
        })}
        streaming={false}
        onRetry={onRetry}
      />
    );
    const card = screen.getByTestId("github-projects-card-error");
    expect(card.getAttribute("role")).toBe("alert");
    expect(card.textContent).toContain("GitHub 接口暂时不可用。");
    expect(card.textContent).toContain("github_unavailable");
    screen.getByTestId("github-projects-retry").click();
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("指向前文的原词时展示来源与消息标识（可追溯）", () => {
    render(
      <GithubProjectsCard
        projects={projection({
          context_source: {
            kind: "paper_search",
            label: "上一轮论文搜索",
            phrase: "Transformer",
            message_id: "m-9",
          },
        })}
        streaming={false}
      />
    );
    const source = screen.getByTestId("github-context-source").textContent;
    expect(source).toContain("上一轮论文搜索");
    expect(source).toContain("Transformer");
    expect(source).toContain("m-9");
  });
});
