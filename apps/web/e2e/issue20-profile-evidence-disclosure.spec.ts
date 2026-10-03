/**
 * Issue 20：画像列表按需展开依据与时效的正式桌面验收。
 *
 * 连真实 API + 后台执行器（零 mock）：注册账户、用显式「记住」写入两条
 * 真实来源的长期信息，删除其中一条来源会话，然后在桌面浏览器里逐条展开。
 * 断言真实原话/时间/范围/期限、来源已删时的诚实提示、原文定位、四类反馈
 * 不删除事实，以及行内保存/取消与删除确认仍可操作。
 */

import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import path from "node:path";

import { signUp, uniqueCredentials } from "./helpers/auth";

interface FirstTurn {
  conversation: { conversation_id: string };
  user_message: { message_id: string };
}

async function remember(page: Page, content: string): Promise<FirstTurn> {
  const response = await page.request.post("/api/chat/first-turn", {
    data: {
      content,
      idempotency_key: `e2e-issue20-${Date.now()}-${Math.random()
        .toString(36)
        .slice(2)}`,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as FirstTurn;
}

test("画像列表按需展开真实依据、诚实标注来源并记录四类反馈", async ({
  page,
}) => {
  const credentials = uniqueCredentials("issue20");
  await signUp(
    page,
    credentials.username,
    credentials.qqEmail,
    "correct-horse-issue20"
  );
  await remember(page, "记住我喜欢跑步");
  const deleted = await remember(page, "记住我计划下周通过英语六级");

  // 等两条长期信息都真实落库后再操作，避免与后台异步提取竞争。
  await expect
    .poll(async () => {
      const response = await page.request.get("/api/profiles/items");
      const items = (await response.json()) as Array<{ text: string }>;
      return items.filter((entry) =>
        ["我喜欢跑步", "我计划下周通过英语六级"].includes(entry.text)
      ).length;
    })
    .toBe(2);

  // 等来源会话的回答生成结束（删除只在没有进行中回答时允许），再删除来源。
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `/api/chat/conversations/${deleted.conversation.conversation_id}`
        );
        const body = (await response.json()) as {
          messages: Array<{ role: string; status: string }>;
        };
        return (
          body.messages.find((message) => message.role === "assistant")?.status ??
          "missing"
        );
      },
      { timeout: 30_000 }
    )
    .not.toBe("streaming");
  const deletion = await page.request.delete(
    `/api/chat/conversations/${deleted.conversation.conversation_id}`
  );
  expect(deletion.ok(), await deletion.text()).toBeTruthy();

  await page.goto("/account/profile");
  const runRow = page
    .getByTestId("atomic-item")
    .filter({ hasText: "我喜欢跑步" });
  const goalRow = page
    .getByTestId("atomic-item")
    .filter({ hasText: "我计划下周通过英语六级" });
  await expect(runRow).toBeVisible();
  await expect(goalRow).toBeVisible();

  // 无固定分类、修改与删除始终可见，展开入口可聚焦。
  for (const label of ["学业情况", "感兴趣的知识", "兴趣爱好", "阶段目标"]) {
    await expect(page.getByText(label, { exact: true })).toHaveCount(0);
  }
  await expect(runRow.getByRole("button", { name: "修改" })).toBeVisible();
  await expect(runRow.getByRole("button", { name: "删除" })).toBeVisible();

  // 第一条：来源可访问。显式「记住」不伪造聊天原话，如实说明；来源有定位。
  await runRow.getByRole("button", { name: "查看依据" }).click();
  const runPanel = runRow.getByTestId("atomic-evidence");
  await expect(runPanel).toContainText("没有保存聊天原话");
  await expect(runPanel).toContainText("适用范围：长期适用");
  await expect(runPanel).toContainText("没有明确期限");
  await runPanel.getByRole("button", { name: "查看原文" }).click();
  await expect(page).toHaveURL(/\/chat\/[^?]+\?message=/);
  await expect(
    page.getByTestId("chat-thread").getByText("记住我喜欢跑步").first()
  ).toBeVisible();

  // 第二条：来源已删除。展示保存时的时效，但不返回定位、不伪造查看入口。
  await page.goto("/account/profile");
  await goalRow.getByRole("button", { name: "查看依据" }).click();
  const goalPanel = goalRow.getByTestId("atomic-evidence");
  await expect(goalPanel).toContainText("原文说「下周」");
  await expect(goalPanel).toContainText("已删除");
  await expect(
    goalPanel.getByRole("button", { name: "查看原文" })
  ).toHaveCount(0);

  // 四类反馈都可提交；提交后列表行仍在，事实不被自动删除。
  for (const label of [
    "事实记错",
    "信息过期",
    "范围不适用",
    "回答没执行偏好",
  ]) {
    await expect(
      goalPanel.getByRole("button", { name: label })
    ).toBeVisible();
  }
  for (const label of ["事实记错", "信息过期", "范围不适用", "回答没执行偏好"]) {
    await goalPanel.getByRole("button", { name: label, exact: true }).click();
    await expect(goalRow.getByTestId("atomic-feedback-notice")).toContainText("已记录反馈");
    await expect(goalPanel).toContainText(`已反馈「${label}」`);
  }
  await expect(goalRow).toBeVisible();
  await expect(goalRow).toContainText("我计划下周通过英语六级");

  // 行内修改保存仍然生效，并即时反映在列表。
  await runRow.getByRole("button", { name: "修改" }).click();
  await page.getByLabel("长期信息内容").fill("我每天都跑步");
  await page.getByRole("button", { name: "保存" }).click();
  const updatedRow = page
    .getByTestId("atomic-item")
    .filter({ hasText: "我每天都跑步" });
  await expect(updatedRow).toBeVisible();

  // 删除仍先确认，确认后即时从列表移除。
  page.on("dialog", (dialog) => void dialog.accept());
  await updatedRow.getByRole("button", { name: "删除" }).click();
  await expect(updatedRow).toHaveCount(0);
});

test("多来源与过期记录核对原话，删除原话来源后停止展示副本", async ({ page }) => {
  const credentials = uniqueCredentials("i20-evidence");
  await signUp(page, credentials.username, credentials.qqEmail, "correct-horse-issue20");
  const first = await remember(page, "记住我喜欢跑步");
  const second = await remember(page, "记住我每周跑步三次");
  const items = await (await page.request.get("/api/profiles/items")).json();
  const target = items.find((entry: { text: string }) => entry.text === "我喜欢跑步");
  expect(target).toBeTruthy();

  // 显式记住不存原话。夹具只补写隔离库的证据与过去期限，验证真实读 API/UI，
  // 不将此场景宣称为真实模型抽取质量验收。
  const health = await (await page.request.get("/api/health/ready")).json();
  const root = path.resolve(__dirname, "../../..");
  const runDir = process.env.E2E_DATA_DIR
    ? path.resolve(root, process.env.E2E_DATA_DIR)
    : path.join(root, ".tmp", "e2e-run", health.extensions.run_id);
  const dbPath = path.join(runDir, "bridges.db");
  expect(createHash("sha256").update(dbPath).digest("hex").slice(0, 8))
    .toBe(health.extensions.database_path_fingerprint);
  const session = await (await page.request.get("/api/auth/session")).json();
  const python = process.env.BRIDGES_PYTHON || path.join(root, ".venv",
    ...(process.platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"]));
  execFileSync(python, ["-c", [
    "import json, sqlite3, sys",
    "db = sqlite3.connect(sys.argv[1], timeout=30)",
    "db.execute(\"UPDATE profile_items SET evidence_quote=?, source_message_ids_json=?, valid_from=?, valid_until=?, validity_phrase=? WHERE account_id=? AND profile_item_id=?\", (sys.argv[2], json.dumps(sys.argv[3:5]), '2020-01-01T00:00:00+00:00', '2020-01-02T00:00:00+00:00', '2020年1月', sys.argv[5], sys.argv[6]))",
    "db.commit()",
    "db.close()",
  ].join("\n"), dbPath, "我喜欢跑步", first.user_message.message_id,
    second.user_message.message_id, session.account.id, target.profile_item_id]);

  await page.goto("/account/profile");
  const row = page.getByTestId("atomic-item").filter({ hasText: "我喜欢跑步" });
  await row.getByRole("button", { name: "查看依据" }).click();
  const panel = row.getByTestId("atomic-evidence");
  await expect(panel.locator("blockquote")).toHaveText("我喜欢跑步");
  await expect(panel).toContainText("已过期");
  await expect(panel.getByRole("button", { name: "查看原文" })).toHaveCount(2);
  await expect.poll(async () => {
    const response = await page.request.delete(`/api/chat/conversations/${first.conversation.conversation_id}`);
    expect([204, 409]).toContain(response.status());
    return response.status();
  }, { timeout: 30_000 }).toBe(204);

  await row.getByRole("button", { name: "收起依据" }).click();
  await row.getByRole("button", { name: "查看依据" }).click();
  await expect(panel).toContainText("保存的原话不再展示");
  await expect(panel.locator("blockquote")).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "查看原文" })).toHaveCount(1);
  const after = await (await page.request.get("/api/profiles/items")).json();
  expect(after.find((entry: { profile_item_id: string }) =>
    entry.profile_item_id === target.profile_item_id).evidence_quote).toBeNull();
});
