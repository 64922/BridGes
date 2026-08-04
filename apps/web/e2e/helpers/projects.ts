import { expect, type Page } from "@playwright/test";

/**
 * 创建 legacy 科学项目空间并进入其工作台（/projects/{id}）。
 *
 * Issue 19 后 /account/projects 改为「学习项目」文件夹列表，旧的页面内
 * 创建表单已退场；科学项目空间仍由 POST /api/projects 提供，这里直接走
 * API 创建再导航到工作台深链（与页面内创建后的落点一致）。
 */
export async function createProject(page: Page, name: string): Promise<string> {
  const response = await page.request.post("/api/projects", { data: { name } });
  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  const projectId = body.id as string;
  await page.goto(`/projects/${projectId}`);
  await page.waitForURL(`/projects/${projectId}`);
  return projectId;
}
