import { describe, expect, it } from "vitest";

import {
  CHAT_MODULES,
  chatModuleIcon,
  chatModuleLabel,
  hasPendingCommuteClarification,
  pendingClarificationModule,
} from "./chat-modules";
import type {
  ChatMessageProjection,
  CommuteRouteProjection,
  LearningResourcesProjection,
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

/** 资料推荐投影（V2 Issue 13）：同样只填本测试关心的字段。 */
function learningResources(
  overrides: Partial<LearningResourcesProjection> = {}
): LearningResourcesProjection {
  return {
    status: "success",
    original_phrase: "深度学习",
    normalized_term: "深度学习",
    expansions: ["deep learning"],
    confidence: 0.7,
    goal: null,
    level_label: "零基础入门",
    level_basis: "你说了「零基础」",
    queries: [],
    final_query: "深度学习 deep learning",
    items: [],
    requested_books: 2,
    requested_videos: 3,
    evidence_notes: [],
    pending: null,
    searched_at: null,
    error_code: null,
    error_message: null,
    retryable: false,
    ...overrides,
  };
}

/** 通勤投影：只填本测试关心的字段，其余用真实的完整形态。 */
function commuteRoute(
  overrides: Partial<CommuteRouteProjection> = {}
): CommuteRouteProjection {
  return {
    status: "success",
    mode: "walking",
    mode_label: "步行",
    mode_phrase: null,
    origin: null,
    destination: null,
    origin_candidates: [],
    destination_candidates: [],
    distance_m: 1800,
    base_duration_seconds: 1400,
    suggested_total_seconds: 1400,
    steps: [],
    polyline: [],
    path_verified: false,
    buffer: null,
    queries: [],
    evidence_notes: [],
    pending: null,
    resolved_at: null,
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
  tieba: TiebaResearchProjection | null = null,
  resources: LearningResourcesProjection | null = null
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
    learning_resources: resources,
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

/** 通勤历史消息投影（模块标识随用户消息持久化）。 */
function commuteMessage(
  id: string,
  route: CommuteRouteProjection | null
): ChatMessageProjection {
  return {
    ...message(id, null),
    commute_route: route,
  };
}

const pendingPaperClarification: PaperSearchProjection = paperSearch({
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

const pendingResourcesClarification: LearningResourcesProjection = learningResources({
  status: "clarification",
  pending: {
    module_id: "resources",
    kind: "clarification",
    question: "你现在的学习层次是哪一档？",
    origin_message_id: "a-2",
    context: {},
    created_at: "2026-09-20T10:00:00Z",
  },
});

describe("chat-modules（V2 Issue 11/13）", () => {
  it("已接入模块给中文名与图标，未接入模块不回显英文 ID", () => {
    expect(chatModuleLabel("paper")).toBe("论文搜索");
    expect(chatModuleIcon("paper")).toBe("paperSearch");
    expect(chatModuleLabel("resources")).toBe("学习资料推荐");
    expect(chatModuleIcon("resources")).toBe("learningProject");
    // 未接入模块没有中文名：宁可不显示标签，也不在中文界面回显英文枚举值。
    expect(chatModuleLabel("github")).toBeNull();
    expect(chatModuleIcon("github")).toBe("chatBubble");
    expect(chatModuleLabel(null)).toBeNull();
  });

  it("菜单里四个已接入模块的 ID 与后端枚举一致", () => {
    expect(CHAT_MODULES.map((module) => module.id)).toEqual([
      "paper",
      "commute",
      "resources",
      "tieba",
    ]);
  });

  it("最后一条论文消息还在等澄清时恢复论文模块选择", () => {
    expect(pendingClarificationModule([message("m-1", pendingPaperClarification)])).toBe(
      "paper"
    );
  });

  it("最后一条资料消息还在等澄清时恢复资料模块选择", () => {
    expect(
      pendingClarificationModule([message("m-1", null, null, pendingResourcesClarification)])
    ).toBe("resources");
  });

  it("两个模块都留过等待时只恢复更晚的那个（不误恢复陈旧等待）", () => {
    expect(
      pendingClarificationModule([
        message("m-1", pendingPaperClarification),
        message("m-2", null, null, pendingResourcesClarification),
      ])
    ).toBe("resources");
  });

  it("已有更晚的完成结果时不恢复（等待状态已被取代）", () => {
    expect(
      pendingClarificationModule([
        message("m-1", pendingPaperClarification),
        message("m-2", paperSearch({ status: "success" })),
      ])
    ).toBeNull();
    expect(
      pendingClarificationModule([
        message("m-1", null, null, pendingResourcesClarification),
        message("m-2", null, null, learningResources({ status: "empty" })),
      ])
    ).toBeNull();
  });

  it("普通聊天历史不恢复任何模块", () => {
    expect(
      pendingClarificationModule([message("m-1", null), message("m-2", null)])
    ).toBeNull();
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
    expect(pendingClarificationModule([message("m-1", pendingPaperClarification, null)])).toBe(
      "paper"
    );
  });

  it("贴吧已有结论时不恢复：success / links_only / empty 都算本轮结束", () => {
    for (const status of ["success", "links_only", "empty"] as const) {
      expect(
        pendingClarificationModule([message("m-1", null, tiebaResearch({ status }))])
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

describe("chat-modules 校园通勤（V2 Issue 12）", () => {
  it("校园通勤在菜单里给中文名与路线图标", () => {
    expect(chatModuleLabel("commute")).toBe("校园通勤");
    expect(chatModuleIcon("commute")).toBe("route");
    expect(CHAT_MODULES.map((module) => module.id)).toEqual([
      "paper",
      "commute",
      "resources",
      "tieba",
    ]);
  });

  it("最后一条通勤消息还在等澄清时恢复模块选择", () => {
    expect(
      hasPendingCommuteClarification([
        commuteMessage(
          "m-1",
          commuteRoute({
            status: "clarification",
            pending: {
              module_id: "commute",
              kind: "clarification",
              question: "「图书馆」在高德匹配到多个地点，你要从哪一个出发？",
              origin_message_id: "a-1",
              context: {},
              created_at: "2026-09-25T01:40:00Z",
            },
          })
        ),
      ])
    ).toBe(true);
  });

  it("已有更晚的通勤结果时不恢复（等待状态已被取代）", () => {
    expect(
      hasPendingCommuteClarification([
        commuteMessage(
          "m-1",
          commuteRoute({
            status: "clarification",
            pending: {
              module_id: "commute",
              kind: "clarification",
              question: "请补充终点。",
              origin_message_id: "a-1",
              context: {},
              created_at: "2026-09-25T01:40:00Z",
            },
          })
        ),
        commuteMessage("m-2", commuteRoute({ status: "success" })),
      ])
    ).toBe(false);
  });

  it("失败的等待状态不恢复（错误不是等待用户回答）", () => {
    expect(
      hasPendingCommuteClarification([
        commuteMessage(
          "m-1",
          commuteRoute({ status: "error", error_code: "amap_not_configured" })
        ),
      ])
    ).toBe(false);
  });

  it("普通聊天历史不恢复通勤模块", () => {
    expect(
      hasPendingCommuteClarification([message("m-1", null), commuteMessage("m-2", null)])
    ).toBe(false);
  });
});
