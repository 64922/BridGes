import { expect, test, type Page, type TestInfo } from "@playwright/test";

/**
 * 工单 13（学习模式重构）：发布验收的桌面切片。
 *
 * 与各票的专项脚本（issue05/06/07 等）不同，本脚本按发布口径一次性核对
 * 学习面板整体在三个桌面视口下的呈现、键盘完整操作与状态播报：
 * - 目标/当前知识点/待答问题/核验状态/已核验知识点齐全且无横向溢出；
 * - 「纠正」可以全键盘完成：Enter 打开、输入、Tab 到保存、Enter 提交；
 * - 目标恢复通知与当前待答问题通过 role=status 播报，刷新后投影保持。
 *
 * 真实服务链路的模型质量、独立诊断、公开查证与生命周期由 Python 旅程
 * （scripts/run_issue13_learning_journeys.py）与集成测试覆盖；本脚本用
 * 固定协议负载只验收界面面。
 */

const NOW = "2026-10-14T00:00:00Z";
const CONVERSATION_ID = "study-release-test";

const VIEWPORTS = [
  { width: 1280, height: 720 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
] as const;

const QUESTION = "如果 A、B 互斥，P(A∩B) 是多少？为什么？";

function conversation(records: unknown[]) {
  return {
    conversation_id: CONVERSATION_ID,
    title: "互斥与独立",
    mode: "study",
    mode_locked: true,
    pinned: false,
    created_at: NOW,
    updated_at: NOW,
    mode_events: [],
    messages: [],
    study_goal: {
      goal_id: "goal-release-1",
      statement: "我想系统学概率论，先打牢事件关系的基础",
      observable_result: "能独立完成条件概率练习",
      scope: "概率论基础",
      constraints: "",
      status: "active",
      version: 1,
      source_message_id: "m-user-1",
      created_at: NOW,
      updated_at: NOW,
      route: {
        version: 1,
        nodes: [
          { node_id: "n1", title: "互斥与独立", prerequisites: [], depth: "基础" },
          { node_id: "n2", title: "条件概率", prerequisites: ["n1"], depth: "基础" },
        ],
        current_node_id: "n1",
        revision_reason: "先巩固事件关系再进入条件概率",
        origin: "assistant_proposal",
        created_at: NOW,
      },
    },
    study_goal_notice: {
      kind: "restored",
      message:
        "已恢复学习目标「我想系统学概率论，先打牢事件关系的基础」，并按已保存的进展与位置继续。",
      goal_statement: "我想系统学概率论，先打牢事件关系的基础",
      created_at: NOW,
    },
    study_local_progress: {
      topic: "互斥与独立",
      knowledge_context: "两个事件不能同时发生。",
      current_focus: "互斥是不是独立？",
      source_message_ids: ["m-user-1", "m-assistant-1"],
      version: 2,
      runtime_run_id: "run-release-1",
      updated_at: NOW,
    },
    study_pending_check: {
      check_id: "check-release-1",
      kind: "formal_check",
      question: QUESTION,
      knowledge_context: "互斥与独立",
      assistance: "none",
      source_status: "not_applicable",
      version: 1,
      source_message_id: "m-assistant-1",
      created_at: NOW,
    },
    study_diagnosis_tasks: [
      {
        task_id: "dx-release-pending",
        status: "pending",
        question: QUESTION,
        knowledge_context: "互斥与独立",
        evidence_message_id: "m-user-2",
        failure_reason: "",
        attempts: 1,
        retryable: false,
        created_at: NOW,
        updated_at: NOW,
      },
    ],
    study_learning_record: records,
    study_record_adoptions: [],
  };
}

const RECORD = {
  record_id: "lr-release-1",
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
      evidence_id: "ev-release-1",
      question: QUESTION,
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

interface MockState {
  records: unknown[];
  postedNote?: string;
  postedVersion?: number;
}

async function mockStudyApi(
  page: Page,
  testInfo: TestInfo,
  state: MockState
): Promise<void> {
  await page.context().addCookies([
    {
      name: "bridges_session",
      value: CONVERSATION_ID,
      url: testInfo.project.use.baseURL!,
    },
  ]);
  const account = {
    id: "study-release-owner",
    username: "发布验收",
    qq_email: "12345@qq.com",
    avatar_choice: "initials",
    created_at: NOW,
    updated_at: NOW,
  };
  const corrected = {
    ...RECORD,
    record_version: 2,
    state: "corrected",
    user_note: "",
    corrected_at: NOW,
    superseded_verdict: "independent_completion",
    superseded_performance: "能说明互斥不等于独立。",
  };
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    if (path === "/api/auth/session") {
      await route.fulfill({ json: { account, session: {}, subject: {} } });
      return;
    }
    if (path === "/api/auth/device/accounts") {
      await route.fulfill({ json: { accounts: [], current_account: account } });
      return;
    }
    if (
      method === "POST" &&
      path ===
        `/api/chat/conversations/${CONVERSATION_ID}/study/records/lr-release-1/corrections`
    ) {
      const payload = JSON.parse(route.request().postData() ?? "{}");
      state.postedNote = payload.note;
      state.postedVersion = payload.expected_version;
      const saved = { ...corrected, user_note: payload.note };
      state.records = [saved];
      await route.fulfill({ json: saved });
      return;
    }
    if (path === `/api/chat/conversations/${CONVERSATION_ID}`) {
      await route.fulfill({ json: conversation(state.records) });
      return;
    }
    if (path === "/api/chat/conversations") {
      await route.fulfill({ json: { conversations: [conversation(state.records)] } });
      return;
    }
    if (path === "/api/chat/attachment-drafts") {
      await route.fulfill({ json: [] });
      return;
    }
    await route.fulfill({ json: {} });
  });
}

for (const viewport of VIEWPORTS) {
  test(`学习面板在 ${viewport.width}×${viewport.height} 视口完整无溢出`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await mockStudyApi(page, testInfo, { records: [RECORD] });
    await page.goto(`/chat/${CONVERSATION_ID}`);
    await expect(page.getByTestId("study-details-panel")).toHaveCount(0);
    await page.getByTestId("study-details-open").click();

    const goalPanel = page.getByRole("region", { name: "学习目标" });
    await expect(goalPanel).toBeVisible();
    await expect(
      goalPanel.getByText("目标：我想系统学概率论，先打牢事件关系的基础"),
    ).toBeVisible();
    await expect(goalPanel.getByText("当前知识点：互斥与独立")).toBeVisible();
    await expect(
      goalPanel.getByRole("status", { name: "目标通知" }),
    ).toContainText("已恢复学习目标");
    await expect(
      goalPanel.getByRole("status", { name: "当前待答问题" }),
    ).toContainText(`待答：${QUESTION}`);

    const recordPanel = page.locator('[aria-label="学习档案"]');
    await expect(recordPanel).toBeVisible();
    await expect(recordPanel.getByText(/待后台核验/)).toBeVisible();
    await expect(
      recordPanel.getByText("已核验的知识点"),
    ).toBeVisible();
    await expect(
      recordPanel.getByText(/互斥与独立（概率论 · 理解事件关系）：独立完成/),
    ).toBeVisible();

    const overflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth
    );
    expect(overflow).toBeLessThanOrEqual(1);
    await page.screenshot({
      path: testInfo.outputPath(`study-panel-${viewport.width}x${viewport.height}.png`),
      fullPage: true,
    });
  });
}

test("键盘完成纠正：Enter 打开、输入、Tab 到保存、Enter 提交并刷新保持", async ({
  page,
}, testInfo) => {
  const state: MockState = { records: [RECORD] };
  await mockStudyApi(page, testInfo, state);
  await page.goto(`/chat/${CONVERSATION_ID}`);
  await page.getByTestId("study-details-open").click();

  const panel = page.locator('[aria-label="学习档案"]');
  const correctButton = panel.getByRole("button", { name: "纠正" });
  await correctButton.focus();
  await expect(correctButton).toBeFocused();
  await page.keyboard.press("Enter");

  const textarea = panel.getByLabel("纠正内容");
  await expect(textarea).toBeVisible();
  await textarea.focus();
  await page.keyboard.type("我当时其实理解了互斥的差别，只是题目答错了细节。");
  await expect(textarea).toHaveValue(
    "我当时其实理解了互斥的差别，只是题目答错了细节。"
  );

  await page.keyboard.press("Tab");
  const saveButton = panel.getByRole("button", { name: "保存" });
  await expect(saveButton).toBeFocused();
  await page.keyboard.press("Enter");
  await expect
    .poll(() => [state.postedNote, state.postedVersion], {
      message: "纠正请求已按记录版本提交",
    })
    .toEqual(["我当时其实理解了互斥的差别，只是题目答错了细节。", 1]);

  await expect(panel.getByText(/用户纠正（自评，未经核验）/)).toBeVisible();
  await expect(
    panel.getByText(/我的纠正（自评，未经核验）：我当时其实理解了互斥的差别/),
  ).toBeVisible();
  await expect(textarea).toBeHidden();

  await page.reload();
  await page.getByTestId("study-details-open").click();
  await expect(
    page
      .locator('[aria-label="学习档案"]')
      .getByText(/用户纠正（自评，未经核验）/),
  ).toBeVisible();
});
