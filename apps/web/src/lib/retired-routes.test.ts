import { describe, expect, it } from "vitest";

import { findRetiredPageRoute, RETIRED_PAGE_ROUTE_IDS } from "@/lib/retired-routes";

describe("退役独立页面兼容路由", () => {
  it("区分学习项目列表与详情，并统一指向新聊天", () => {
    expect(findRetiredPageRoute("/account/projects", "")).toMatchObject({
      endpointId: "legacy.pages.learning_projects.list",
      replacementPath: "/",
    });
    expect(findRetiredPageRoute("/account/projects/project-1", "")).toMatchObject({
      endpointId: "legacy.pages.learning_projects.detail",
      replacementPath: "/",
    });
  });

  it("覆盖任务、插件和 MCP 管理旧地址", () => {
    expect(findRetiredPageRoute("/tasks", "")?.endpointId).toBe("legacy.pages.tasks");
    expect(findRetiredPageRoute("/plugins", "")?.endpointId).toBe("legacy.pages.plugins");
    expect(findRetiredPageRoute("/mcp", "")?.endpointId).toBe("legacy.pages.mcp");
  });

  it("识别开发模板中的退役分区，但不误伤保留的知识库分区", () => {
    expect(findRetiredPageRoute("/templates/list", "?section=projects")?.endpointId).toBe(
      "legacy.pages.learning_projects.list"
    );
    expect(findRetiredPageRoute("/templates/detail", "?section=tasks")?.endpointId).toBe(
      "legacy.pages.tasks"
    );
    expect(findRetiredPageRoute("/templates/list", "?section=knowledge")).toBeNull();
  });

  it("路由 ID 是稳定且不含账户、URL 参数或请求正文的固定集合", () => {
    expect(RETIRED_PAGE_ROUTE_IDS).toEqual([
      "legacy.pages.learning_projects.list",
      "legacy.pages.learning_projects.detail",
      "legacy.pages.tasks",
      "legacy.pages.plugins",
      "legacy.pages.mcp",
    ]);
  });
});
