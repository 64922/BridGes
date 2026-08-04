import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

// 1×1 透明 PNG（真实图片字节，经内容嗅探识别为 image/png）。
const PNG_1PX = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64"
);

/**
 * Issue 25 — 数字分身画像中心与静态头像。
 *
 * 播种策略（真实后端）：画像服务挂载在 API 进程内（InMemory 仓库），因此
 * 本 spec 全程在同一个 API 进程生命周期内完成注册 → 新增 → 治理 → 导出。
 * 候选数据通过 API 播种观察与候选（页面无自动写入来源）。
 */

const PASSWORD = "correct-horse-25";

async function freshAccount(page: Page, prefix: string) {
  const creds = uniqueCredentials(prefix);
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  return creds;
}

async function getAccountId(page: Page): Promise<string> {
  const res = await page.request.get("/api/auth/session");
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  return body.account.id as string;
}

/** 通过 API 播种一条观察 + 一条候选，供「待确认候选」区块使用。 */
async function seedCandidate(page: Page, accountId: string, content: string) {
  const observation = await page.request.post("/api/profiles/observations", {
    data: {
      owner_account_id: accountId,
      source_type: "explicit_statement",
      source_ref: "e2e-conversation-25",
      source_span_or_event: "user-message-25",
      scene: "quick_check",
      purpose: "interest_preference",
      observed_content: content,
      signal_kind: "preference",
      extractor_and_version: "e2e-seed-1",
      reliability_factors: ["explicit_statement"],
      sensitivity_class: "preference",
      retention_policy: "account_lifetime",
    },
  });
  expect(observation.ok()).toBeTruthy();
  const observationBody = await observation.json();
  const candidate = await page.request.post("/api/profiles/candidates", {
    data: {
      owner_account_id: accountId,
      canonical_dimension: "interest_preference",
      value_or_rule: content,
      applicable_scenes: ["quick_check"],
      supporting_observation_ids: [observationBody.observation_id],
      evidence_summary: "E2E 播种候选",
      authorization_scope: "general",
    },
  });
  expect(candidate.ok()).toBeTruthy();
}

async function openProfileCenter(page: Page) {
  await page.goto("/account/profile");
  await expect(page.getByRole("heading", { name: "数字分身画像" })).toBeVisible();
}

test("九类分区、空态与新增记录", async ({ page }) => {
  await freshAccount(page, "i25-create");
  await openProfileCenter(page);

  // 九个独立分区都有 tab。
  const tabLabels = [
    "基本情况",
    "阶段目标",
    "兴趣偏好",
    "表达习惯",
    "知识状态",
    "情绪变化趋势",
    "重要经历",
    "正在面对的问题",
    "授权范围",
  ];
  for (const label of tabLabels) {
    await expect(page.getByRole("tab", { name: new RegExp(label) })).toBeVisible();
  }

  // 空态（分区面板内）。
  await expect(page.getByText("该类别还没有记录")).toBeVisible();

  // 新增一条记录（阶段目标）。
  await page.getByRole("tab", { name: /阶段目标/ }).click();
  await page.getByRole("button", { name: "新增记录" }).click();
  await page.getByLabel("画像记录内容").fill("三个月内完成科学项目框架");
  await page.getByLabel("来源说明（记录从何而来，用于证据追溯）").fill("E2E 手动声明");
  await page.getByRole("dialog").getByRole("button", { name: "保存" }).click();

  // 记录卡出现：值、来源、状态徽章、时间。
  await expect(page.getByText("三个月内完成科学项目框架")).toBeVisible();
  await expect(page.getByText("E2E 手动声明")).toBeVisible();
  await expect(page.getByText("活跃").first()).toBeVisible();
  await expect(page.getByText("来源证据", { exact: true })).toBeVisible();
  await expect(page.getByText("最近用于回答")).toBeVisible();
});

test("编辑、版本历史与回滚说明来源", async ({ page }) => {
  await freshAccount(page, "i25-history");
  await openProfileCenter(page);

  await page.getByRole("tab", { name: /阶段目标/ }).click();
  await page.getByRole("button", { name: "新增记录" }).click();
  await page.getByLabel("画像记录内容").fill("三个月内完成科学项目框架");
  await page.getByRole("dialog").getByRole("button", { name: "保存" }).click();

  // 编辑并查看历史：旧值保留、变更原因可读。
  await page.getByRole("button", { name: "编辑" }).click();
  await page.getByLabel("记录内容").fill("两个月内完成科学项目框架");
  await page.getByLabel("变更原因").fill("调整目标周期");
  await page.getByRole("dialog").getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("两个月内完成科学项目框架")).toBeVisible();

  await page.getByRole("button", { name: "版本历史" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText("v1")).toBeVisible();
  await expect(page.getByText("三个月内完成科学项目框架")).toBeVisible();
  await expect(page.getByText("调整目标周期")).toBeVisible();
  await page.keyboard.press("Escape");
});

test("撤回、解冻、冻结与删除的完整治理闭环", async ({ page }) => {
  await freshAccount(page, "i25-lifecycle");
  await openProfileCenter(page);

  await page.getByRole("tab", { name: /阶段目标/ }).click();
  await page.getByRole("button", { name: "新增记录" }).click();
  await page.getByLabel("画像记录内容").fill("完成毕业论文初稿");
  await page.getByRole("dialog").getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("完成毕业论文初稿")).toBeVisible();

  // 撤回：需要原因与确认。
  await page.getByRole("button", { name: "撤回" }).click();
  await page.getByRole("button", { name: "确认撤回" }).click();
  await expect(page.getByText("请填写操作原因，以便审计记录。")).toBeVisible();
  await page.getByLabel("操作原因").fill("不再适用");
  await page.getByRole("button", { name: "确认撤回" }).click();
  await expect(page.getByText("已撤回").first()).toBeVisible();

  // 解冻恢复活跃。
  await page.getByRole("button", { name: "解冻" }).click();
  await page.getByLabel("操作原因").fill("恢复使用");
  await page.getByRole("button", { name: "确认解冻" }).click();
  await expect(page.getByText("活跃").first()).toBeVisible();

  // 冻结。
  await page.getByRole("button", { name: "冻结" }).click();
  await page.getByLabel("操作原因").fill("暂时冻结");
  await page.getByRole("button", { name: "确认冻结" }).click();
  await expect(page.getByText("已冻结").first()).toBeVisible();

  // 解冻后删除。
  await page.getByRole("button", { name: "解冻" }).click();
  await page.getByLabel("操作原因").fill("恢复后删除");
  await page.getByRole("button", { name: "确认解冻" }).click();
  await page.getByRole("button", { name: "删除" }).click();
  await page.getByLabel("操作原因").fill("确认删除记录");
  await page.getByRole("button", { name: "确认删除" }).click();
  await expect(page.getByText("已删除").first()).toBeVisible();
});

test("候选确认后进入画像记录", async ({ page }) => {
  await freshAccount(page, "i25-candidate");
  const accountId = await getAccountId(page);
  await seedCandidate(page, accountId, "喜欢阅读科学史书籍");

  await openProfileCenter(page);

  // 待确认候选区块。
  await expect(page.getByText("待确认候选")).toBeVisible();
  await expect(page.getByText("喜欢阅读科学史书籍")).toBeVisible();

  // 确认后进入兴趣偏好类别。
  await page.getByRole("button", { name: "确认" }).click();
  await expect(page.getByText("待确认候选")).not.toBeVisible();
  await page.getByRole("tab", { name: /兴趣偏好/ }).click();
  await expect(page.getByText("喜欢阅读科学史书籍")).toBeVisible();
});

test("导出下载可读画像与授权历史", async ({ page }) => {
  await freshAccount(page, "i25-export");
  await openProfileCenter(page);

  await page.getByRole("tab", { name: /阶段目标/ }).click();
  await page.getByRole("button", { name: "新增记录" }).click();
  await page.getByLabel("画像记录内容").fill("导出前新增的记录");
  await page.getByRole("dialog").getByRole("button", { name: "保存" }).click();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出画像与授权历史" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/bridges-profile-export-/);
});

test("上传并移除静态头像，头像仅作视觉标识", async ({ page }) => {
  await freshAccount(page, "i25-avatar");
  await openProfileCenter(page);

  // 上传真实 PNG：预览变为已上传头像。
  await page.locator('input[type="file"][accept="image/png,image/jpeg"]').setInputFiles({
    name: "avatar.png",
    mimeType: "image/png",
    buffer: PNG_1PX,
  });
  await page.getByRole("button", { name: "保存头像" }).click();
  await expect(page.getByTestId("main-content").getByTestId("account-avatar")).toHaveAttribute(
    "data-avatar-choice",
    "uploaded"
  );

  // 移除：回退到姓名首字静态头像，上传对象删除后不可再读取。
  await page.getByRole("button", { name: "移除头像" }).click();
  await expect(page.getByTestId("main-content").getByTestId("account-avatar")).toHaveAttribute(
    "data-avatar-choice",
    "initials"
  );
  const avatar = await page.request.get("/api/auth/profile/avatar");
  expect(avatar.status()).toBe(404);
});

test("键盘可用方向键浏览画像类别", async ({ page }) => {
  await freshAccount(page, "i25-keyboard");
  await openProfileCenter(page);

  const stageTab = page.getByRole("tab", { name: /基本情况/ });
  await stageTab.focus();
  expect(await stageTab.getAttribute("aria-selected")).toBe("true");

  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /阶段目标/ })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /兴趣偏好/ })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByRole("tab", { name: /阶段目标/ })).toHaveAttribute(
    "aria-selected",
    "true"
  );
});
