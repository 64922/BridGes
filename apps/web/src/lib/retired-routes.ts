/**
 * 退役独立页面的兼容窗口路由表。
 *
 * 这里的匹配只使用固定路径和退役分区名称；返回值不携带账户、路径参数、
 * 查询参数或请求正文，供 middleware 记录稳定的迁移观测维度。
 */
export const RETIRED_PAGE_ROUTE_IDS = [
  "legacy.pages.learning_projects.list",
  "legacy.pages.learning_projects.detail",
  "legacy.pages.tasks",
  "legacy.pages.plugins",
  "legacy.pages.mcp",
] as const;

export type RetiredPageRouteId = (typeof RETIRED_PAGE_ROUTE_IDS)[number];

export interface RetiredPageRoute {
  endpointId: RetiredPageRouteId;
  replacementPath: "/";
}

function templateSection(search: string): string | null {
  return new URLSearchParams(search).get("section");
}

/** 返回旧独立页面的稳定兼容标识；当前产品页面返回 null。 */
export function findRetiredPageRoute(
  pathname: string,
  search = ""
): RetiredPageRoute | null {
  if (pathname === "/account/projects") {
    return { endpointId: "legacy.pages.learning_projects.list", replacementPath: "/" };
  }
  if (pathname.startsWith("/account/projects/")) {
    return { endpointId: "legacy.pages.learning_projects.detail", replacementPath: "/" };
  }
  if (
    pathname === "/tasks" ||
    ((pathname === "/templates/list" || pathname === "/templates/detail") &&
      templateSection(search) === "tasks")
  ) {
    return { endpointId: "legacy.pages.tasks", replacementPath: "/" };
  }
  if (
    pathname === "/plugins" ||
    pathname === "/account/plugins" ||
    ((pathname === "/templates/list" || pathname === "/templates/detail") &&
      templateSection(search) === "plugins")
  ) {
    return { endpointId: "legacy.pages.plugins", replacementPath: "/" };
  }
  if (pathname === "/mcp" || pathname === "/account/mcp") {
    return { endpointId: "legacy.pages.mcp", replacementPath: "/" };
  }
  if (
    (pathname === "/templates/list" || pathname === "/templates/detail") &&
    templateSection(search) === "projects"
  ) {
    return {
      endpointId: pathname === "/templates/detail"
        ? "legacy.pages.learning_projects.detail"
        : "legacy.pages.learning_projects.list",
      replacementPath: "/",
    };
  }
  return null;
}
