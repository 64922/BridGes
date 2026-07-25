import type { Page } from "@playwright/test";

export async function createProject(page: Page, name: string): Promise<string> {
  await page.goto("/account/projects");
  await page.getByLabel("项目名称").fill(name);
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.waitForURL(/\/projects\/[A-Za-z0-9_-]+/);
  const match = page.url().match(/\/projects\/([A-Za-z0-9_-]+)/);
  if (!match) {
    throw new Error(`Failed to extract project id from ${page.url()}`);
  }
  return match[1];
}
