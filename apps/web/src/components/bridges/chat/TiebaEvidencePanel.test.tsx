import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  TiebaOfficialCheck,
  TiebaPostProjection,
  TiebaResearchProjection,
  TiebaSummaryCitation,
} from "@/lib/api";

import { TiebaEvidencePanel } from "./TiebaEvidencePanel";

afterEach(cleanup);

const citation: TiebaSummaryCitation = {
  citation_id: "E1",
  url: "https://tieba.baidu.com/p/8123456789",
  title: "南区宿舍到底怎么样",
  reply_id: "reply-3",
  parent_reply_id: null,
  floor: 3,
  posted_at: "2026-09-20 21:14",
  quote: "南区六人间，有空调，热水要刷卡。",
};

const post: TiebaPostProjection = {
  thread_id: "8123456789",
  url: "https://tieba.baidu.com/p/8123456789",
  title: "南区宿舍到底怎么样",
  affiliation_evidence: "页面吧头显示「华东交通大学吧」。",
  read_status: "partial",
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
      reply_id: "reply-3",
      parent_reply_id: null,
      temporal_scope: "unknown",
      posted_at: "2026-09-20 21:14",
      is_original_poster: false,
      content: "南区六人间，有空调，热水要刷卡。",
    },
    {
      floor: 4,
      reply_id: "reply-4",
      parent_reply_id: null,
      temporal_scope: "unknown",
      posted_at: "2026-09-20 22:10",
      is_original_poster: false,
      content: "热水晚上十一点后就没了。",
    },
  ],
  read_error_code: null,
  read_error_message: "本轮只读取到部分楼层。",
  retrieved_at: "2026-09-25T02:00:02Z",
};

const officialCheck: TiebaOfficialCheck = {
  title: "华东交通大学学生住宿费管理办法",
  url: "https://jwc.ecjtu.edu.cn/info/1004/3042.htm",
  host: "jwc.ecjtu.edu.cn",
  fetched_at: "2026-10-19T02:00:02Z",
  status: "verified",
  excerpt: "住宿费按学年收取，具体标准按学校规定执行。",
  matched_terms: ["住宿费", "规定"],
  applicability: {
    subject: "jwc.ecjtu.edu.cn（学校官方域名页面）",
    subject_confirmed: true,
    campus: null,
    campus_confirmed: null,
    purpose_terms: ["住宿费", "规定"],
    purpose_confirmed: true,
    date_requirement: null,
    date_confirmed: null,
    applicable: true,
    note: "页面说明了本校或主管部门主体。已定位到相关规定用途。",
  },
};

function officialResearch(
  overrides: Partial<TiebaResearchProjection> = {}
): TiebaResearchProjection {
  return {
    official_checks: [officialCheck],
    official_check_requested: true,
    official_unverified_note: null,
    official_blocked_reason: null,
    conflicts: [],
    ...overrides,
  } as unknown as TiebaResearchProjection;
}

function renderPanel(
  options: {
    citation?: TiebaSummaryCitation;
    post?: TiebaPostProjection | null;
    research?: TiebaResearchProjection | null;
  } = {}
) {
  const onClose = vi.fn();
  render(
    <TiebaEvidencePanel
      selection={{
        conversationId: "c-1",
        messageId: "a-1",
        citation: options.citation ?? citation,
        post: options.post === undefined ? post : options.post,
        research: options.research ?? null,
      }}
      onClose={onClose}
    />
  );
  return onClose;
}

describe("贴吧引用右侧资料面板（工单 10）", () => {
  it("展示引用楼层、原文、读取范围与帖子回复，并标出当前引用", () => {
    renderPanel();

    expect(screen.getByTestId("tieba-evidence-panel")).toBeTruthy();
    expect(screen.getByTestId("tieba-evidence-location").textContent).toContain(
      "第 3 楼"
    );
    expect(screen.getByTestId("tieba-evidence-location").textContent).toContain(
      "2026-09-20 21:14"
    );
    expect(screen.getByTestId("tieba-evidence-quote").textContent).toContain(
      "南区六人间"
    );
    const scope = screen.getByTestId("tieba-evidence-scope");
    expect(scope.textContent).toContain("已读 1/2 页");
    expect(scope.textContent).toContain("第 1–12 楼");
    expect(scope.textContent).toContain("本轮只读取到部分楼层");
    expect(screen.getByTestId("tieba-evidence-reply-3").getAttribute("data-active")).toBe(
      "true"
    );
    expect(screen.getByTestId("tieba-evidence-reply-4").getAttribute("data-active")).toBeNull();
    expect(screen.getByTestId("tieba-evidence-replies").textContent).toContain(
      "热水晚上十一点后就没了"
    );
    expect(screen.getByTestId("tieba-evidence-open").getAttribute("href")).toBe(
      "https://tieba.baidu.com/p/8123456789"
    );
  });

  it("没有楼层定位时诚实说明只能定位到帖子", () => {
    renderPanel({
      citation: { ...citation, floor: null, reply_id: null },
    });

    expect(screen.getByTestId("tieba-evidence-no-location").textContent).toContain(
      "只能定位到帖子"
    );
  });

  it("引用楼层不在已读原文时说明无法展开，不编造楼层内容", () => {
    renderPanel({ citation: { ...citation, floor: 9 } });

    expect(screen.getByTestId("tieba-evidence-location-missing").textContent).toContain(
      "第 9 楼"
    );
    expect(screen.queryByTestId("tieba-evidence-reply-9")).toBeNull();
    expect(screen.getByTestId("tieba-evidence-replies").textContent).toContain(
      "南区六人间"
    );
  });

  it("没有帖子投影时只保留引用摘录并说明缺少读取范围", () => {
    renderPanel({ post: null });

    expect(screen.getByTestId("tieba-evidence-no-scope").textContent).toContain(
      "没有提供对应帖子的读取范围"
    );
    expect(screen.getByTestId("tieba-evidence-no-replies").textContent).toContain(
      "不能补写回复内容"
    );
  });

  it("引用详情同时展示吧友原文与学校官方依据", () => {
    renderPanel({ research: officialResearch() });

    expect(screen.getByTestId("tieba-evidence-quote").textContent).toContain(
      "南区六人间"
    );
    const official = screen.getByTestId("tieba-evidence-official");
    expect(official.textContent).toContain("学校官方依据");
    expect(official.textContent).toContain("华东交通大学学生住宿费管理办法");
    expect(official.textContent).toContain("住宿费按学年收取");
    expect(official.textContent).toContain("适用性核对");
    expect(
      screen.getByTestId("tieba-official-jwc.ecjtu.edu.cn-excerpt").textContent
    ).toContain("住宿费按学年收取");
  });

  it("官网未核实或失败时，引用详情保留说明与贴吧原文", () => {
    renderPanel({
      research: officialResearch({
        official_checks: [],
        official_unverified_note:
          "本轮未取得可适用的学校官方原文，官方规定保持未核实；吧友说法不构成官方结论。",
      }),
    });

    const official = screen.getByTestId("tieba-evidence-official");
    expect(official.textContent).toContain("本轮未取得学校官方页面");
    expect(official.textContent).toContain("官方规定保持未核实");
    expect(screen.getByTestId("tieba-evidence-quote").textContent).toContain(
      "南区六人间"
    );
  });

  it("Esc 与关闭按钮都能关闭并归还控制", () => {
    const onClose = renderPanel();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);

    cleanup();
    const onCloseAgain = renderPanel();
    fireEvent.click(screen.getByTestId("tieba-evidence-close"));
    expect(onCloseAgain).toHaveBeenCalledTimes(1);
  });
});
