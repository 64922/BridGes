import { describe, expect, it } from "vitest";
import { CHAT_MODULES, chatModuleIcon, chatModuleLabel, pendingClarificationModule } from "./chat-modules";
import { message, githubProjects, pendingPaperClarification } from "./chat-modules.test-support";

describe("chat-modules GitHub 项目推荐（V2 Issue 16）", () => {
  it("GitHub 在菜单里给中文名与仓库图标", () => {
    expect(chatModuleLabel("github")).toBe("GitHub 项目推荐");
    expect(chatModuleIcon("github")).toBe("githubRepo");
    expect(CHAT_MODULES.find((module) => module.id === "github")?.description).toContain(
      "公开仓库"
    );
  });

  it("最后一条 GitHub 消息还在等澄清时恢复 GitHub 模块选择", () => {
    const pending = githubProjects({
      status: "clarification",
      pending: {
        module_id: "github",
        kind: "clarification",
        question: "你想找哪个项目或哪个功能的公开仓库？",
        origin_message_id: "a-1",
        context: {},
        created_at: "2026-09-26T02:00:00Z",
      },
    });
    expect(
      pendingClarificationModule([message("m-1", null, null, null, null, pending)])
    ).toBe("github");
    // 论文的历史判定不受 GitHub 等待影响（两者互不冒充）。
    expect(pendingClarificationModule([message("m-1", pendingPaperClarification)])).toBe(
      "paper"
    );
  });

  it("GitHub 已有结论时不恢复：success / metadata_only / empty 都算本轮结束", () => {
    for (const status of ["success", "metadata_only", "empty"] as const) {
      expect(
        pendingClarificationModule([
          message("m-1", null, null, null, null, githubProjects({ status })),
        ])
      ).toBeNull();
    }
  });

  it("本轮失败或停止时不冒领更早的等待状态", () => {
    const pending = githubProjects({
      status: "clarification",
      pending: {
        module_id: "github",
        kind: "clarification",
        question: "你想找哪个功能的公开仓库？",
        origin_message_id: "a-1",
        context: {},
        created_at: "2026-09-26T02:00:00Z",
      },
    });
    expect(
      pendingClarificationModule([
        message("m-1", null, null, null, null, pending),
        message("m-2", null, null, null, null, githubProjects({ status: "error" })),
      ])
    ).toBeNull();
    expect(
      pendingClarificationModule([
        message("m-1", null, null, null, null, pending),
        message("m-2", null, null, null, null, githubProjects({ status: "stopped" })),
      ])
    ).toBeNull();
  });
});
