import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TiebaPostProjection, TiebaSummaryCitation } from "@/lib/api";
import { tiebaResearch } from "@/lib/chat-modules.test-support";

import { TiebaAnswer } from "./TiebaAnswer";

afterEach(cleanup);

const citation: TiebaSummaryCitation = {
  citation_id: "E1",
  url: "https://tieba.baidu.com/p/8123456789",
  title: "南区宿舍到底怎么样",
  reply_id: "reply-3",
  parent_reply_id: null,
  floor: 3,
  posted_at: "2026-09-20 21:14",
  author: "吧友甲",
  nested: false,
  quote: "南区六人间，有空调，热水要刷卡。",
};

const post: TiebaPostProjection = {
  thread_id: "8123456789",
  url: "https://tieba.baidu.com/p/8123456789",
  title: "南区宿舍到底怎么样",
  affiliation_evidence: "页面吧头显示「华东交通大学吧」。",
  read_status: "read",
  pages_read: 1,
  pages_limit: 2,
  pages_complete: false,
  scroll_rounds: 0,
  scroll_rounds_limit: 0,
  total_pages: 3,
  floor_min: 1,
  floor_max: 12,
  replies_obtained: true,
  nested_complete: false,
  nested_incomplete_count: 0,
  replies: [
    {
      floor: 3,
      reply_id: "reply-3",
      parent_reply_id: null,
      temporal_scope: "unknown",
      posted_at: "2026-09-20 21:14",
      author: "吧友甲",
      is_original_poster: false,
      content: "南区六人间，有空调，热水要刷卡。",
      nested: false,
      nested_preview: false,
    },
  ],
  read_error_code: null,
  read_error_message: null,
  retrieved_at: "2026-09-25T02:00:02Z",
};

describe("贴吧回答正文与依据（工单 10）", () => {
  it("默认展示已核验总结，引用按钮打开对应楼层，不重复渲染服务端同一份正文", () => {
    const onOpenCitation = vi.fn();
    render(
      <TiebaAnswer
        research={tiebaResearch({
          summary: "部分回复提到晚间热水供应不稳定。",
          summary_citations: [citation],
          summary_claims: [
            {
              kind: "finding",
              text: "部分回复提到晚间热水供应不稳定。",
              citation_ids: ["E1"],
            },
          ],
          confirmed_posts: [post],
        })}
        streaming={false}
        fallbackText="服务端已提交正文（同一份总结）"
        onOpenCitation={onOpenCitation}
      />
    );

    expect(screen.getByTestId("tieba-summary").textContent).toContain(
      "晚间热水供应不稳定"
    );
    expect(screen.queryByText("服务端已提交正文（同一份总结）")).toBeNull();
    expect(screen.getByTestId("tieba-citation-E1").textContent).toContain("吧友甲");

    fireEvent.click(screen.getByTestId("tieba-citation-E1"));
    expect(onOpenCitation).toHaveBeenCalledTimes(1);
    expect(onOpenCitation.mock.calls[0][0]).toEqual(citation);
    expect(onOpenCitation.mock.calls[0][1]).toEqual(post);
  });

  it("只有帖链时明确未取得回复内容，并把搜索摘要与已读帖子分开", () => {
    render(
      <TiebaAnswer
        research={tiebaResearch({
          status: "links_only",
          candidate_links: [
            {
              url: "https://tieba.baidu.com/p/7000000001",
              title: "宿舍条件汇总",
              source: "tavily",
              unconfirmed_reason: "页面不可读：访问受限，未绕过",
            },
          ],
          evidence_boundary: ["页面读取被访问限制打断，只给出帖链。"],
        })}
        streaming={false}
        fallbackText="服务端正文"
      />
    );

    expect(screen.getByTestId("tieba-links-only").textContent).toContain(
      "未取得回复内容"
    );
    expect(screen.getByTestId("tieba-links-only").textContent).toContain(
      "搜索摘要不是已读帖子"
    );
    expect(screen.getByTestId("tieba-candidate-links").textContent).toContain(
      "归属未确认"
    );
    expect(screen.getByTestId("tieba-candidate-links").textContent).toContain(
      "页面不可读：访问受限，未绕过"
    );
    expect(screen.queryByText("服务端正文")).toBeNull();
  });

  it("局部回复读取失败保留已读总结，并标出未读边界", () => {
    const partialPost: TiebaPostProjection = {
      ...post,
      read_status: "partial",
      read_error_message: "本轮只读取到部分楼层。",
    };
    render(
      <TiebaAnswer
        research={tiebaResearch({
          summary: "已读楼层提到空调与热水刷卡。",
          summary_citations: [citation],
          confirmed_posts: [partialPost],
          evidence_boundary: ["未读楼层不进入结论。"],
        })}
        streaming={false}
      />
    );

    expect(screen.getByTestId("tieba-summary").textContent).toContain(
      "空调与热水刷卡"
    );
    const boundary = screen.getByTestId("tieba-read-boundary");
    expect(boundary.textContent).toContain("本轮只读取到部分楼层");
    expect(boundary.textContent).toContain("未读部分不进入结论");
    expect(screen.getByTestId("tieba-evidence-boundary").textContent).toContain(
      "未读楼层不进入结论"
    );
  });

  it("等待登录或安全验证时给出现有动作，不提供绕过入口", () => {
    render(
      <TiebaAnswer
        research={tiebaResearch({
          status: "waiting_auth",
          pending: {
            module_id: "tieba",
            kind: "authentication",
            question: "请完成专用贴吧会话的登录或安全验证。",
            origin_message_id: "a-1",
            context: {},
            created_at: "2026-10-11T00:00:00Z",
          },
        })}
        streaming={false}
      />
    );

    expect(screen.getByTestId("tieba-pending").textContent).toContain(
      "请完成专用贴吧会话的登录或安全验证"
    );
    expect(screen.getByTestId("tieba-pending").textContent).toContain(
      "回复“继续”"
    );
    expect(screen.getByTestId("tieba-pending").textContent).toContain(
      "不会读取系统浏览器凭据，也不会绕过验证"
    );
    expect(screen.queryByRole("button", { name: /打开专用贴吧浏览器|绕过/ })).toBeNull();
  });

  it("官方核验保留独立语义：有可适用原文才展示摘录，不适用时不说官方确认", () => {
    const { unmount } = render(
      <TiebaAnswer
        research={tiebaResearch({
          status: "empty",
          official_check_requested: true,
          official_checks: [
            {
              title: "2026年国庆节图书馆开放安排",
              host: "lib.ecjtu.edu.cn",
              url: "https://lib.ecjtu.edu.cn/info/1076/7501.htm",
              fetched_at: "2026-10-07T12:47:35Z",
              status: "verified",
              excerpt: "10月1日至3日，8:00-17:00。",
              matched_terms: ["国庆节"],
              applicability: {
                subject: "图书馆",
                subject_confirmed: true,
                purpose_terms: ["国庆节"],
                purpose_confirmed: true,
                applicable: true,
                note: "仅为图书馆开放安排。",
              },
            },
          ],
        })}
        streaming={false}
      />
    );

    expect(screen.getByTestId("tieba-official-checks").textContent).toContain(
      "10月1日至3日，8:00-17:00"
    );
    expect(screen.queryByText("官方确认")).toBeNull();
    unmount();

    render(
      <TiebaAnswer
        research={tiebaResearch({
          status: "empty",
          official_check_requested: true,
          official_checks: [
            {
              title: "某通知",
              host: "www.ecjtu.edu.cn",
              url: "https://www.ecjtu.edu.cn/info/1.htm",
              fetched_at: "2026-10-07T12:47:35Z",
              status: "verified",
              excerpt: "原文摘录。",
              matched_terms: [],
              applicability: {
                subject: "学校",
                subject_confirmed: true,
                purpose_terms: [],
                purpose_confirmed: false,
                applicable: false,
                note: "无法确认适用于本次问题。",
              },
            },
          ],
        })}
        streaming={false}
      />
    );

    expect(screen.getByTestId("tieba-official-not-applicable").textContent).toContain(
      "不显示为官方确认"
    );
  });

  it("官方未取得页面时明确保持未核实", () => {
    render(
      <TiebaAnswer
        research={tiebaResearch({
          official_check_requested: true,
          official_checks: [],
          summary_note: "本轮模型总结未生成。",
        })}
        streaming={false}
        fallbackText="服务端正文"
      />
    );

    expect(screen.getByTestId("tieba-official-unverified").textContent).toContain(
      "官方规定保持未核实"
    );
    expect(screen.getByTestId("tieba-summary-note").textContent).toContain(
      "本轮模型总结未生成"
    );
    expect(screen.getByTestId("tieba-fallback-body").textContent).toContain(
      "服务端正文"
    );
  });

  it("没有总结和摘录时仍以服务端已提交正文兜底，不留下空回答", () => {
    render(
      <TiebaAnswer
        research={tiebaResearch({ summary: null, summary_claims: [] })}
        streaming={false}
        fallbackText="本轮没有生成总结，但已读到的原文在这里。"
      />
    );

    expect(screen.getByTestId("tieba-fallback-body").textContent).toContain(
      "本轮没有生成总结"
    );
  });

  it("展示每轮检索目标与实际执行词，执行词不含动作词", () => {
    render(
      <TiebaAnswer
        research={tiebaResearch({
          status: "empty",
          topic_terms: ["宿舍"],
          query_rounds: [
            {
              goal: "原词查询",
              query: "site:tieba.baidu.com/p/ 华东交通大学吧 宿舍",
              execution_terms: "宿舍",
            },
            {
              goal: "常用说法查询",
              query: "site:tieba.baidu.com/p/ 华东交通大学吧 寝室",
              execution_terms: "寝室",
            },
          ],
          empty_reason: "本轮检索没有返回可确认属于「华东交通大学吧」的公开帖子。",
        })}
        streaming={false}
      />
    );

    const plan = screen.getByTestId("tieba-query-plan-rounds").textContent ?? "";
    expect(plan).toContain("原词查询：宿舍");
    expect(plan).toContain("常用说法查询：寝室");
    expect(plan).not.toContain("获取");
  });

  it("历史失败只在没有回合恢复投影时提供整轮重跑入口", () => {
    const onRetry = vi.fn();
    render(
      <TiebaAnswer
        research={tiebaResearch({
          status: "error",
          retryable: true,
          error_code: "tieba_search_timeout",
          error_message: "搜索服务超时，请稍后重试。",
        })}
        streaming={false}
        showRetry
        onRetry={onRetry}
      />
    );

    expect(screen.getByTestId("tieba-error").textContent).toContain(
      "搜索服务超时，请稍后重试。"
    );
    expect(screen.getByText(/重跑整轮对话/)).toBeTruthy();
    expect(screen.getByText(/不会只补缺失回复/)).toBeTruthy();
    fireEvent.click(screen.getByTestId("tieba-legacy-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
