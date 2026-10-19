import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow } from "./helpers/issue16";
import {
  assistantCard,
  citation,
  CONVERSATION_ID,
  installClipboardProbe,
  message,
  NOW,
  openConversation,
  post,
  reply,
  tieba,
  turnResult,
} from "./helpers/issue13";

/**
 * 工单 13：完整旅程的进度、引用和历史结果可用。
 *
 * 本文件用真实历史投影结构驱动桌面 UI：等待认证、有证据无总结、零证据、
 * 取消、耗尽、旧记录、尝试历史、复制与刷新。进度节点测试使用持久化的
 * active_run 认证节点，验证刷新后仍能看到真实等待阶段。
 */

test.describe("Issue 13 贴吧交付与历史", () => {
  test("成功、等待、无总结、零证据与旧记录各自保留真实边界", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await installClipboardProbe(page);

    const nestedCitation = citation({
      citation_id: "E2",
      reply_id: "n5",
      parent_reply_id: "reply-4",
      floor: 4,
      nested: true,
      author: "纠正者",
      posted_at: null,
      quote: "更正：北区是十一点半停水，不是十一点。",
    });
    await openConversation(page, [
      message("u-1", "user", "贴吧里最近宿舍条件怎么样"),
      message(
        "a-1-old",
        "assistant",
        "第一次尝试：已读取第 1/2 页。证据边界：未读部分不进入结论。",
        null,
        { attempt_number: 1, status: "error", error_message: "页面读取中断。" }
      ),
      message(
        "a-1",
        "assistant",
        "总结：已读楼层提到空调与热水刷卡。\n已读范围：已读取 1/2 页。\n证据边界：未读部分不进入结论。",
        tieba({
          summary: "已读楼层提到，南区宿舍有空调，热水需要刷卡。",
          summary_citations: [citation(), nestedCitation],
          summary_claims: [
            {
              kind: "finding",
              text: "已读楼层提到，南区宿舍有空调，热水需要刷卡。",
              citation_ids: ["E1"],
            },
          ],
          confirmed_posts: [post()],
          evidence_boundary: ["未读部分不进入结论。"],
        }),
        {
          attempt_number: 2,
          turn_result: turnResult({ trust: "evidence_bound" }),
        }
      ),
      message("u-auth", "user", "需要登录的宿舍问题"),
      message(
        "a-auth",
        "assistant",
        "等待你完成贴吧登录或安全验证。",
        tieba({
          status: "waiting_auth",
          pending: {
            module_id: "tieba",
            kind: "authentication",
            question: "请完成专用贴吧会话的登录或安全验证。",
            origin_message_id: "a-auth",
            context: {},
            created_at: NOW,
          },
          recovery: {
            action: "continue_auth",
            automatic: true,
            reuses_saved_evidence: true,
            failed_stage: null,
            scope_note: "完成验证后自动续接；若窗口已关闭，回复“继续”。",
          },
        }),
        {
          turn_result: turnResult({
            outcome: "needs_input",
            outcome_label: "等待你补充信息后继续。",
            trust: null,
            trust_label: null,
            delivered: [],
            capability_list: [],
            wait_reason: "tieba_authentication",
          }),
        }
      ),
      message("u-no-summary", "user", "有证据但没有总结的宿舍问题"),
      message(
        "a-no-summary",
        "assistant",
        "服务端正文：已读楼层提到空调与热水。",
        tieba({
          summary: null,
          summary_note: "总结未完整生成。",
          retryable: false,
          recovery: {
            action: "retry_incomplete_stage",
            automatic: false,
            reuses_saved_evidence: true,
            failed_stage: "tieba.synthesize",
            scope_note: "只会重做未完成的总结阶段，已保存页面与检索会复用。",
          },
          confirmed_posts: [post()],
          evidence_boundary: ["未读部分不进入结论。"],
        }),
        { turn_result: turnResult({ trust: "evidence_bound" }) }
      ),
      message("u-empty", "user", "贴吧里没有召回的宿舍问题"),
      message(
        "a-empty",
        "assistant",
        "本轮没有召回帖子。",
        tieba({
          status: "empty",
          empty_reason: "本轮检索没有返回可确认属于目标贴吧的公开帖子。",
        }),
        {
          turn_result: turnResult({
            outcome: "blocked",
            outcome_label: "本轮未取得贴吧证据。",
            trust: null,
            trust_label: null,
            delivered: [],
            blocked: [
              {
                module_id: "tieba",
                label: "贴吧",
                state: "empty",
                trust: "evidence_bound",
                detail: "没有召回可确认的帖子。",
              },
            ],
          }),
        }
      ),
      message("u-legacy", "user", "旧版本的宿舍记录"),
      message(
        "a-legacy",
        "assistant",
        "旧记录仍按当时读取范围保留。",
        tieba({
          summary: "旧记录只保留当时已读的楼层。",
          summary_citations: [citation({ citation_id: "E9" })],
          confirmed_posts: [
            post({
              pages_read: 0,
              pages_limit: 0,
              pages_complete: false,
              page_evidence: undefined,
              replies: [reply()],
              read_error_message: null,
            }),
          ],
        }),
        { turn_result: turnResult({ trust: "evidence_bound" }) }
      ),
    ]);

    const success = assistantCard(page, "南区宿舍有空调");
    await expect(success.getByTestId("tieba-summary")).toContainText(
      "南区宿舍有空调"
    );
    await expect(success.getByText("服务端已提交的同一份总结")).toHaveCount(0);

    const firstTrigger = success.getByTestId("tieba-citation-E1");
    await firstTrigger.focus();
    await page.keyboard.press("Enter");
    const panel = page.getByTestId("tieba-evidence-panel");
    await expect(panel).toBeVisible();
    await expect(page.getByTestId("tieba-evidence-location")).toContainText(
      "发言者 吧友甲"
    );
    await expect(page.getByTestId("tieba-evidence-location")).toContainText(
      "发言时间 2026-09-20 21:14"
    );
    await expect(page.getByTestId("tieba-evidence-scope")).toContainText(
      "已读 1/2 页"
    );
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(firstTrigger).toBeFocused();

    await success.getByTestId("tieba-citation-E2").click();
    await expect(page.getByTestId("tieba-evidence-location")).toContainText(
      "楼中楼（第 4 楼）"
    );
    await expect(page.getByTestId("tieba-evidence-location")).toContainText(
      "发言时间未给出"
    );
    await expect(page.getByTestId("tieba-evidence-parent-context")).toContainText(
      "北区热水十一点就停了"
    );
    await page.getByTestId("tieba-evidence-close").click();

    const toolbar = success.getByRole("toolbar", { name: "消息操作" });
    await toolbar.getByRole("button", { name: "复制", exact: true }).click();
    await expect(toolbar.getByText("已复制", { exact: true })).toBeVisible();
    const copied = await page.evaluate(
      () =>
        (window as unknown as { __copiedText?: string }).__copiedText ?? ""
    );
    expect(copied).toContain("证据边界：未读部分不进入结论");
    expect(copied).toContain("已读取 1/2 页");

    await success.getByTestId("attempt-history").getByText(
      "此问题的前 1 次尝试"
    ).click();
    await expect(success.getByTestId("attempt-content-1")).toContainText(
      "证据边界"
    );

    const waiting = assistantCard(page, "专用贴吧会话的登录或安全验证");
    await expect(waiting.getByTestId("public-process-label")).toHaveText(
      "等待你完成贴吧登录或安全验证"
    );
    await expect(waiting.getByTestId("tieba-pending")).toHaveAttribute(
      "role",
      "status"
    );
    await expect(waiting.getByTestId("delivery-outcome")).toContainText(
      "等待你完成贴吧登录或安全验证"
    );
    await expect(page.getByText("tieba_authentication")).toHaveCount(0);

    const noSummary = assistantCard(page, "总结未完整生成");
    await expect(
      noSummary.getByRole("button", { name: "重试未完成阶段" })
    ).toBeVisible();
    await expect(noSummary.getByTestId("tieba-summary-note")).toContainText(
      "总结未完整生成"
    );

    const empty = assistantCard(
      page,
      "本轮检索没有返回可确认属于目标贴吧的公开帖子"
    );
    await expect(empty.getByTestId("tieba-empty-guidance")).toContainText(
      "未召回不代表吧内没有相关讨论"
    );

    const legacy = assistantCard(page, "旧记录只保留当时已读的楼层");
    await legacy.getByTestId("tieba-citation-E9").click();
    await expect(page.getByTestId("tieba-evidence-scope")).toContainText(
      "旧记录未保存分页范围"
    );
    await expect(page.getByTestId("tieba-evidence-scope")).not.toContainText(
      "已读 0/0 页"
    );
    await page.getByTestId("tieba-evidence-close").click();

    await page.reload();
    await expect(success.getByTestId("tieba-summary")).toContainText(
      "南区宿舍有空调"
    );
    await expect(waiting.getByTestId("public-process-label")).toHaveText(
      "等待你完成贴吧登录或安全验证"
    );
    await expect(empty.getByTestId("tieba-empty-guidance")).toBeVisible();
    await expect(success.getByTestId("attempt-history")).toContainText(
      "此问题的前 1 次尝试"
    );

    await page.setViewportSize({ width: 1024, height: 576 });
    await expectNoHorizontalOverflow(page);
    await expect(page.getByTestId("composer")).toBeVisible();
    await expect(success.getByTestId("tieba-citation-E1")).toBeVisible();
  });

  test("部分成功、取消与耗尽只给真实动作，不伪造完成", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    await openConversation(page, [
      message("u-partial", "user", "部分读取成功的问题"),
      message(
        "a-partial",
        "assistant",
        "部分读取成功的正文。",
        tieba({
          summary: "已读楼层提到空调与热水刷卡。",
          summary_citations: [citation()],
          confirmed_posts: [post()],
          evidence_boundary: ["未读部分不进入结论。"],
        }),
        {
          turn_result: turnResult({
            outcome: "partial",
            outcome_label: "本轮先交付有效部分，其余未完成。",
            trust: "evidence_bound",
            blocked: [],
            gaps: ["未读楼层未进入结论"],
          }),
        }
      ),
      message("u-stopped", "user", "停止贴吧读取"),
      message(
        "a-stopped",
        "assistant",
        "已停止贴吧读取。",
        tieba({
          status: "stopped",
          recovery: {
            action: "none",
            automatic: false,
            reuses_saved_evidence: true,
            failed_stage: null,
            scope_note: "已停止，不会自动继续；已保存证据会按当前条件复用。",
          },
        }),
        {
          status: "stopped",
          turn_result: turnResult({
            outcome: "cancelled",
            outcome_label: "本轮已停止，不会自动继续；已生成的内容保留。",
            trust: null,
            trust_label: null,
          }),
        }
      ),
      message("u-exhausted", "user", "预算耗尽的贴吧问题"),
      message(
        "a-exhausted",
        "assistant",
        "本轮运行预算已用尽。",
        tieba({
          status: "error",
          error_code: "tieba_budget_exhausted",
          error_message: "本轮运行预算已用尽，已保留已读证据。",
          retryable: true,
          recovery: {
            action: "retry_run",
            automatic: false,
            reuses_saved_evidence: true,
            failed_stage: "tieba.read",
            scope_note: "重试会复用已保存的检索与页面证据。",
          },
        }),
        {
          status: "error",
          error_message: "本轮运行预算已用尽，已保留已读证据。",
          turn_result: turnResult({
            outcome: "failed",
            outcome_label: "本轮未完成。",
            trust: null,
            trust_label: null,
            delivered: [],
            recovery: {
              action: "retry",
              label: "已保留证据，可重试本轮。",
              retryable: true,
              available_after: null,
            },
          }),
        }
      ),
    ]);

    const partial = assistantCard(page, "空调与热水刷卡。");
    await expect(partial.getByTestId("delivery-status")).toHaveAttribute(
      "data-outcome",
      "partial"
    );
    await expect(partial.getByTestId("tieba-read-boundary")).toContainText(
      "未读部分不进入结论"
    );
    await expect(partial.getByTestId("delivery-retry")).toHaveCount(0);
    await expect(
      partial
        .getByRole("toolbar", { name: "消息操作" })
        .getByRole("button", { name: "重新生成" })
    ).toBeVisible();

    const stopped = assistantCard(page, "已停止，不会自动继续");
    await expect(stopped.getByTestId("delivery-status")).toHaveAttribute(
      "data-outcome",
      "cancelled"
    );
    await expect(stopped.getByTestId("delivery-retry")).toHaveText("继续生成");
    await expect(stopped.getByTestId("tieba-stopped")).toContainText(
      "不会自动继续"
    );

    const exhausted = assistantCard(page, "本轮运行预算已用尽");
    await expect(exhausted.getByTestId("delivery-status")).toHaveAttribute(
      "data-outcome",
      "failed"
    );
    await expect(exhausted.getByTestId("delivery-recovery")).toContainText(
      "已保留证据，可重试本轮"
    );
    await expect(exhausted.getByTestId("delivery-retry")).toHaveText("重试本轮");
  });

  test("刷新生成中的贴吧回合仍从 active_run 显示真实认证等待节点", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await installClipboardProbe(page);
    await page.route(
      `**/api/chat/conversations/${CONVERSATION_ID}/messages/a-live/events**`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: "",
        });
      }
    );

    await openConversation(page, [
      message("u-live", "user", "正在等待登录的宿舍问题"),
      message(
        "a-live",
        "assistant",
        "",
        tieba({ status: "searching" }),
        {
          status: "streaming",
          duration_ms: null,
          active_run: {
            run_id: "run-live",
            status: "running",
            stage: null,
            cursor: 0,
            attempt_count: 1,
            created_at: NOW,
            updated_at: NOW,
            graph_version: "daily-v2",
            current_node: "tieba.auth",
            wait_reason: null,
            model_lock_id: null,
          },
        }
      ),
    ]);
    const live = assistantCard(page, "等待贴吧登录");
    const label = live.getByTestId("public-process-label");
    await expect(label).toHaveText("等待贴吧登录或安全验证…");
    await expect(label).toHaveAttribute("role", "status");
    await expect(label).toHaveAttribute("aria-live", "polite");
    await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();
  });
});
