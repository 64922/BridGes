import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { TiebaResearchProjection } from "@/lib/api";

import { TiebaEvidenceDetails } from "./TiebaEvidenceDetails";

afterEach(cleanup);

const research: TiebaResearchProjection = {
  status: "empty",
  topic: "空调",
  original_question: "华东交通大学吧 空调",
  question_kind: "experience",
  parallel_evidence: false,
  official_check_requested: false,
  retryable: false,
  forum: "华东交通大学吧",
  topic_terms: ["空调"],
  time_filter: {
    applied: false,
    recent_count: 0,
    historical_count: 0,
    unknown_count: 0,
    note: "本轮问题没有提出时间条件。",
  },
  search_scope: [
    {
      tab: "reply",
      query: "空调",
      pages_read: 2,
      pages_limit: 2,
      visited_pages: [1, 2],
      last_page: 2,
      has_more: true,
      stop_reason: "pages_limit",
      note: "仅读取前 2 页，页面声明还有后续结果。",
    },
  ],
  empty_reason: "本轮检索没有返回可确认属于目标贴吧的公开帖子。",
};

describe("贴吧搜索页范围（工单 05）", () => {
  it("展示回复页签读到的页码与未穷尽范围", () => {
    render(<TiebaEvidenceDetails research={research} />);

    const scope = screen.getByTestId("tieba-search-scope");
    expect(scope.textContent).toContain("回复");
    expect(scope.textContent).toContain("第 1、2 页");
    expect(scope.textContent).toContain("页面声明还有后续结果");
    expect(scope.textContent).toContain("达到翻页上限");
  });
});
