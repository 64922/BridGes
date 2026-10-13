import { describe, expect, it } from "vitest";
import { CHAT_MODULES, chatModuleIcon, chatModuleLabel, pendingClarificationModule } from "./chat-modules";
import { paperSearch, learningResources, message, pendingPaperClarification, pendingResourcesClarification } from "./chat-modules.test-support";

describe("chat-modules（V2 Issue 11/13）", () => {
  it("已接入模块给中文名与图标，未接入模块不回显英文 ID", () => {
    expect(chatModuleLabel("paper")).toBe("论文搜索");
    expect(chatModuleIcon("paper")).toBe("paperSearch");
    expect(chatModuleLabel("resources")).toBe("学习资料推荐");
    expect(chatModuleIcon("resources")).toBe("chatBubble");
    expect(chatModuleLabel("github")).toBe("GitHub 项目推荐");
    expect(chatModuleIcon("github")).toBe("chatBubble");
    // 三个日常模块已全部接入；未知（或尚未接入的）取值没有中文名：
    // 宁可不显示标签，也不在中文界面回显英文枚举值。
    expect(chatModuleLabel("unknown_module")).toBeNull();
    expect(chatModuleIcon("unknown_module")).toBe("chatBubble");
    expect(chatModuleLabel(null)).toBeNull();
  });

  it("菜单里三个已接入模块的 ID 与后端枚举一致", () => {
    expect(CHAT_MODULES.map((module) => module.id)).toEqual([
      "paper",
      "commute",
      "tieba",
    ]);
  });

  it("最后一条论文消息还在等澄清时恢复论文模块选择", () => {
    expect(pendingClarificationModule([message("m-1", pendingPaperClarification)])).toBe(
      "paper"
    );
  });

  it("历史资料模块的澄清不恢复已退役选择", () => {
    expect(
      pendingClarificationModule([message("m-1", null, null, pendingResourcesClarification)])
    ).toBeNull();
  });

  it("最新投影属于退役模块时不恢复它或更早的等待", () => {
    expect(
      pendingClarificationModule([
        message("m-1", pendingPaperClarification),
        message("m-2", null, null, pendingResourcesClarification),
      ])
    ).toBeNull();
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
