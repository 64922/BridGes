import { expect, test } from "@playwright/test";

// Issue 06（学习模式重构）：档案纠正与删除的桌面旅程。
// 桌面只验证入口、确认与投影回显；版本并发校验、迟到结果保护、有效
// 读取边界由 tests/chat/test_issue06_learning_record_controls.py 走真实
// 服务链路覆盖。
const NOW = "2026-10-14T00:00:00Z";

const pendingTask = {
  task_id: "dx-pending",
  status: "pending",
  question: "如果 A、B 互斥，P(A∩B) 是多少？为什么？",
  knowledge_context: "互斥与独立",
  evidence_message_id: "m-user-2",
  failure_reason: "",
  attempts: 1,
  retryable: false,
  created_at: NOW,
  updated_at: NOW,
};

const record = {
  record_id: "lr-1",
  subject: "概率论",
  title: "互斥与独立",
  usage_context: "理解事件关系",
  verdict: "independent_completion",
  performance: "能说明互斥不等于独立。",
  supporting: ["给出了正确的反例"],
  refuting: [],
  uncertainty: "只在单题上验证过。",
  next_validation: "换一个条件概率题再验证。",
  pending_confirmation: false,
  evidence_count: 1,
  evidences: [
    {
      evidence_id: "ev-1",
      question: "如果 A、B 互斥，P(A∩B) 是多少？为什么？",
      knowledge_context: "互斥与独立",
      assistance: "independent",
      verdict: "independent_completion",
      answer_message_id: "m-user-2",
      feedback_message_id: "m-assistant-2",
      occurred_at: NOW,
      source_available: false,
      source_note: "回答消息已删除，无法回读原文。",
    },
  ],
  created_at: NOW,
  updated_at: NOW,
  record_version: 1,
  state: "active",
  user_note: "",
  corrected_at: null,
  superseded_verdict: null,
  superseded_performance: "",
  source_available: false,
};

function conversation(tasks: unknown[], records: unknown[]) {
  return {
    conversation_id: "record-controls-test",
    title: "互斥与独立",
    mode: "study",
    mode_locked: true,
    pinned: false,
    created_at: NOW,
    updated_at: NOW,
    mode_events: [],
    study_local_progress: {
      topic: "互斥与独立",
      knowledge_context: "两个事件不能同时发生。",
      current_focus: "互斥是不是独立？",
      source_message_ids: ["m-user-1", "m-assistant-1"],
      version: 2,
      runtime_run_id: "run-1",
      updated_at: NOW,
    },
    study_diagnosis_tasks: tasks,
    study_learning_record: records,
    messages: [],
  };
}

async function mockAccount(
  page: import("@playwright/test").Page,
  testInfo: import("@playwright/test").TestInfo
) {
  await page.context().addCookies([
    {
      name: "bridges_session",
      value: "record-controls-test",
      url: testInfo.project.use.baseURL!,
    },
  ]);
  const account = {
    id: "record-controls-owner",
    username: "档案测试",
    qq_email: "12345@qq.com",
    avatar_choice: "initials",
    created_at: NOW,
    updated_at: NOW,
  };
  return { account };
}

test("纠正学习记录：查看依据、保存用户自评并保留被取代结论", async ({
  page,
}, testInfo) => {
  const { account } = await mockAccount(page, testInfo);
  let records: unknown[] = [record];
  let postedNote = "";
  let postedVersion = -1;
  const correctedRecord = {
    ...record,
    record_version: 2,
    state: "corrected",
    user_note: "我当时其实靠猜的，并没有真正理解。",
    corrected_at: NOW,
    superseded_verdict: "independent_completion",
    superseded_performance: "能说明互斥不等于独立。",
    source_available: false,
  };

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    let body: unknown = {};
    if (path === "/api/auth/session") {
      body = { account, session: {}, subject: {} };
    } else if (path === "/api/auth/device/accounts") {
      body = { accounts: [], current_account: account };
    } else if (
      method === "POST" &&
      path === "/api/chat/conversations/record-controls-test/study/records/lr-1/corrections"
    ) {
      const payload = JSON.parse(route.request().postData() ?? "{}");
      postedNote = payload.note;
      postedVersion = payload.expected_version;
      records = [correctedRecord];
      body = correctedRecord;
    } else if (path === "/api/chat/conversations/record-controls-test") {
      body = conversation([], records);
    } else if (path === "/api/chat/conversations") {
      body = { conversations: [conversation([], records)] };
    } else if (path === "/api/chat/attachment-drafts") {
      body = [];
    }
    await route.fulfill({ json: body });
  });

  await page.goto("/chat/record-controls-test");
  await page.getByTestId("study-details-open").click();
  const panel = page.locator('[aria-label="学习档案"]');
  await expect(panel).toBeVisible();
  // 来源不可读的证据如实标注，不伪造引文与核验结论。
  await expect(panel.getByText(/部分依据来源已不可读/)).toBeVisible();
  await panel.getByText("依据与证据（1 条）").click();
  await expect(panel.getByText(/回答消息已删除，无法回读原文/)).toBeVisible();

  // 纠正：行内编辑提交用户自评，按记录版本做并发校验。
  await panel.getByRole("button", { name: "纠正" }).click();
  await panel
    .getByLabel("纠正内容")
    .fill("我当时其实靠猜的，并没有真正理解。");
  await panel.getByRole("button", { name: "保存" }).click();
  await expect
    .poll(() => [postedNote, postedVersion], { message: "纠正请求已按记录版本提交" })
    .toEqual(["我当时其实靠猜的，并没有真正理解。", 1]);

  await expect(panel.getByText(/用户纠正（自评，未经核验）/)).toBeVisible();
  await expect(
    panel.getByText(/我的纠正（自评，未经核验）：我当时其实靠猜的/),
  ).toBeVisible();
  await expect(
    panel.getByText(/原核验结论「独立完成」已被你的纠正取代，不再用于后续教学/),
  ).toBeVisible();
  await expect(panel.getByLabel("纠正内容")).toBeHidden();

  // 刷新后保持：纠正态来自持久化投影。
  await page.reload();
  await page.getByTestId("study-details-open").click();
  const refreshed = page.locator('[aria-label="学习档案"]');
  await expect(
    refreshed.getByText(/用户纠正（自评，未经核验）/),
  ).toBeVisible();
});

test("删除学习记录：确认后撤回，迟到的核验结果不恢复", async ({
  page,
}, testInfo) => {
  const { account } = await mockAccount(page, testInfo);
  let tasks: unknown[] = [pendingTask];
  let records: unknown[] = [record];
  let deletedVersion = -1;
  let confirmMessage = "";

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    if (path === "/api/auth/session") {
      await route.fulfill({ json: { account, session: {}, subject: {} } });
    } else if (path === "/api/auth/device/accounts") {
      await route.fulfill({ json: { accounts: [], current_account: account } });
    } else if (
      method === "DELETE" &&
      path === "/api/chat/conversations/record-controls-test/study/records/lr-1"
    ) {
      const payload = JSON.parse(route.request().postData() ?? "{}");
      deletedVersion = payload.expected_version;
      records = [];
      await route.fulfill({ status: 204 });
    } else if (path === "/api/chat/conversations/record-controls-test") {
      await route.fulfill({ json: conversation(tasks, records) });
    } else if (path === "/api/chat/conversations") {
      await route.fulfill({
        json: { conversations: [conversation(tasks, records)] },
      });
    } else if (path === "/api/chat/attachment-drafts") {
      await route.fulfill({ json: [] });
    } else {
      await route.fulfill({ json: {} });
    }
  });

  page.on("dialog", async (dialog) => {
    confirmMessage = dialog.message();
    await dialog.accept();
  });

  await page.goto("/chat/record-controls-test");
  await page.getByTestId("study-details-open").click();
  const panel = page.locator('[aria-label="学习档案"]');
  await expect(panel.getByText("已核验的知识点")).toBeVisible();

  await panel.getByRole("button", { name: "删除" }).click();
  await expect
    .poll(() => deletedVersion, { message: "删除请求已按记录版本提交" })
    .toBe(1);
  expect(confirmMessage).toContain("不再用于后续教学");

  // 删除后本对话不再展示该记录；待核验任务仍在继续。
  await expect(panel.getByText("已核验的知识点")).toBeHidden();
  await expect(panel.getByText(/待后台核验/)).toBeVisible();

  // 用户删除后，迟到的核验成功也不恢复记录（撤回保护）。
  tasks = [{ ...pendingTask, status: "succeeded" }];
  await expect(panel.getByText(/待后台核验/)).toBeHidden({
    timeout: 10_000,
  });
  await expect(panel.getByText("已核验的知识点")).toBeHidden();
});
