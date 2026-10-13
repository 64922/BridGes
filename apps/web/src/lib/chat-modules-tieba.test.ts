import { describe, expect, it } from "vitest";
import { chatModuleIcon, chatModuleLabel, pendingClarificationModule } from "./chat-modules";
import { message, tiebaResearch, pendingPaperClarification } from "./chat-modules.test-support";

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
