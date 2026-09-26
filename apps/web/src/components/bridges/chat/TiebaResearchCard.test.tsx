import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TiebaResearchCard } from "./TiebaResearchCard";
import type { TiebaPostProjection, TiebaResearchProjection } from "@/lib/api";

afterEach(cleanup);

function projection(
  overrides: Partial<TiebaResearchProjection> = {}
): TiebaResearchProjection {
  return {
    status: "success",
    topic: "宿舍条件",
    original_question: "华东交通大学吧里最近说的宿舍条件怎么样",
    topic_terms: ["宿舍", "条件"],
    place_or_event: [],
    time_filter: {
      requirement: "最近",
      year: null,
      applied: false,
      note: "相对时间说法没有绝对年份，按页面时间如实标注，未做年份过滤。",
    },
    queries: [
      {
        source: "tavily",
        query: "tieba.baidu.com 华东交通大学吧 宿舍 条件",
        status: "success",
        evidence_count: 3,
        retrieved_at: "2026-09-25T02:00:00Z",
        retryable: false,
      },
    ],
    forum: "华东交通大学吧",
    confirmed_posts: [],
    candidate_links: [],
    rejected_candidates: [],
    official_check_requested: false,
    official_checks: [],
    sections: [],
    evidence_boundary: [],
    empty_reason: null,
    retryable: false,
    error_code: null,
    error_message: null,
    completed_at: "2026-09-25T02:00:05Z",
    pending: null,
    ...overrides,
  };
}

const confirmedPost: TiebaPostProjection = {
  thread_id: "8123456789",
  url: "https://tieba.baidu.com/p/8123456789",
  title: "南区宿舍到底怎么样",
  affiliation_evidence: "页面吧头显示「华东交通大学吧」。",
  read_status: "read",
  pages_read: 1,
  pages_limit: 2,
  total_pages: 3,
  floor_min: 1,
  floor_max: 12,
  replies_obtained: true,
  replies: [
    {
      floor: 3,
      posted_at: "2026-09-20 21:14",
      is_original_poster: false,
      content: "南区六人间，有空调，热水要刷卡。",
    },
  ],
  read_error_code: null,
  read_error_message: null,
  retrieved_at: "2026-09-25T02:00:02Z",
};

describe("TiebaResearchCard（V2 Issue 14）", () => {
  it("呈现原始问题、时间条件执行状态与真实外部调用记录", () => {
    render(<TiebaResearchCard research={projection()} streaming={false} />);
    const card = screen.getByTestId("tieba-research-card-success");
    expect(card.textContent).toContain("华东交通大学吧里最近说的宿舍条件怎么样");
    expect(card.textContent).toContain("宿舍、条件");
    expect(screen.getByTestId("tieba-time-filter").textContent).toContain("最近");
    expect(screen.getByTestId("tieba-time-filter").textContent).toContain("未做年份过滤");
    expect(screen.getByTestId("tieba-research-queries").textContent).toContain(
      "tieba.baidu.com 华东交通大学吧 宿舍 条件"
    );
  });

  it("已确认帖子呈现读到的楼层与时间，并注明读取范围", () => {
    render(
      <TiebaResearchCard
        research={projection({
          confirmed_posts: [confirmedPost],
          sections: ["第 3 楼提到南区六人间，热水要刷卡。"],
        })}
        streaming={false}
      />
    );
    const posts = screen.getByTestId("tieba-research-posts");
    expect(posts.textContent).toContain("南区宿舍到底怎么样");
    expect(posts.textContent).toContain("页面吧头显示「华东交通大学吧」");
    expect(posts.textContent).toContain("已读 1/2 页");
    expect(posts.textContent).toContain("第 1–12 楼");
    const replies = screen.getByTestId("tieba-post-8123456789-replies");
    expect(replies.textContent).toContain("第 3 楼");
    expect(replies.textContent).toContain("2026-09-20 21:14");
    expect(replies.textContent).toContain("南区六人间，有空调，热水要刷卡。");
    expect(screen.queryByTestId("tieba-post-8123456789-no-replies")).toBeNull();
    expect(screen.getByTestId("tieba-research-sections").textContent).toContain("第 3 楼提到");
  });

  it("拿不到回复时明确标注未取得回复内容，而不是静默省略", () => {
    const post: TiebaPostProjection = {
      ...confirmedPost,
      read_status: "access_restricted",
      pages_read: 0,
      floor_min: null,
      floor_max: null,
      replies_obtained: false,
      replies: [],
      read_error_code: "tieba_access_restricted",
      read_error_message: "百度安全验证要求登录，本轮未绕过访问限制。",
    };
    render(
      <TiebaResearchCard research={projection({ confirmed_posts: [post] })} streaming={false} />
    );
    const note = screen.getByTestId("tieba-post-8123456789-no-replies");
    expect(note.textContent).toContain("未取得回复内容");
    expect(note.textContent).toContain("未绕过访问限制");
    expect(screen.queryByTestId("tieba-post-8123456789-replies")).toBeNull();
  });

  it("仅帖链降级时标注归属未确认，并保留被剔除的他吧同名帖", () => {
    render(
      <TiebaResearchCard
        research={projection({
          status: "links_only",
          confirmed_posts: [],
          candidate_links: [
            {
              url: "https://tieba.baidu.com/p/7000000001",
              title: "宿舍条件汇总",
              source: "tavily",
            },
          ],
          rejected_candidates: [
            {
              url: "https://tieba.baidu.com/p/7000000002",
              title: "宿舍条件怎么样",
              evidence: "页面吧头显示「上海交通大学研究生吧」，非目标贴吧。",
            },
          ],
          evidence_boundary: ["页面读取被访问限制打断，只给出帖链，未取得任何回复内容。"],
        })}
        streaming={false}
      />
    );
    expect(screen.getByTestId("tieba-research-card-links_only")).toBeTruthy();
    expect(screen.getByTestId("tieba-research-degraded").textContent).toContain("仅帖链");
    const links = screen.getByTestId("tieba-candidate-links");
    expect(links.textContent).toContain("宿舍条件汇总");
    expect(links.textContent).toContain("归属未确认");
    expect(screen.getByTestId("tieba-rejected-candidates").textContent).toContain(
      "上海交通大学研究生吧"
    );
    expect(screen.getByTestId("tieba-research-notes").textContent).toContain(
      "未取得任何回复内容"
    );
  });

  it("官方核验与吧友经历分列，未取得官方页面时如实说明", () => {
    const { unmount } = render(
      <TiebaResearchCard
        research={projection({
          official_check_requested: true,
          official_checks: [
            {
              title: "本科生转专业管理办法",
              url: "https://jwc.ecjtu.edu.cn/info/1234.htm",
              host: "jwc.ecjtu.edu.cn",
              fetched_at: "2026-09-25T02:00:04Z",
              status: "verified",
              excerpt: "学生转专业须在第二学期末提出申请。",
              matched_terms: ["转专业"],
              error_code: null,
              error_message: null,
            },
          ],
        })}
        streaming={false}
      />
    );
    const checks = screen.getByTestId("tieba-official-checks");
    expect(checks.textContent).toContain("jwc.ecjtu.edu.cn");
    expect(checks.textContent).toContain("已定位相关段落");
    expect(checks.textContent).toContain("本科生转专业管理办法");
    expect(screen.getByTestId("tieba-official-jwc.ecjtu.edu.cn-excerpt").textContent).toContain(
      "第二学期末"
    );
    unmount();

    render(
      <TiebaResearchCard
        research={projection({ official_check_requested: true, official_checks: [] })}
        streaming={false}
      />
    );
    expect(screen.getByTestId("tieba-research-card-success").textContent).toContain(
      "本轮未取得学校官方页面"
    );
  });

  it("澄清状态呈现待答问题", () => {
    render(
      <TiebaResearchCard
        research={projection({
          status: "clarification",
          pending: {
            module_id: "tieba",
            kind: "clarification",
            question: "你想查华东交通大学吧里的哪个话题？",
            origin_message_id: "a-1",
            context: {},
            created_at: "2026-09-25T02:00:00Z",
          },
        })}
        streaming={false}
      />
    );
    expect(screen.getByTestId("tieba-research-clarification").textContent).toContain(
      "你想查华东交通大学吧里的哪个话题？"
    );
  });

  it("失败状态如实呈现错误与错误码，可按 retryable 重试", () => {
    const onRetry = vi.fn();
    render(
      <TiebaResearchCard
        research={projection({
          status: "error",
          retryable: true,
          error_code: "tieba_search_timeout",
          error_message: "搜索服务超时，请稍后重试。",
        })}
        streaming={false}
        onRetry={onRetry}
      />
    );
    const card = screen.getByTestId("tieba-research-card-error");
    expect(card.getAttribute("role")).toBe("alert");
    expect(card.textContent).toContain("搜索服务超时，请稍后重试。");
    expect(card.textContent).toContain("tieba_search_timeout");
    screen.getByTestId("tieba-research-retry").click();
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("停止状态如实呈现，且没有可重试按钮", () => {
    render(
      <TiebaResearchCard
        research={projection({ status: "stopped", retryable: false })}
        streaming={false}
      />
    );
    expect(screen.getByTestId("tieba-research-card-stopped").textContent).toContain(
      "已停止贴吧检索"
    );
    expect(screen.queryByTestId("tieba-research-retry")).toBeNull();
  });

  it("没有任何投影时不渲染", () => {
    const { container } = render(<TiebaResearchCard research={null} streaming={false} />);
    expect(container.innerHTML).toBe("");
  });
});
