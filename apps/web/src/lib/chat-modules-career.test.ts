import { describe, expect, it } from "vitest";
import { chatModuleIcon, chatModuleLabel, pendingClarificationModule } from "./chat-modules";
import { message, careerPlan, pendingPaperClarification } from "./chat-modules.test-support";

describe("chat-modules 职业规划（V2 Issue 15）", () => {
  it("职业规划在菜单里给中文名与岗位图标", () => {
    expect(chatModuleLabel("career")).toBe("职业规划");
    expect(chatModuleIcon("career")).toBe("careerPlan");
  });

  it("最后一条职业规划消息还在等澄清时恢复模块选择", () => {
    const pending = careerPlan({
      status: "clarification",
      pending: {
        module_id: "career",
        kind: "clarification",
        question: "你想找的是哪个岗位方向？",
        origin_message_id: "a-1",
        context: {},
        created_at: "2026-09-26T02:00:00Z",
      },
    });
    expect(pendingClarificationModule([message("m-1", null, null, null, pending)])).toBe(
      "career"
    );
    // 其他模块的历史判定不受职业规划等待影响（互不冒充）。
    expect(pendingClarificationModule([message("m-1", pendingPaperClarification)])).toBe(
      "paper"
    );
  });

  it("职业规划已有结论时不恢复：success / links_only / empty 都算本轮结束", () => {
    for (const status of ["success", "links_only", "empty"] as const) {
      expect(
        pendingClarificationModule([message("m-1", null, null, null, careerPlan({ status }))])
      ).toBeNull();
    }
  });

  it("职业规划失败时不冒领更早的等待状态", () => {
    const pending = careerPlan({
      status: "clarification",
      pending: {
        module_id: "career",
        kind: "clarification",
        question: "你想找的是哪个岗位方向？",
        origin_message_id: "a-1",
        context: {},
        created_at: "2026-09-26T02:00:00Z",
      },
    });
    // 失败是「本轮结束了」，不再把更早那条澄清当成待续问题。
    expect(
      pendingClarificationModule([
        message("m-1", null, null, null, pending),
        message("m-2", null, null, null, careerPlan({ status: "error" })),
      ])
    ).toBeNull();
  });
});
