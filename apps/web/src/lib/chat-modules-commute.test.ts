import { describe, expect, it } from "vitest";
import { CHAT_MODULES, chatModuleIcon, chatModuleLabel, hasPendingCommuteClarification } from "./chat-modules";
import { commuteRoute, message, commuteMessage } from "./chat-modules.test-support";

describe("chat-modules 校园通勤（V2 Issue 12）", () => {
  it("校园通勤在菜单里给中文名与路线图标", () => {
    expect(chatModuleLabel("commute")).toBe("校园通勤");
    expect(chatModuleIcon("commute")).toBe("route");
    expect(CHAT_MODULES.map((module) => module.id)).toEqual([
      "paper",
      "commute",
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
