import { expect, test } from "@playwright/test";

// Issue 05（学习模式重构）：后台核验与学习档案的桌面旅程。
// 桌面只验证展示、重试与轮询推进；真实任务登记、原子提交、乱序归并
// 由 tests/chat/test_issue05_durable_diagnosis.py 走真实服务链路覆盖。
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

const failedTask = {
  task_id: "dx-failed",
  status: "failed",
  question: "如果 A、B 独立，P(A∩B) 与 P(A)、P(B) 的关系是什么？",
  knowledge_context: "互斥与独立",
  evidence_message_id: "m-user-3",
  failure_reason: "诊断服务暂时不可用，稍后将自动重试。",
  attempts: 3,
  retryable: true,
  created_at: NOW,
  updated_at: NOW,
};

const succeededTask = {
  ...pendingTask,
  status: "succeeded",
  retryable: false,
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
      source_available: true,
      source_note: "",
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
  source_available: true,
};

function conversation(tasks: unknown[], records: unknown[]) {
  return {
    conversation_id: "diagnosis-test",
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
    messages: [
      {
        message_id: "m-user-1",
        conversation_id: "diagnosis-test",
        role: "user",
        attempt_number: 1,
        status: "done",
        created_at: NOW,
        updated_at: NOW,
        content: "互斥是不是独立？",
      },
      {
        message_id: "m-assistant-1",
        conversation_id: "diagnosis-test",
        role: "assistant",
        attempt_number: 1,
        status: "done",
        created_at: NOW,
        updated_at: NOW,
        content: "先检查一个关键点。如果 A、B 互斥，P(A∩B) 是多少？为什么？",
      },
    ],
  };
}

test("学习详情展示待核验与真实状态，成功后给出表现与依据", async ({
  page,
}, testInfo) => {
  await page.context().addCookies([
    {
      name: "bridges_session",
      value: "diagnosis-test",
      url: testInfo.project.use.baseURL!,
    },
  ]);
  const account = {
    id: "diagnosis-owner",
    username: "诊断测试",
    qq_email: "12345@qq.com",
    avatar_choice: "initials",
    created_at: NOW,
    updated_at: NOW,
  };
  let tasks: unknown[] = [pendingTask, failedTask];
  let records: unknown[] = [];
  let retryPosted = false;

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body: unknown = {};
    if (path === "/api/auth/session") {
      body = { account, session: {}, subject: {} };
    } else if (path === "/api/auth/device/accounts") {
      body = { accounts: [], current_account: account };
    } else if (
      route.request().method() === "POST" &&
      path === "/api/chat/conversations/diagnosis-test/study/diagnosis/dx-failed/retry"
    ) {
      retryPosted = true;
      tasks = [
        pendingTask,
        { ...failedTask, status: "pending", failure_reason: "", retryable: false },
      ];
      body = tasks[1];
    } else if (path === "/api/chat/conversations/diagnosis-test") {
      body = conversation(tasks, records);
    } else if (path === "/api/chat/conversations") {
      body = { conversations: [conversation(tasks, records)] };
    } else if (path === "/api/chat/attachment-drafts") {
      body = [];
    }
    await route.fulfill({ json: body });
  });

  await page.goto("/chat/diagnosis-test");
  await page.getByTestId("study-details-open").click();
  const panel = page.locator('[aria-label="学习档案"]');
  await expect(panel).toBeVisible();
  await expect(panel.getByText("核验状态")).toBeVisible();
  await expect(
    panel.getByText(/「如果 A、B 互斥，P\(A∩B\) 是多少？为什么？」：待后台核验/),
  ).toBeVisible();
  await expect(panel.getByText(/核验失败 · 诊断服务暂时不可用/)).toBeVisible();
  await expect(
    panel.getByText(/后台独立核验只针对当时的实际作答/),
  ).toBeVisible();
  // 尚无成功核验时如实显示空态：没有档案条目。
  await expect(panel.getByText("已核验的知识点")).toBeHidden();

  // 失败任务提供手动重试；点击后任务回到待核验。
  await panel.getByRole("button", { name: "重试核验" }).click();
  await expect
    .poll(() => retryPosted, { message: "重试请求已发出" })
    .toBe(true);

  // 后台核验成功：轮询推进到真实结果，展示表现、依据与提示程度。
  tasks = [succeededTask];
  records = [record];
  await expect(panel.getByText("已核验的知识点")).toBeVisible({
    timeout: 10_000,
  });
  await expect(panel.getByText("核验失败")).toBeHidden();
  await expect(panel.getByText("待后台核验")).toBeHidden();
  await expect(
    panel.getByText(/（概率论 · 理解事件关系）：独立完成/),
  ).toBeVisible();
  await expect(panel.getByText(/表现：能说明互斥不等于独立/)).toBeVisible();
  await expect(panel.getByText("依据与证据（1 条）")).toBeVisible();
  await panel.getByText("依据与证据（1 条）").click();
  await expect(panel.getByText(/作答前无提示/)).toBeVisible();

  // 刷新后保持：档案与核验状态来自持久化投影。
  await page.reload();
  await page.getByTestId("study-details-open").click();
  const refreshed = page.locator('[aria-label="学习档案"]');
  await expect(
    refreshed.getByText(/（概率论 · 理解事件关系）：独立完成/),
  ).toBeVisible();
  await expect(
    refreshed.getByText(/表现：能说明互斥不等于独立/),
  ).toBeVisible();
});
