import { expect, test, type Page } from "@playwright/test";
import * as path from "path";
import { signUp, uniqueCredentials } from "./helpers/auth";

test.skip(process.env.ISSUE38_DOMAIN_FIXTURES !== "1", "专业路径须通过独立固定来源配置执行。");

/** 正式浏览器与真实 HTTP/执行器/现行图；模型和来源固定，仅证明机制。 */
const VIEWPORTS = [
  { width: 1280, height: 720 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
];
const MODULES = [
  {
    id: "paper",
    label: "论文搜索",
    initial: "帮我找 Transformer 的论文",
    continuation: "机器学习方向的，入门",
    field: "paper_search",
    card: "paper-search-card-success",
  },
  {
    id: "github",
    label: "GitHub 项目推荐",
    initial: "有没有人做过类似的项目",
    continuation: "我想做一个校园二手书交换平台，学生可以发布想卖的书，搜索想要的书，线下交换",
    field: "github_projects",
    card: "github-projects-card-success",
  },
];

async function send(page: Page, content: string) {
  const composer = page.getByTestId("composer");
  await expect(page.getByRole("button", { name: "停止生成" })).toHaveCount(0);
  await composer.getByLabel("输入消息").fill(content);
  await composer.getByRole("button", { name: "发送消息" }).click();
}

async function history(page: Page) {
  const id = page.url().split("/chat/")[1].split(/[?#]/)[0];
  const response = await page.request.get(`/api/chat/conversations/${id}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

for (const viewport of VIEWPORTS) {
  test(`${viewport.width}×${viewport.height} 学习照片、出题、判定与总结独立恢复`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize(viewport);
    const credentials = uniqueCredentials("study38");
    await signUp(page, credentials.username, credentials.qqEmail, "correct-horse-issue38");
    await page.getByRole("button", { name: "学习模式", exact: true }).click();
    // 可预览的最小 PNG；照片识别由固定模型边界提供，只证明流程机制。
    await page.getByTestId("composer-file-input").setInputFiles({
      name: "本节书页.png",
      mimeType: "image/png",
      buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6nCEAAAAASUVORK5CYII=", "base64"),
    });
    await expect(page.getByRole("button", { name: "发送消息" })).toBeEnabled();
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect(page).toHaveURL(/\/chat\/[^/]+$/);
    await expect.poll(async () => (await history(page)).study?.stage, { timeout: 30_000 }).toBe("tutoring");
    await expect(page.getByTestId("turn-result").last()).toHaveAttribute("data-outcome", /complete|partial/);
    await send(page, "学完了");
    await expect.poll(async () => (await history(page)).study?.review?.questions?.length, { timeout: 30_000 }).toBe(1);
    const asking = await history(page);
    expect(asking.messages.at(-1).turn_result.outcome).toBe("needs_input");
    expect(asking.study.review.questions[0].canonical_answer).toBeNull();
    expect(asking.study.review.questions[0].core_points).toEqual([]);
    await expect(page.getByTestId("turn-result").last()).toHaveAttribute("data-outcome", "needs_input");
    await send(page, "a 是斜率");
    await expect.poll(async () => (await history(page)).messages.at(-1).status, { timeout: 30_000 }).toBe("error");
    const failed = await history(page);
    expect(failed.messages.at(-1).turn_result.outcome).toBe("partial");
    expect(failed.messages.at(-1).content).toContain("回答正确");
    const grade = failed.study.review.questions[0].grade_record;
    await expect(page.getByTestId("turn-result-retry").last()).toBeVisible();
    await expect(page.getByText(/回答正确/).last()).toBeVisible();
    await expect(page.locator("body")).not.toContainText("study.summarize");
    await page.getByTestId("turn-result").last().scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.screenshot({
      path: path.resolve(__dirname, `../../../.scratch/2/acceptance/38-browser/partial-${viewport.width}x${viewport.height}.png`),
    });
    await page.getByTestId("turn-result-retry").last().focus();
    await page.keyboard.press("Enter");
    await expect.poll(async () => (await history(page)).messages.at(-1).status, { timeout: 30_000 }).toBe("done");
    const recovered = await history(page);
    expect(recovered.messages.at(-1).content).toContain("本节学习总结");
    expect(recovered.study.review.questions[0].grade_record).toEqual(grade);
    await expect(page.getByTestId("turn-result").last()).toHaveAttribute("data-outcome", /complete|partial/);
    await page.reload();
    const reloaded = await history(page);
    expect(reloaded.messages).toEqual(recovered.messages);
    expect(reloaded.study.review.questions[0].grade_record).toEqual(grade);
  });
  for (const module of MODULES) {
    test(`${viewport.width}×${viewport.height} ${module.label} 澄清、续接与刷新`, async ({ page }) => {
      test.setTimeout(120_000);
      await page.setViewportSize(viewport);
      const credentials = uniqueCredentials(`domain38${module.id}`);
      await signUp(page, credentials.username, credentials.qqEmail, "correct-horse-issue38");
      await page.getByRole("button", { name: "添加功能或文件" }).click();
      await page.getByRole("menuitem", { name: module.label, exact: false }).click();
      await send(page, module.initial);
      await expect(page).toHaveURL(/\/chat\/[^/]+$/);
      await expect(page.getByTestId("turn-result").last()).toHaveAttribute("data-outcome", "needs_input");
      const waiting = await history(page);
      expect(waiting.messages.at(-1)[module.field].status).toBe("clarification");
      expect(waiting.messages.at(-1).turn_result.wait_reason).toBeTruthy();
      await expect(page.getByRole("button", { name: `移除${module.label}模块` })).toBeVisible();
      await send(page, module.continuation);
      await expect.poll(async () => (await history(page)).messages.at(-1)[module.field]?.status, { timeout: 30_000 }).toBe("success");
      await expect(page.getByTestId("turn-result").last()).toHaveAttribute("data-outcome", /complete|partial/);
      const completed = await history(page);
      const message = completed.messages.at(-1);
      expect(message.turn_result.actual_module_id).toBe(module.id);
      expect(message.turn_result.delivered.length).toBeGreaterThan(0);
      expect(message[module.field].queries.length).toBeGreaterThan(0);
      expect(message.content).toContain("https://");
      await expect(page.getByTestId("turn-result-capability").last()).toContainText(module.label);
      const card = page.getByTestId(module.card).last();
      await expect(card).toBeVisible();
      const visible = await card.innerText();
      expect(visible).toContain(module.id === "paper" ? "未通读全文" : "README 自述");
      expect(visible).toContain("本次外部调用记录");
      await page.reload();
      await expect.poll(() => page.getByTestId(module.card).last().innerText()).toBe(visible);
      const reloaded = await history(page);
      expect(reloaded.messages).toEqual(completed.messages);
      await expect(page.getByTestId("turn-result-capability").last()).toContainText(module.label);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    });
  }
}
