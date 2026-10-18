import { expect, test, type Page, type TestInfo } from "@playwright/test";

/**
 * 工单 12：学习讲解、作答反馈与学习详情按需查看的桌面旅程。
 *
 * 固定协议负载只验收呈现与交互；真实教学执行、诊断归并与原子提交由
 * tests/chat/test_issue04_learner_controls.py、test_issue05_durable_diagnosis.py
 * 与 test_issue09_adaptive_teaching.py 走服务链路覆盖。
 */
const NOW = "2026-10-18T00:00:00Z";
const CONVERSATION_ID = "issue12-learning";
const QUESTION = "如果 A、B 互斥，P(A∩B) 是多少？为什么？";

const ACCOUNT = {
  id: "issue12-owner",
  username: "学习呈现",
  qq_email: "12345@qq.com",
  avatar_choice: "initials",
  created_at: NOW,
  updated_at: NOW,
};

const GOAL = {
  goal_id: "goal-1",
  statement: "系统学概率论，先理解事件关系",
  observable_result: "能独立解释互斥与独立",
  scope: "概率论基础",
  constraints: "",
  status: "active",
  version: 1,
  source_message_id: "u1",
  created_at: NOW,
  updated_at: NOW,
  route: {
    version: 1,
    nodes: [
      { node_id: "n1", title: "互斥与独立", prerequisites: [], depth: "基础" },
    ],
    current_node_id: "n1",
    revision_reason: "先巩固事件关系",
    origin: "assistant_proposal",
    created_at: NOW,
  },
};

const LOCAL_PROGRESS = {
  topic: "互斥与独立",
  knowledge_context: "两个事件不能同时发生。",
  current_focus: "互斥是不是独立？",
  source_message_ids: ["u1", "a1"],
  version: 1,
  runtime_run_id: "run-1",
  updated_at: NOW,
};

function message(messageId: string, role: "user" | "assistant", content: string) {
  return {
    message_id: messageId,
    conversation_id: CONVERSATION_ID,
    role,
    attempt_number: 1,
    status: "done",
    content,
    created_at: NOW,
    updated_at: NOW,
  };
}

function conversation(overrides: Record<string, unknown> = {}) {
  return {
    conversation_id: CONVERSATION_ID,
    title: "互斥与独立",
    mode: "study",
    mode_locked: true,
    pinned: false,
    created_at: NOW,
    updated_at: NOW,
    mode_events: [],
    messages: [
      message("u1", "user", "开始学互斥与独立"),
      message("a1", "assistant", `先检查一个关键点。${QUESTION}`),
    ],
    study_goal: GOAL,
    study_local_progress: LOCAL_PROGRESS,
    ...overrides,
  };
}

async function mockApi(
  page: Page,
  testInfo: TestInfo,
  getConversation: () => unknown,
  onRequest?: (path: string, method: string, body: unknown) => unknown,
) {
  await page.context().addCookies([{
    name: "bridges_session",
    value: CONVERSATION_ID,
    url: testInfo.project.use.baseURL!,
  }]);
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    const body = route.request().postData()
      ? JSON.parse(route.request().postData() ?? "{}")
      : null;
    const custom = onRequest?.(path, method, body);
    if (custom !== undefined) {
      await route.fulfill({ json: custom });
      return;
    }
    if (path === "/api/auth/session") {
      await route.fulfill({ json: { account: ACCOUNT, session: {}, subject: {} } });
    } else if (path === "/api/auth/device/accounts") {
      await route.fulfill({ json: { accounts: [], current_account: ACCOUNT } });
    } else if (path === `/api/chat/conversations/${CONVERSATION_ID}`) {
      await route.fulfill({ json: getConversation() });
    } else if (path === "/api/chat/conversations") {
      await route.fulfill({ json: { conversations: [getConversation()] } });
    } else if (path === "/api/chat/attachment-drafts") {
      await route.fulfill({ json: [] });
    } else {
      await route.fulfill({ json: {} });
    }
  });
}

test("问题留在正文，学习详情默认关闭，Esc 关闭后焦点回到入口", async ({
  page,
}, testInfo) => {
  await mockApi(page, testInfo, () => conversation({
    study_pending_check: {
      check_id: "check-1",
      kind: "formal_check",
      question: QUESTION,
      knowledge_context: "互斥与独立",
      assistance: "none",
      source_status: "available",
      version: 1,
      source_message_id: "a1",
      created_at: NOW,
    },
  }));
  await page.goto(`/chat/${CONVERSATION_ID}`);

  await expect(page.getByTestId("study-status-bar")).toContainText(
    "目标：系统学概率论，先理解事件关系",
  );
  await expect(page.getByTestId("study-status-bar")).toContainText("待你作答");
  await expect(page.getByRole("region", { name: "学习目标" })).toHaveCount(0);
  await expect(page.getByTestId("study-details-panel")).toHaveCount(0);
  await expect(
    page.getByTestId("assistant-message").getByText(QUESTION, { exact: false }).first(),
  ).toBeVisible();

  const turn = page.getByTestId("study-turn-status");
  await expect(turn).toContainText("本轮形成学习目标");
  await expect(turn).toContainText("待你作答");
  await expect(turn).toContainText(QUESTION);

  const trigger = page.getByTestId("study-details-open");
  await trigger.click();
  const panel = page.getByTestId("study-details-panel");
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("region", { name: "学习目标" })).toContainText(
    "当前知识点：互斥与独立",
  );
  await expect(panel.getByLabel("当前待答问题")).toContainText(`待答：${QUESTION}`);

  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("诊断异步失败保留解释，并在本轮提供真实重试", async ({ page }, testInfo) => {
  let retryPosted = false;
  let diagnosisStatus = "failed";
  const task = {
    task_id: "dx-1",
    status: diagnosisStatus,
    question: QUESTION,
    knowledge_context: "互斥与独立",
    evidence_message_id: "u2",
    failure_reason: "诊断服务暂时不可用。",
    attempts: 2,
    retryable: true,
    created_at: NOW,
    updated_at: NOW,
  };
  await mockApi(
    page,
    testInfo,
    () => conversation({
      messages: [
        message("u1", "user", "开始学互斥与独立"),
        message("a1", "assistant", `先检查一个关键点。${QUESTION}`),
        message("u2", "user", "P(A∩B)=0，因为互斥不能同时发生。"),
        message("a2", "assistant", "解释仍然保留：你答对了交集为空。"),
      ],
      study_diagnosis_tasks: [{
        ...task,
        status: diagnosisStatus,
        failure_reason: diagnosisStatus === "failed"
          ? "诊断服务暂时不可用。"
          : "",
        retryable: diagnosisStatus === "failed",
      }],
    }),
    (path, method) => {
      if (
        method === "POST"
        && path === `/api/chat/conversations/${CONVERSATION_ID}/study/diagnosis/dx-1/retry`
      ) {
        retryPosted = true;
        diagnosisStatus = "pending";
        return { ...task, status: "pending", failure_reason: "", retryable: false };
      }
      return undefined;
    },
  );
  await page.goto(`/chat/${CONVERSATION_ID}`);

  const turn = page.getByTestId("study-turn-status").last();
  await expect(turn).toContainText("后台核验失败");
  await expect(turn).toContainText("诊断服务暂时不可用");
  await expect(page.getByText("解释仍然保留：你答对了交集为空。")).toBeVisible();

  await turn.getByRole("button", { name: "重试核验" }).click();
  await expect.poll(() => retryPosted).toBe(true);
  await expect(turn).toContainText("待后台核验");
  await expect(turn).toContainText("已生成的解释保留");
});

test("小结、跳过与采用依据归属本轮，详情中仍可纠正和查看", async ({
  page,
}, testInfo) => {
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
    evidences: [{
      evidence_id: "ev-1",
      question: QUESTION,
      knowledge_context: "互斥与独立",
      assistance: "independent",
      verdict: "independent_completion",
      answer_message_id: "u2",
      feedback_message_id: "a2",
      occurred_at: NOW,
      source_available: true,
      source_note: "",
    }],
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
  await mockApi(page, testInfo, () => conversation({
    messages: [
      message("u1", "user", "开始学互斥与独立"),
      message("a1", "assistant", `先检查一个关键点。${QUESTION}`),
      message("u2", "user", "这题跳过吧。"),
      message("a2", "assistant", "好，这次先跳过；下面是阶段小结。"),
    ],
    study_last_control: {
      control_id: "control-1",
      kind: "skip",
      question: QUESTION,
      released: true,
      assistant_message_id: "a2",
      created_at: NOW,
    },
    study_summary: {
      summary_id: "summary-1",
      trigger: "explicit_request",
      status: "complete",
      content: "【本阶段小结】\n本次保留问题未验证。",
      source_message_id: "u2",
      assistant_message_id: "a2",
      created_at: NOW,
    },
    study_record_adoptions: [{
      adoption_id: "ad-1",
      goal_id: GOAL.goal_id,
      goal_version: GOAL.version,
      route_version: GOAL.route.version,
      status: "applied",
      invalid_reason: "",
      uncertainty_note: "",
      assistant_message_id: "a2",
      created_at: NOW,
      entries: [{
        record_id: "lr-1",
        record_version: 1,
        subject: "概率论",
        title: "互斥与独立",
        usage_context: "理解事件关系",
        verdict: "independent_completion",
        performance: "能说明互斥不等于独立。",
        adjustments: ["recheck"],
        reason: "继续验证一次。",
      }],
    }],
    study_learning_record: [record],
  }));
  await page.goto(`/chat/${CONVERSATION_ID}`);

  const turn = page.getByTestId("study-turn-status").last();
  await expect(turn).toContainText("未验证");
  await expect(turn).toContainText("阶段小结");
  await expect(turn).toContainText("已采用学情");

  const summaryButton = turn.getByRole("button", { name: "查看阶段小结" });
  await summaryButton.click();
  const panel = page.getByTestId("study-details-panel");
  await expect(panel).toBeVisible();
  await expect(panel.getByText(/教学采用的学情依据（1 次）/)).toBeVisible();
  await expect(panel.locator('[aria-label="学习档案"]')).toBeVisible();
  await expect(panel.getByRole("button", { name: "纠正" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "删除" })).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
  await expect(summaryButton).toBeFocused();
});

test("旧书页历史保持只读，只有用户打开详情后才可主动续学", async ({
  page,
}, testInfo) => {
  let restartOptionsRequested = false;
  await mockApi(
    page,
    testInfo,
    () => conversation({
      study_goal: null,
      study_local_progress: null,
      study: { subsection_id: "legacy", stage: "tutoring", state_version: 4 },
      study_history_status: {
        status: "legacy",
        version: 3,
        message: "此学习记录采用旧格式（版本 3）；已按原记录只读展示。",
      },
    }),
    (path) => {
      if (path.endsWith("/study/restart-options")) {
        restartOptionsRequested = true;
        return {
          conversation_id: CONVERSATION_ID,
          can_restart: false,
          linked_goal_id: null,
          goals: [],
          materials: [],
        };
      }
      return undefined;
    },
  );
  await page.goto(`/chat/${CONVERSATION_ID}`);

  await expect(page.getByTestId("study-status-bar")).toContainText(
    "旧学习记录（只读）",
  );
  await expect(page.getByTestId("study-details-panel")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "学习阶段" })).toHaveCount(0);
  await expect(page.getByText("书页识别")).toHaveCount(0);
  expect(restartOptionsRequested).toBe(false);

  await page.getByTestId("study-details-open").click();
  const panel = page.getByTestId("study-details-panel");
  await expect(panel.getByText(/按原始记录只读展示的历史/)).toBeVisible();
  await expect(panel.getByText("辅导")).toBeVisible();
  expect(restartOptionsRequested).toBe(false);

  await panel.getByRole("button", { name: "主动续学" }).click();
  await expect.poll(() => restartOptionsRequested).toBe(true);
  await expect(panel.getByText(/当前没有可沿用的学习目标/)).toBeVisible();
});
