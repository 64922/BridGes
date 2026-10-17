import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const PASSWORD = "correct-horse-closeout";
const LEARNING_QUERY = "我想学习Transformer架构的相关知识";
const PROFILE_ACADEMIC_QUERY = "我是一名人工智能专业的大三学生，目标是考一个211院校的相关专业，给我规划一下考研";
const PAPER_QUERY = "给我找几篇Transformer方向相关的论文";

async function sendFromNewChat(page: Page, content: string) {
  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill(content);
  await composer.getByRole("button", { name: "发送消息" }).click();
  await expect(page).toHaveURL(/\/chat\//);
  await expect(page.getByTestId("chat-thread")).toBeVisible();
}

async function openNewChat(page: Page) {
  await page
    .getByTestId("app-sidebar")
    .getByRole("link", { name: "新聊天", exact: true })
    .click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId("composer")).toBeVisible();
}

test("真实浏览器链路：学习、画像、论文三条旅程与画像回放", async ({ page }) => {
  const credentials = uniqueCredentials("release-gate");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  // 旅程一：学习模式由本地导师执行并如实交付——不再产生旧教学门证据卡；
  // 会话公开投影给出真实局部进展，且不显示未核实的掌握宣称。
  await page.getByTestId("mode-toggle").getByRole("button", { name: "学习模式" }).click();
  await sendFromNewChat(page, LEARNING_QUERY);
  await expect(page.getByTestId("turn-result").last()).toHaveAttribute(
    "data-outcome",
    /complete|partial/,
    { timeout: 45_000 }
  );
  const studyProgress = page.getByRole("region", { name: "局部学习进展" });
  await expect(studyProgress).toBeVisible();
  await expect(studyProgress).toContainText("正在学习：");
  await expect(page.getByTestId("teaching-card")).toHaveCount(0);
  await expect(page.getByTestId("chat-thread")).not.toContainText("证据门：证据充足");

  // 画像相关的两轮必须来自不同聊天，但保持同一浏览器账户。
  await openNewChat(page);
  await sendFromNewChat(page, PROFILE_ACADEMIC_QUERY);
  await expect(page.getByTestId("chat-thread")).toContainText(PROFILE_ACADEMIC_QUERY);
  await expect(page.getByTestId("public-process")).toContainText("回答已生成", {
    timeout: 45_000,
  });

  // 旅程二：论文请求只展示 arXiv 真实结果，不把普通搜索卡混入本轮。
  await openNewChat(page);
  await sendFromNewChat(page, PAPER_QUERY);
  const arxivCard = page.getByTestId("arxiv-search-card");
  await expect(arxivCard).toBeVisible({ timeout: 45_000 });
  await expect(arxivCard).toContainText("已返回 1 篇真实论文");
  await expect(arxivCard).toContainText("Attention Is All You Need: Transformer Architecture");
  await expect(page.getByTestId("arxiv-search-card-error")).toHaveCount(0);

  // 旅程三：画像页展示三类证据记录、兴趣爱好空态，并在刷新后仍可读取。
  await page.getByTestId("app-sidebar").getByRole("link", { name: "用户画像", exact: true }).click();
  await expect(page).toHaveURL(/\/account\/profile$/);
  await expect(page.getByRole("heading", { name: "你的信息" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "感兴趣的知识" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "学业情况" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "阶段目标" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "兴趣爱好" })).toBeVisible();
  const profileMain = page.locator("main");
  await expect(profileMain.getByText("Transformer架构的相关知识", { exact: true })).toBeVisible();
  await expect(profileMain.getByText("一名人工智能专业的大三学生", { exact: true })).toBeVisible();
  await expect(profileMain.getByText("考一个211院校的相关专业", { exact: true })).toBeVisible();
  await expect(page.getByText("聊过兴趣爱好后，会在这里整理相关内容。", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "你的信息" })).toBeVisible();
  await expect(page.getByText("聊过兴趣爱好后，会在这里整理相关内容。", { exact: true })).toBeVisible();
});
