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
    question_kind: "experience",
    parallel_evidence: false,
    topic: "宿舍条件",
    original_question: "华东交通大学吧里最近说的宿舍条件怎么样",
    topic_terms: ["宿舍", "条件"],
    place_or_event: [],
    time_filter: {
      requirement: "最近",
      year: null,
      applied: false,
      recent_count: 0,
      historical_count: 0,
      unknown_count: 0,
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
  scroll_rounds: 0,
  scroll_rounds_limit: 0,
  total_pages: 3,
  floor_min: 1,
  floor_max: 12,
  replies_obtained: true,
  replies: [
    {
      floor: 3,
      temporal_scope: "unknown",
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
  it("显示模型总结的可点击引用并默认折叠原文", () => {
    render(<TiebaResearchCard research={projection({
      summary: "部分回复提到晚间热水供应不稳定。",
      summary_note: "另有历史发言仅供上下文，未作为总结依据。",
      summary_claims: [{
        kind: "finding",
        text: "部分回复提到晚间热水供应不稳定。",
        citation_ids: ["E1"],
      }],
      summary_citations: [{
        citation_id: "E1", url: "https://tieba.baidu.com/p/123", title: "宿舍讨论",
        reply_id: "reply-3", parent_reply_id: null, floor: 3,
        posted_at: "2026-09-01", quote: "晚上热水供应不太稳定。",
      }],
    })} streaming={false} />);
    expect(screen.getByTestId("tieba-research-summary")).toBeTruthy();
    expect(screen.getByTestId("tieba-summary-note").textContent).toContain("仅供上下文");
    expect(screen.getByRole("link", { name: "查看引用 E1" }).getAttribute("href")).toBe("#tieba-citation-E1");
    expect(screen.getByText("晚上热水供应不太稳定。").closest("details")?.open).toBe(false);
  });

  it("认证等待不显示手动打开或完成登录的按钮", () => {
    render(<TiebaResearchCard research={projection({
      status: "waiting_auth",
      pending: {
        module_id: "tieba", kind: "authentication", question: "登录后说继续。",
        origin_message_id: "assistant-1", context: { original_question: "宿舍条件", remaining_work_ms: 200000 },
        created_at: "2026-10-11T00:00:00Z",
      },
    })} streaming={false} />);
    expect(screen.getByTestId("tieba-research-card-waiting_auth")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /打开专用贴吧浏览器|保存并重新检索/ })).toBeNull();
  });

  it("部分回复限制默认可见，完整原文默认折叠", () => {
    render(<TiebaResearchCard research={projection({ confirmed_posts: [{
      ...confirmedPost, read_status: "partial", read_error_message: "未穷尽全部回复。",
    }] })} streaming={false} />);
    const coverage = screen.getByTestId("tieba-research-coverage");
    expect(coverage.textContent).toContain("未穷尽全部回复。");
    expect(coverage.closest("details")).toBeNull();
    expect(screen.getByTestId(`tieba-post-${confirmedPost.thread_id}-replies`).closest("details")?.open).toBe(false);
  });

  it("未取得讨论时交付官方通知的具体安排，并说明公开索引的限制", () => {
    render(<TiebaResearchCard research={projection({
      status: "empty", official_check_requested: true,
      official_checks: [{
        title: "2026年国庆节图书馆开放安排", host: "lib.ecjtu.edu.cn",
        url: "https://lib.ecjtu.edu.cn/info/1076/7501.htm",
        fetched_at: "2026-10-07T12:47:35Z", status: "verified",
        excerpt: "根据学校通知，开放时间安排如下：\n1.10月1日至3日，8:00-17:00。",
        matched_terms: ["国庆节"], applicability: {
          subject: "图书馆", subject_confirmed: true, purpose_terms: ["国庆节"],
          purpose_confirmed: true, applicable: true, note: "仅为图书馆开放安排。",
        },
      }],
    })} streaming={false} />);
    expect(screen.getByText("已取得官方信息，贴吧讨论尚未取得")).toBeTruthy();
    expect(screen.getByTestId("tieba-official-lib.ecjtu.edu.cn-excerpt").textContent)
      .toContain("10月1日至3日，8:00-17:00");
    expect(screen.getByText(/未召回帖子不代表吧内没有相关讨论/)).toBeTruthy();
  });
  it("压缩展示仍保留未核实提示和官方与帖子的冲突", () => {
    render(<TiebaResearchCard research={projection({
      official_unverified_note: "官方规定仍未核实。",
      conflicts: [{
        topic_terms: ["国庆节"], official_title: "国庆节通知",
        official_url: "https://www.ecjtu.edu.cn/info/1.htm",
        official_statement: "假期安排以学校通知为准。",
        post_refs: [{ url: "https://tieba.baidu.com/p/1", floor: 2,
          posted_at: "2025-10-01", quote: "我去年提前回家了。" }],
        time_basis: "年份不同。", scope_basis: "个人经历不等于学校规定。",
        resolution: "kept_both", note: "保留冲突，不归纳为共同结论。",
      }],
    })} streaming={false} />);
    expect(screen.getByText("官方规定仍未核实。").closest("details")).toBeNull();
    const conflict = screen.getByTestId("tieba-research-conflicts");
    expect(conflict.textContent).toContain("我去年提前回家了。");
    expect(conflict.textContent).toContain("保留冲突，不归纳为共同结论。");
    expect(conflict.closest("details")).toBeNull();
  });
  it("固定来源入口可见，查询与剔除记录默认折叠", () => {
    render(<TiebaResearchCard research={projection({
      rejected_candidates: [{ url: "https://www.douyin.com/video/123", title: "无关视频", evidence: "站外" }],
      evidence_boundary: ["仅核对目标贴吧"],
    })} streaming={false} />);
    const entry = screen.getByRole("link", { name: "华东交通大学吧" });
    expect(entry.getAttribute("href")).toBe(
      "https://tieba.baidu.com/f?kw=%E5%8D%8E%E4%B8%9C%E4%BA%A4%E9%80%9A%E5%A4%A7%E5%AD%A6"
    );
    for (const testId of ["tieba-research-queries", "tieba-rejected-candidates", "tieba-research-notes"]) {
      const details = screen.getByTestId(testId).closest("details");
      expect(details).not.toBeNull();
      expect(details?.hasAttribute("open")).toBe(false);
    }
  });
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

  it("仅帖链降级时逐条标注未确认原因，并保留被剔除的候选依据", () => {
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
              unconfirmed_reason: "页面不可读：访问受限，未绕过",
            },
          ],
          rejected_candidates: [
            {
              url: "https://tieba.baidu.com/p/7000000002",
              title: "宿舍条件怎么样",
              evidence: "页面吧头显示「上海交通大学研究生吧」，非目标贴吧。",
            },
            {
              url: "https://nani.baidu.com/home/main?id=tb.1.ce8f0206",
              title: "华东交大小益君的贴吧",
              evidence: "搜索结果不是帖子页面链接",
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
    // 帖链逐条带出「为什么只有帖链」：来源、具体原因与归属未确认分列。
    expect(links.textContent).toContain("tavily");
    expect(links.textContent).toContain("页面不可读：访问受限");
    expect(links.textContent).toContain("归属未确认");
    const rejected = screen.getByTestId("tieba-rejected-candidates");
    expect(rejected.textContent).toContain("上海交通大学研究生吧");
    expect(rejected.textContent).toContain("搜索结果不是帖子页面链接");
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
