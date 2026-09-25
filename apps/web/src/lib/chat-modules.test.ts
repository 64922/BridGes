import { describe, expect, it } from "vitest";

import {
  chatModuleIcon,
  chatModuleLabel,
  pendingClarificationModule,
} from "./chat-modules";
import type {
  ChatMessageProjection,
  PaperSearchProjection,
  TiebaResearchProjection,
} from "@/lib/api";

/** 论文投影：只填本测试关心的字段，其余用真实的完整形态。 */
function paperSearch(overrides: Partial<PaperSearchProjection> = {}): PaperSearchProjection {
  return {
    status: "success",
    original_phrase: "Transformer",
    normalized_term: "Transformer",
    expansions: [],
    confidence: 0.9,
    context_label: null,
    queries: [],
    final_query: "transformer",
    papers: [],
    requested_count: 3,
    evidence_notes: [],
    pending: null,
    searched_at: null,
    error_code: null,
    error_message: null,
    retryable: false,
    ...overrides,
  };
}

/** 历史消息投影：只填本测试关心的字段。 */
function message(
  id: string,
  search: PaperSearchProjection | null,
  tieba: TiebaResearchProjection | null = null
): ChatMessageProjection {
  return {
    message_id: id,
    conversation_id: "conversation-1",
    role: "assistant",
    attempt_number: 1,
    status: "done",
    content: "回答",
    paper_search: search,
    tieba_research: tieba,
    created_at: "2026-09-20T10:00:00Z",
    updated_at: "2026-09-20T10:00:01Z",
  };
}

/** 贴吧投影：只填本测试关心的字段。 */
function tiebaResearch(
  overrides: Partial<TiebaResearchProjection> = {}
): TiebaResearchProjection {
  return {
    status: "success",
    topic: "宿舍条件",
    original_question: "华东交通大学吧里最近的宿舍条件怎么样",
    topic_terms: ["宿舍", "条件"],
    place_or_event: [],
    time_filter: { requirement: null, year: null, applied: false, note: "未提出时间条件。" },
    queries: [],
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
    completed_at: null,
    pending: null,
    ...overrides,
  };
}

const pendingClarification: PaperSearchProjection = paperSearch({
  status: "clarification",
  pending: {
    module_id: "paper",
    kind: "clarification",
    question: "你想找哪一个？",
    origin_message_id: "a-1",
    context: {},
    created_at: "2026-09-20T10:00:00Z",
  },
});

describe("chat-modules（V2 Issue 11）", () => {
  it("已接入模块给中文名与图标，未接入模块不回显英文 ID", () => {
    expect(chatModuleLabel("paper")).toBe("论文搜索");
    expect(chatModuleIcon("paper")).toBe("paperSearch");
    // 未接入模块没有中文名：宁可不显示标签，也不在中文界面回显英文枚举值。
    expect(chatModuleLabel("github")).toBeNull();
    expect(chatModuleIcon("github")).toBe("chatBubble");
    expect(chatModuleLabel(null)).toBeNull();
  });

  it("最后一条论文消息还在等澄清时恢复模块选择", () => {
    expect(pendingClarificationModule([message("m-1", pendingClarification)])).toBe("paper");
  });

  it("已有更晚的完成结果时不恢复（等待状态已被取代）", () => {
    expect(
      pendingClarificationModule([
        message("m-1", pendingClarification),
        message("m-2", paperSearch({ status: "success" })),
      ])
    ).toBeNull();
  });

  it("普通聊天历史不恢复任何模块", () => {
    expect(pendingClarificationModule([message("m-1", null), message("m-2", null)])).toBeNull();
    expect(pendingClarificationModule([message("m-1", null)])).toBeNull();
  });
});

describe("chat-modules（V2 Issue 14 贴吧）", () => {
  it("贴吧模块给中文名与图标", () => {
    expect(chatModuleLabel("tieba")).toBe("贴吧信息搜集");
    expect(chatModuleIcon("tieba")).toBe("tiebaThread");
  });

  it("最后一条贴吧消息还在等澄清时恢复贴吧模块选择", () => {
    const pending = tiebaResearch({
      status: "clarification",
      pending: {
        module_id: "tieba",
        kind: "clarification",
        question: "你想查华东交通大学吧里的哪个话题？",
        origin_message_id: "a-1",
        context: {},
        created_at: "2026-09-25T02:00:00Z",
      },
    });
    expect(pendingClarificationModule([message("m-1", null, pending)])).toBe("tieba");
    // 论文的历史判定不受贴吧等待影响（两者互不冒充）。
    expect(pendingClarificationModule([message("m-1", pendingClarification, null)])).toBe(
      "paper"
    );
  });

  it("贴吧已有结论时不恢复：success / links_only / empty 都算本轮结束", () => {
    for (const status of ["success", "links_only", "empty"] as const) {
      expect(
        pendingClarificationModule([
          message("m-1", null, tiebaResearch({ status })),
        ])
      ).toBeNull();
    }
  });

  it("本轮检索失败或停止时不冒领更早的等待状态", () => {
    const pending = tiebaResearch({
      status: "clarification",
      pending: {
        module_id: "tieba",
        kind: "clarification",
        question: "你想查哪个话题？",
        origin_message_id: "a-1",
        context: {},
        created_at: "2026-09-25T02:00:00Z",
      },
    });
    // 失败/停止是「本轮结束了」，不再把更早那条澄清当成待续问题（否则下次
    // 发言会被悄悄派发到贴吧模块）。
    expect(
      pendingClarificationModule([
        message("m-1", null, pending),
        message("m-2", null, tiebaResearch({ status: "error" })),
      ])
    ).toBeNull();
  });
});
