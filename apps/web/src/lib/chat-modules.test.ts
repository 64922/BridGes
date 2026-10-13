import { describe, expect, it } from "vitest";
import { CHAT_MODULES, chatModuleIcon, chatModuleLabel, pendingClarificationModule } from "./chat-modules";
import { paperSearch, learningResources, message, pendingPaperClarification, pendingResourcesClarification } from "./chat-modules.test-support";

describe("chat-modules（V2 Issue 11/13）", () => {
  it("已接入模块给中文名与图标，未接入模块不回显英文 ID", () => {
    expect(chatModuleLabel("paper")).toBe("论文搜索");
    expect(chatModuleIcon("paper")).toBe("paperSearch");
    expect(chatModuleLabel("resources")).toBe("学习资料推荐");
    expect(chatModuleIcon("resources")).toBe("learningProject");
    expect(chatModuleLabel("github")).toBe("GitHub 项目推荐");
    expect(chatModuleIcon("github")).toBe("githubRepo");
    // 六个日常模块已全部接入；未知（或尚未接入的）取值没有中文名：
    // 宁可不显示标签，也不在中文界面回显英文枚举值。
    expect(chatModuleLabel("unknown_module")).toBeNull();
    expect(chatModuleIcon("unknown_module")).toBe("chatBubble");
    expect(chatModuleLabel(null)).toBeNull();
  });

  it("菜单里六个已接入模块的 ID 与后端枚举一致", () => {
    expect(CHAT_MODULES.map((module) => module.id)).toEqual([
      "paper",
      "commute",
      "resources",
      "tieba",
      "career",
      "github",
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
