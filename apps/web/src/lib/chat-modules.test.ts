import { describe, expect, it } from "vitest";

import {
  CHAT_MODULES,
  chatModuleIcon,
  chatModuleLabel,
  hasPendingCommuteClarification,
  hasPendingPaperClarification,
} from "./chat-modules";
import type {
  ChatMessageProjection,
  CommuteRouteProjection,
  PaperSearchProjection,
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
  search: PaperSearchProjection | null
): ChatMessageProjection {
  return {
    message_id: id,
    conversation_id: "conversation-1",
    role: "assistant",
    attempt_number: 1,
    status: "done",
    content: "回答",
    paper_search: search,
    created_at: "2026-09-20T10:00:00Z",
    updated_at: "2026-09-20T10:00:01Z",
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
    expect(hasPendingPaperClarification([message("m-1", pendingClarification)])).toBe(true);
  });

  it("已有更晚的完成结果时不恢复（等待状态已被取代）", () => {
    expect(
      hasPendingPaperClarification([
        message("m-1", pendingClarification),
        message("m-2", paperSearch({ status: "success" })),
      ])
    ).toBe(false);
  });

  it("普通聊天历史不恢复任何模块", () => {
    expect(hasPendingPaperClarification([message("m-1", null), message("m-2", null)])).toBe(
      false
    );
  });
});

describe("chat-modules 校园通勤（V2 Issue 12）", () => {
  it("校园通勤在菜单里给中文名与路线图标", () => {
    expect(chatModuleLabel("commute")).toBe("校园通勤");
    expect(chatModuleIcon("commute")).toBe("route");
    expect(CHAT_MODULES.map((module) => module.id)).toEqual(["paper", "commute"]);
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
