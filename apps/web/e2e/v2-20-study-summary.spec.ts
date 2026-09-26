import { expect, test } from "@playwright/test";

// 界面使用受控 API 替身；总结生成、持久化与隔离另由聊天 API 集成测试验证。
const NOW = "2026-09-26T00:00:00Z";
for (const [width, height] of [[1280, 720], [1440, 900], [1920, 1080]]) {
  test(`学习总结三段与同节追问 ${width}×${height}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height });
    await page.context().addCookies([{ name: "bridges_session", value: "summary-test", url: testInfo.project.use.baseURL! }]);
    const account = { id: "study-owner", username: "学习测试", qq_email: "12345@qq.com",
      avatar_choice: "initials", created_at: NOW, updated_at: NOW };
    const conversation = {
      conversation_id: "summary-test", title: "线性函数", mode: "study", mode_locked: true,
      pinned: false, created_at: NOW, updated_at: NOW, mode_events: [],
      messages: [{ message_id: "a1", conversation_id: "summary-test", role: "assistant",
        attempt_number: 1, status: "done", created_at: NOW, updated_at: NOW,
        content: "回答正确。\n\n正确答案：a 是斜率，b 是纵截距。\n\n本节学习总结（依据本节书页与复盘已判定题）" }],
      study: {
        subsection_id: "summary-test", stage: "review",
        pages: [{ ordinal: 1, object_id: "photo-1", content_hash: "hash-1", model_id: "m",
          page_number: 12, same_section: true, replaced_object_ids: [], unclear: [],
          fragments: [{ fragment_id: "photo-1:1", kind: "formula", position: "中部公式",
            text: "y=ax+b", confidence: 0.9, source: "photo" }] }],
        review: { complete: false, needs_replan: false, questions: [
          { question_id: "q1", question: "a 的含义是什么？", coverage_units: ["线性函数"],
            fragment_ids: ["photo-1:1"], asked: true, judgement: "correct" }] },
        summary: null as null | { points: { kind: string; text: string;
          question_ids?: string[]; fragment_ids?: string[] }[] },
      },
    };
    let sent: Record<string, unknown> | undefined;
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = {};
      if (path === "/api/auth/session") body = { account, session: {}, subject: {} };
      else if (path === "/api/auth/device/accounts") body = { accounts: [], current_account: account };
      else if (path === "/api/chat/conversations") body = { conversations: [conversation] };
      else if (path === "/api/chat/conversations/summary-test") body = conversation;
      else if (path === "/api/chat/attachment-drafts") body = [];
      else if (path.endsWith("/messages") && route.request().method() === "POST") {
        sent = route.request().postDataJSON();
        await route.fulfill({ json: { assistant_message: { message_id: "a2" } } });
        return;
      }
      await route.fulfill({ json: body });
    });
    await page.goto("/chat/summary-test");
    const progress = page.getByRole("region", { name: "学习阶段" });
    await expect(progress.getByText("复盘", { exact: true })).toHaveAttribute("aria-current", "step");
    await expect(progress.getByText("学到了什么")).toHaveCount(0);

    // 最后一题判定完成后进入总结：三段分述，逐条附上题目判定与书页依据。
    conversation.study.stage = "summary";
    conversation.study.review.complete = true;
    conversation.study.summary = { points: [
      { kind: "learned", text: "本节讲线性函数 y=ax+b 的斜率与截距。", fragment_ids: ["photo-1:1"] },
      { kind: "mastered", text: "能解释斜率与截距的含义。", question_ids: ["q1"] },
    ] };
    conversation.messages[0].content =
      "回答正确。\n\n正确答案：a 是斜率，b 是纵截距。\n\n本节学习总结（依据本节书页与复盘已判定题）"
      + "\n\n学到了什么：\n- 本节讲线性函数 y=ax+b 的斜率与截距。\n  依据：上传第1页（书上第12页） · 中部公式"
      + "\n\n复盘已掌握：\n- 能解释斜率与截距的含义。\n  依据：第1题「a 的含义是什么？」判定为正确"
      + "\n\n还需补的点：\n- 本次复盘的题目全部答对，暂无待补的理解点。";
    await page.reload();
    await expect(progress.getByText("总结", { exact: true })).toHaveAttribute("aria-current", "step");
    await expect(progress.getByText("学到了什么")).toBeVisible();
    await expect(progress.getByText(/上传第1页（书上第12页） · 中部公式/)).toBeVisible();
    await expect(progress.getByText(/判定为正确/)).toBeVisible();
    await expect(page.getByText(/本次复盘的题目全部答对/).first()).toBeVisible();
    await expect(progress.getByRole("button")).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath(`summary-${width}.png`), fullPage: true });

    // 同节追问仍可继续；总结与判定保留在阶段面板里。
    conversation.study.stage = "tutoring";
    await page.reload();
    await expect(progress.getByText("辅导", { exact: true })).toHaveAttribute("aria-current", "step");
    await expect(progress.getByText("学到了什么")).toBeVisible();
    await expect(progress.getByText(/上传第1页（书上第12页） · 中部公式/)).toBeVisible();
    await page.getByRole("textbox", { name: "输入消息" }).fill("再讲讲截距的几何意义");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect.poll(() => sent?.content).toBe("再讲讲截距的几何意义");
    await expect(progress.getByText("总结", { exact: true })).toBeVisible();
  });
}
