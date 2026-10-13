import { expect, test } from "@playwright/test";

// Issue 07（学习模式重构）：新目标到首个教学节点的桌面旅程。
// 桌面只验证展示与刷新保持；目标创建、规划调用与原子提交由
// tests/chat/test_issue07_goals_and_learning_route.py 走真实服务链路覆盖。
const NOW = "2026-10-13T00:00:00Z";

test("顶部显示学习目标与当前知识点，路线按需展开且刷新后保持", async ({
  page,
}, testInfo) => {
  await page.context().addCookies([
    {
      name: "bridges_session",
      value: "goal-test",
      url: testInfo.project.use.baseURL!,
    },
  ]);
  const account = {
    id: "goal-owner",
    username: "目标测试",
    qq_email: "12345@qq.com",
    avatar_choice: "initials",
    created_at: NOW,
    updated_at: NOW,
  };
  const goal = {
    goal_id: "goal-1",
    statement: "我想学线性代数，主要为了理解机器学习里的矩阵",
    observable_result: "能用矩阵运算理解机器学习里的矩阵",
    scope: "线性代数基础与机器学习常用矩阵运算",
    constraints: "不要布置作业",
    status: "active",
    version: 1,
    source_message_id: "m-user-1",
    created_at: NOW,
    updated_at: NOW,
    route: {
      version: 2,
      nodes: [
        { node_id: "n1", title: "向量与线性组合", prerequisites: [], depth: "基础" },
        { node_id: "n2", title: "矩阵与矩阵乘法", prerequisites: ["n1"], depth: "基础" },
        { node_id: "n3", title: "特征值与特征向量", prerequisites: ["n2"], depth: "深入" },
      ],
      current_node_id: "n2",
      revision_reason: "按你的要求先学矩阵",
      origin: "user_request",
      created_at: NOW,
    },
  };
  const conversation = {
    conversation_id: "goal-test",
    title: "线性代数学习",
    mode: "study",
    mode_locked: true,
    pinned: false,
    created_at: NOW,
    updated_at: NOW,
    mode_events: [],
    study_goal: goal,
    study_local_progress: {
      topic: "线性代数学习",
      knowledge_context: "矩阵乘法规则。",
      current_focus: "继续讲矩阵乘法",
      source_message_ids: ["m-user-1", "m-assistant-1"],
      version: 2,
      runtime_run_id: "run-1",
      updated_at: NOW,
    },
    messages: [
      {
        message_id: "m-user-1",
        conversation_id: "goal-test",
        role: "user",
        attempt_number: 1,
        status: "done",
        created_at: NOW,
        updated_at: NOW,
        content: "我想学线性代数，主要为了理解机器学习里的矩阵",
      },
      {
        message_id: "m-assistant-1",
        conversation_id: "goal-test",
        role: "assistant",
        attempt_number: 1,
        status: "done",
        created_at: NOW,
        updated_at: NOW,
        content: "我们从矩阵与矩阵乘法开始：先看矩阵如何表示线性组合。",
      },
    ],
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/auth/session") body = { account, session: {}, subject: {} };
    else if (path === "/api/auth/device/accounts")
      body = { accounts: [], current_account: account };
    else if (path === "/api/chat/conversations") body = { conversations: [conversation] };
    else if (path === "/api/chat/conversations/goal-test") body = conversation;
    else if (path === "/api/chat/attachment-drafts") body = [];
    await route.fulfill({ json: body });
  });

  await page.goto("/chat/goal-test");
  const progress = page.getByRole("region", { name: "学习目标" });
  await expect(
    progress.getByText("目标：我想学线性代数，主要为了理解机器学习里的矩阵"),
  ).toBeVisible();
  await expect(progress.getByText("当前知识点：矩阵与矩阵乘法")).toBeVisible();
  await expect(progress.getByText("学习路线（版本 2，3 个知识点）")).toBeVisible();

  // 路线默认收起，按需展开后逐节点可见；不出现任何掌握宣称。
  const routeDetails = progress.locator("details");
  await expect(routeDetails).not.toHaveAttribute("open", "");
  await progress.getByText("学习路线（版本 2，3 个知识点）").click();
  await expect(progress.getByText("特征值与特征向量")).toBeVisible();
  await expect(progress.getByText("修订依据：按你的要求先学矩阵")).toBeVisible();
  await expect(
    progress.getByText("路线是可调整的导师方案；跳到某节点不代表前面的内容已完成。"),
  ).toBeVisible();
  await expect(progress.getByText(/已掌握/)).toHaveCount(0);
  await expect(page.getByText(/我们从矩阵与矩阵乘法开始/)).toBeVisible();

  // 刷新后目标、当前知识点与路线版本保持（服务端投影为准）。
  await page.reload();
  await expect(progress.getByText("当前知识点：矩阵与矩阵乘法")).toBeVisible();
  await expect(progress.getByText("学习路线（版本 2，3 个知识点）")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("goals-and-learning-route.png"),
    fullPage: true,
  });
});
