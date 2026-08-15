import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { HumanizerResultProjection } from "@/lib/api";

import { HumanizerResultCard } from "./HumanizerResultCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const clipMock = vi.fn(async (_text: string): Promise<boolean> => true);

vi.mock("@/lib/clipboard", () => ({
  copyTextToClipboard: (text: string) => clipMock(text),
}));

const trackMock = vi.fn();

vi.mock("@/lib/article-projection-telemetry", () => ({
  collectRiskTypes: () => ["fidelity_blocking"],
  trackArticleProjectionEvent: (
    payload: unknown,
    kind: string
  ) => trackMock(payload, kind),
}));

/** 成功无风险投影（Issue 08：正文优先、无固定前言、无空审计段）。 */
function cleanResult(): HumanizerResultProjection {
  return {
    task_id: "task-1",
    skill_id: "bridges-humanizer",
    skill_version: "1.0.0",
    path: "rewrite",
    genre: null,
    contract: { path: "rewrite", source_text: "原文", allow_assumptions: false, allow_first_person: false },
    status: "done",
    repair_attempts: 0,
    writing_call_count: 1,
    process_state: "done",
    output: {
      final_text: "光合作用把光能转化为化学能。",
      edits: [],
      fact_check: [],
      open_questions: [],
      quality_status: "ok",
    },
    article: {
      projection_version: "1",
      audit_version: "1",
      delivery_status: "delivered",
      material_state: "sufficient",
      final_text: "光合作用把光能转化为化学能。",
      fidelity: {
        passed: true,
        blocking_count: 0,
        needs_confirmation_count: 0,
        items: [],
      },
      style_review: {
        finding_count: 0,
        warning_count: 0,
        suggestion_count: 0,
        items: [],
      },
      revision: null,
      evidence: [],
      confirmations: [],
    },
  };
}

/** 硬门失败投影：违规候选绝不显示为最终正文。 */
function failedResult(): HumanizerResultProjection {
  return {
    ...cleanResult(),
    status: "error",
    output: null,
    error_code: "fidelity_gate_conflict",
    error_message: "来源保真硬门未通过，已停止交付：新增结论没有来源绑定。",
    article: {
      ...cleanResult().article!,
      delivery_status: "failed",
      final_text: null,
      fidelity: {
        passed: false,
        blocking_count: 1,
        needs_confirmation_count: 0,
        items: [
          {
            code: "unattributed_claim",
            severity: "blocking",
            category: "无来源新增 claim",
            note: "新增结论没有来源绑定。",
          },
        ],
      },
    },
  };
}

describe("HumanizerResultCard（Issue 08 正文优先交付界面）", () => {
  it("成功无风险时首屏只显示状态与复制入口，默认折叠审计", () => {
    render(createElement(HumanizerResultCard, { result: cleanResult() }));
    expect(screen.getByRole("button", { name: /已交付/ })).toBeTruthy();
    // 首屏可见「复制正文」入口（折叠状态不影响复制）
    expect(screen.getByRole("button", { name: "复制最终正文" })).toBeTruthy();
    // 审计分区默认折叠：来源保真分区内容不可见
    expect(screen.queryByTestId("humanizer-fidelity")).toBeNull();
    // 无固定「已完成人味化」自夸前言与空事实核查段
    expect(screen.queryByText(/已完成人味化/)).toBeNull();
    expect(screen.queryByText(/事实核查结果/)).toBeNull();
  });

  it("展开后展示带计数的审计分区，键盘按钮 aria-expanded 正确", () => {
    render(createElement(HumanizerResultCard, { result: cleanResult() }));
    const toggle = screen.getByRole("button", { name: /已交付/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    // 保真通过 pill 在头部可见（计数与状态不依赖展开）
    expect(screen.getByRole("status").textContent).toContain("保真通过");
    // 成功无风险：不渲染空分区（无空事实核查段）
    expect(screen.queryByTestId("humanizer-fidelity")).toBeNull();
    expect(screen.queryByTestId("humanizer-style-review")).toBeNull();
    // 展开事件只上报一次
    expect(trackMock).toHaveBeenCalledTimes(1);
  });

  it("复制只复制最终正文，不混入审计文案", async () => {
    render(createElement(HumanizerResultCard, { result: cleanResult() }));
    fireEvent.click(screen.getByRole("button", { name: "复制最终正文" }));
    await vi.waitFor(() => {
      expect(clipMock).toHaveBeenCalledWith("光合作用把光能转化为化学能。");
    });
    await vi.waitFor(() => {
      expect(screen.getByText("已复制")).toBeTruthy();
    });
    expect(trackMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projection_version: "1",
        delivery_status: "delivered",
      }),
      "copy"
    );
  });

  it("硬门失败显示「未交付」且不展示违规稿", () => {
    render(createElement(HumanizerResultCard, { result: failedResult() }));
    expect(screen.getByRole("button", { name: /未交付/ })).toBeTruthy();
    // 展开后展示稳定失败原因
    fireEvent.click(screen.getByRole("button", { name: /未交付/ }));
    expect(screen.getByTestId("humanizer-error-detail").textContent).toContain(
      "来源保真硬门未通过"
    );
    expect(screen.getByTestId("humanizer-fidelity").textContent).toContain(
      "无来源新增 claim"
    );
    // 正文区域不出现违规候选正文
    expect(screen.queryByText(/光合作用/)).toBeNull();
  });

  it("风险存在时折叠状态也显示审计计数", () => {
    const result = failedResult();
    render(createElement(HumanizerResultCard, { result }));
    const status = screen.getByRole("status");
    expect(status.textContent).toContain("保真 1 项未通过");
  });

  it("多风险项与长文：正文首屏完整，审计全部展开不截断", () => {
    const longText = `第一段：${"光合作用把光能转化为化学能。".repeat(40)}`;
    const risky: HumanizerResultProjection = {
      ...cleanResult(),
      output: { ...cleanResult().output!, final_text: longText },
      article: {
        ...cleanResult().article!,
        final_text: longText,
        style_review: {
          finding_count: 2,
          warning_count: 1,
          suggestion_count: 1,
          items: [
            {
              severity: "warning",
              category: "重复开场",
              evidence: "首先，我们来了解一下……",
              suggestion: "删去「首先」直接进入主题。",
              location: { start: 0, end: 12 },
            },
            {
              severity: "suggestion",
              category: "密集排比设问",
              evidence: "可能或许大概",
              suggestion: "保留一处限定即可。",
              location: { start: 20, end: 40 },
            },
          ],
        },
        evidence: [
          { code: "correlation_as_causality", category: "把相关性说成因果", kind: "risk", original_span: "A 导致 B", revised_span: null, reason: "来源只支持相关。", source_label: null, needs_user_confirmation: false },
          { code: "significance_without_test", category: "无检验写显著", kind: "risk", original_span: "显著增加", revised_span: null, reason: "没有检验依据。", source_label: null, needs_user_confirmation: false },
          { code: "hold_for_user", category: "证据安全修订未应用", kind: "hold", original_span: null, revised_span: null, reason: "材料不足保持原文。", source_label: null, needs_user_confirmation: true },
        ],
      },
    };
    render(createElement(HumanizerResultCard, { result: risky }));
    // 折叠状态头部显示多项审计计数
    const status = screen.getByRole("status");
    expect(status.textContent).toContain("审稿 1 项");
    expect(status.textContent).toContain("证据风险 3 项");
    // 展开后全部风险项与审稿定向项可见（不截断）
    fireEvent.click(screen.getByRole("button", { name: /已交付/ }));
    const evidence = screen.getByTestId("humanizer-evidence");
    expect(evidence.textContent).toContain("把相关性说成因果");
    expect(evidence.textContent).toContain("无检验写显著");
    expect(evidence.textContent).toContain("材料不足保持原文");
    const styleReview = screen.getByTestId("humanizer-style-review");
    expect(styleReview.textContent).toContain("删去「首先」");
    expect(styleReview.textContent).toContain("保留一处限定即可");
  });

  it("硬门失败时不显示复制正文入口（无最终正文可复制）", () => {
    render(createElement(HumanizerResultCard, { result: failedResult() }));
    expect(screen.queryByRole("button", { name: "复制最终正文" })).toBeNull();
  });

  it("部分交付（修订未完成）标注「已交付首稿」并展示说明", () => {
    const partial: HumanizerResultProjection = {
      ...cleanResult(),
      article: {
        ...cleanResult().article!,
        delivery_status: "partial",
        delivery_note: "已交付首稿，未完成修订（剩余预算不足）。",
        revision: {
          triggered: true,
          trigger_label: null,
          problem_count: 2,
          resolved_count: 0,
          remaining_count: 2,
          skipped_reason: "预算不足",
        },
      },
    };
    render(createElement(HumanizerResultCard, { result: partial }));
    // 头部状态明确「已交付首稿（未完成修订）」，成功终态而非错误
    expect(
      screen.getByRole("button", { name: /已交付首稿（未完成修订）/ })
    ).toBeTruthy();
    // 首稿正文可复制（部分交付仍是成功交付）
    expect(screen.getByRole("button", { name: "复制最终正文" })).toBeTruthy();
    // 展开后展示部分交付说明与修订跳过原因
    fireEvent.click(screen.getByRole("button", { name: /已交付首稿/ }));
    expect(screen.getByTestId("humanizer-partial-note").textContent).toContain(
      "已交付首稿，未完成修订"
    );
    expect(screen.getByTestId("humanizer-revision").textContent).toContain(
      "预算不足"
    );
  });

  it("已剔除交付：标注移除条数、如实展示移除清单，复制正常可用", async () => {
    const excised: HumanizerResultProjection = {
      ...cleanResult(),
      article: {
        ...cleanResult().article!,
        final_text: "光合作用是植物把光能转化为化学能的过程。",
        delivery_note: "已移除 2 处无来源/未授权内容（剔除 1 句）。",
        excision: {
          removed_count: 2,
          removed_sentence_count: 1,
          sentence_ratio: 0.25,
          items: [
            {
              code: "unattributed_claim",
              category: "无来源新增 claim",
              note: "候选新增「90%」没有账本来源。",
            },
            {
              code: "assumption_not_allowed",
              category: "显式假设",
              note: "候选使用了假设内容，但任务契约不允许。",
            },
          ],
        },
        revision: {
          triggered: true,
          trigger_label: null,
          problem_count: 2,
          resolved_count: 1,
          remaining_count: 0,
          skipped_reason: null,
        },
      },
    };
    render(createElement(HumanizerResultCard, { result: excised }));
    // 头部状态明确「已交付（已剔除 2 处无来源内容）」，成功终态
    expect(
      screen.getByRole("button", { name: /已交付（已剔除 2 处无来源内容）/ })
    ).toBeTruthy();
    // 折叠状态头部 pill 如实显示移除条数
    expect(screen.getByRole("status").textContent).toContain("已剔除 2 处");
    // 剔除交付仍是成功交付：最终正文可复制（只复制正文，不混入说明）
    fireEvent.click(screen.getByRole("button", { name: "复制最终正文" }));
    await vi.waitFor(() => {
      expect(clipMock).toHaveBeenCalledWith(
        "光合作用是植物把光能转化为化学能的过程。"
      );
    });
    // 展开后展示移除说明分区与条目类别（无来源/假设）
    fireEvent.click(screen.getByRole("button", { name: /已交付（已剔除/ }));
    const excisionSection = screen.getByTestId("humanizer-excision");
    expect(excisionSection.textContent).toContain("已移除 2 处无来源/未授权内容");
    expect(excisionSection.textContent).toContain("无来源新增 claim");
    expect(excisionSection.textContent).toContain("显式假设");
    // 未交付态展示不变：无 error 详情
    expect(screen.queryByTestId("humanizer-error-detail")).toBeNull();
  });

  it("灰度开关开启时新投影也按旧版结果展示", () => {
    window.localStorage.setItem("bridges:article-projection:legacy", "1");
    render(createElement(HumanizerResultCard, { result: cleanResult() }));
    expect(screen.getByTestId("humanizer-legacy-badge")).toBeTruthy();
    window.localStorage.removeItem("bridges:article-projection:legacy");
  });

  it("legacy 旧结果（无 article）标注「旧版结果」并保持可读", () => {
    const legacy: HumanizerResultProjection = {
      task_id: "task-old",
      skill_id: "bridges-humanizer",
      skill_version: "1.0.0",
      path: "rewrite",
      genre: null,
      contract: { path: "rewrite", source_text: "原文", allow_assumptions: false, allow_first_person: false },
      status: "done",
      repair_attempts: 0,
      writing_call_count: 1,
      process_state: "done",
      output: {
        final_text: "旧版正文",
        edits: [],
        fact_check: [{ item: "来源保真检查", result: "已核实", evidence: "账本通过" }],
        open_questions: [],
        quality_status: "ok",
      },
      article: null,
    };
    render(createElement(HumanizerResultCard, { result: legacy }));
    expect(screen.getByTestId("humanizer-legacy-badge").textContent).toContain(
      "旧版结果"
    );
    // legacy 读取上报一次
    expect(trackMock).toHaveBeenCalledWith(
      expect.objectContaining({ legacy: true }),
      "legacy_read"
    );
    // 旧字段仍可展开读取
    fireEvent.click(screen.getByRole("button", { name: /人味化完成/ }));
    expect(screen.getByTestId("humanizer-fact-check").textContent).toContain(
      "来源保真检查"
    );
  });
});
