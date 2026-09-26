import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StudyProgress } from "./StudyProgress";

afterEach(cleanup);

describe("学习阶段与页级证据", () => {
  it("开始操作等待请求完成，失败后仍可重试", async () => {
    let finish!: (value: boolean) => void;
    const onAction = vi.fn(() => new Promise<boolean>((resolve) => { finish = resolve; }));
    render(<StudyProgress study={{ subsection_id: "s", stage: "tutoring" }} onAction={onAction} />);
    const button = screen.getByRole("button", { name: "开始复盘" });
    fireEvent.click(button);
    expect(onAction).toHaveBeenCalledWith("开始复盘");
    expect(button.hasAttribute("disabled")).toBe(true);
    finish(false);
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
  });

  it("复盘中可暂停，生成时禁用阶段操作", () => {
    const onAction = vi.fn(async () => true);
    const { rerender } = render(<StudyProgress study={{ subsection_id: "s", stage: "review" }}
      onAction={onAction} busy />);
    const button = screen.getByRole("button", { name: "暂停复盘回辅导" });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("复盘").getAttribute("aria-current")).toBe("step");
    rerender(<StudyProgress study={{ subsection_id: "s", stage: "tutoring",
      review: { complete: false, needs_replan: false } }}
      onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: "继续复盘" }));
    expect(onAction).toHaveBeenCalledWith("继续复盘");
  });

  it("复盘结束不重新开题，追加页待确认时不提供复盘操作", () => {
    const onAction = vi.fn(async () => true);
    const { rerender } = render(<StudyProgress study={{ subsection_id: "s", stage: "tutoring",
      review: { complete: true, needs_replan: false } }} onAction={onAction} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("复盘已结束");
    rerender(<StudyProgress study={{ subsection_id: "s", stage: "tutoring",
      page_update: { pages: [] } }} onAction={onAction} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("刷新后展示等待位置和用户补录来源，阶段不可点击", () => {
    render(
      <StudyProgress
        study={{
          subsection_id: "section-1",
          stage: "awaiting_pages",
          wait_reason: "unclear_page",
          pages: [
            {
              ordinal: 1,
              object_id: "photo-1",
              content_hash: "hash-1",
              model_id: "test-model",
              same_section: true,
              replaced_object_ids: [],
              fragments: [
                {
                  fragment_id: "user:1",
                  kind: "formula",
                  position: "中部公式",
                  text: "y=ax+b",
                  confidence: 1,
                  source: "user",
                },
              ],
              unclear: [{ position: "右上角", reason: "符号模糊" }],
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("书页识别").getAttribute("aria-current")).toBe("step");
    expect(screen.queryByRole("button", { name: "辅导" })).toBeNull();
    expect(screen.getByText(/书页有待补拍/)).toBeTruthy();
    screen.getByText(/已识别书页与证据/).click();
    expect(screen.getByText(/用户补录/)).toBeTruthy();
    expect(screen.getByText(/符号模糊/)).toBeTruthy();
  });

  it("页序等待展示书上页码，不误报为模糊补拍", () => {
    render(<StudyProgress study={{
      subsection_id: "section-1", stage: "awaiting_pages", wait_reason: "page_order",
      pages: [{ ordinal: 1, object_id: "photo-1", content_hash: "hash", model_id: "test",
        page_number: 12, same_section: true, fragments: [], unclear: [] }],
    }} />);
    expect(screen.getByRole("status").textContent).toContain("调整页序");
    expect(screen.getByText(/书上第12页/)).toBeTruthy();
    expect(screen.queryByText(/待补拍/)).toBeNull();
  });

  it("追加页未确认时保留辅导阶段，并明确不属于已确认范围", () => {
    render(<StudyProgress study={{
      subsection_id: "section-1", stage: "tutoring", pages: [],
      page_update: { wait_reason: "unclear_page", pages: [{
        ordinal: 2, object_id: "new-photo", content_hash: "new", model_id: "test",
        same_section: false, fragments: [], unclear: [],
      }] },
    }} />);
    expect(screen.getByText("辅导").getAttribute("aria-current")).toBe("step");
    expect(screen.getByText(/尚未更新本节范围/)).toBeTruthy();
    expect(screen.getByText(/同节归属待确认/)).toBeTruthy();
    expect(screen.queryByText(/已识别书页与证据/)).toBeNull();
  });

  it("总结阶段分述三段并逐条给出题目判定与书页依据", () => {
    render(<StudyProgress study={{
      subsection_id: "section-1", stage: "summary",
      pages: [{ ordinal: 1, object_id: "photo-1", content_hash: "hash", model_id: "test",
        page_number: 12, same_section: true, replaced_object_ids: [], unclear: [],
        fragments: [{ fragment_id: "photo-1:1", kind: "formula", position: "中部公式",
          text: "y=ax+b", confidence: 0.9, source: "photo" }] }],
      review: { complete: true, needs_replan: false, questions: [
        { question_id: "q1", question: "a 的含义是什么？", coverage_units: ["线性函数"],
          fragment_ids: ["photo-1:1"], asked: true, judgement: "correct" },
        { question_id: "q2", question: "b 如何影响图像？", coverage_units: ["线性函数"],
          fragment_ids: ["photo-1:1"], asked: true, judgement: "incorrect" },
      ] },
      summary: { points: [
        { kind: "learned", text: "本节讲线性函数 y=ax+b。", fragment_ids: ["photo-1:1"] },
        { kind: "mastered", text: "能解释斜率。", question_ids: ["q1"] },
        { kind: "gap", text: "截距的几何意义需要补。", question_ids: ["q2"] },
      ] },
    }} />);

    expect(screen.getByText("总结").getAttribute("aria-current")).toBe("step");
    expect(screen.getByText("学到了什么")).toBeTruthy();
    expect(screen.getByText("复盘已掌握")).toBeTruthy();
    expect(screen.getByText("还需补的点")).toBeTruthy();
    expect(screen.getByText(/第1题「a 的含义是什么？」判定为正确/)).toBeTruthy();
    expect(screen.getByText(/第2题「b 如何影响图像？」判定为错误/)).toBeTruthy();
    expect(screen.getByText(/上传第1页（书上第12页） · 中部公式/)).toBeTruthy();
    expect(screen.getByText(/学新小节请新建学习对话/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("全部答对时待补段只陈述实际判定，不宣称有漏洞", () => {
    render(<StudyProgress study={{
      subsection_id: "section-1", stage: "summary", pages: [],
      review: { complete: true, needs_replan: false, questions: [
        { question_id: "q1", question: "a 的含义是什么？", coverage_units: ["线性函数"],
          fragment_ids: [], asked: true, judgement: "correct" },
      ] },
      summary: { points: [
        { kind: "learned", text: "本节讲线性函数。", fragment_ids: [] },
        { kind: "mastered", text: "能解释斜率。", question_ids: ["q1"] },
      ] },
    }} />);

    expect(screen.getByText("本次复盘的题目全部答对，暂无待补的理解点。")).toBeTruthy();
    expect(screen.queryByText(/本次复盘没有判定为正确的题目/)).toBeNull();
  });
});
