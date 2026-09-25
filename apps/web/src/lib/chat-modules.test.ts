import { describe, expect, it } from "vitest";

import {
  CHAT_MODULES,
  chatModuleIcon,
  chatModuleLabel,
  pendingClarificationModule,
} from "./chat-modules";
import type {
  ChatMessageProjection,
  LearningResourcesProjection,
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

/** 历史消息投影：只填本测试关心的字段。 */
function message(
  id: string,
  search: PaperSearchProjection | null,
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
    learning_resources: resources,
    created_at: "2026-09-20T10:00:00Z",
    updated_at: "2026-09-20T10:00:01Z",
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

  it("菜单里两个已接入模块的 ID 与后端枚举一致", () => {
    expect(CHAT_MODULES.map((module) => module.id)).toEqual(["paper", "resources"]);
  });

  it("最后一条论文消息还在等澄清时恢复论文模块选择", () => {
    expect(pendingClarificationModule([message("m-1", pendingPaperClarification)])).toBe(
      "paper"
    );
  });

  it("最后一条资料消息还在等澄清时恢复资料模块选择", () => {
    expect(
      pendingClarificationModule([message("m-1", null, pendingResourcesClarification)])
    ).toBe("resources");
  });

  it("两个模块都留过等待时只恢复更晚的那个（不误恢复陈旧等待）", () => {
    expect(
      pendingClarificationModule([
        message("m-1", pendingPaperClarification),
        message("m-2", null, pendingResourcesClarification),
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
        message("m-1", null, pendingResourcesClarification),
        message("m-2", null, learningResources({ status: "empty" })),
      ])
    ).toBeNull();
  });

  it("普通聊天历史不恢复任何模块", () => {
    expect(
      pendingClarificationModule([message("m-1", null), message("m-2", null)])
    ).toBeNull();
  });
});
