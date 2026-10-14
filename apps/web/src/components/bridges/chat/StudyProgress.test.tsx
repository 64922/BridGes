import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StudyProgress } from "./StudyProgress";

afterEach(cleanup);

describe("学习阶段与页级证据", () => {
  it("新学习进展展示主题而不显示书页阶段条", () => {
    render(<StudyProgress studyLocalProgress={{
      topic: "互斥与独立",
      knowledge_context: "两个事件不能同时发生。",
      current_focus: "互斥是不是独立？",
      source_message_ids: ["user-1", "assistant-1"],
      version: 1,
      runtime_run_id: "run-1",
      updated_at: "2026-10-13T00:00:00Z",
    }} />);

    expect(screen.getByText("正在学习：互斥与独立")).toBeTruthy();
    expect(screen.getByText("当前关注：互斥是不是独立？")).toBeTruthy();
    expect(screen.queryByText("书页识别")).toBeNull();
  });

  it("展示当前待答问题与实际帮助状态", () => {
    render(<StudyProgress
      studyLocalProgress={{
        topic: "互斥与独立",
        knowledge_context: "两个事件不能同时发生。",
        current_focus: "互斥是不是独立？",
        source_message_ids: ["user-1", "assistant-1"],
        version: 2,
        runtime_run_id: "run-2",
        updated_at: "2026-10-13T00:00:00Z",
      }}
      studyPendingCheck={{
        check_id: "check-1",
        kind: "formal_check",
        question: "如果 A、B 互斥，P(A∩B) 是多少？为什么？",
        knowledge_context: "互斥与独立",
        assistance: "hinted",
        version: 1,
        source_message_id: "assistant-2",
        created_at: "2026-10-13T00:00:00Z",
      }}
    />);

    expect(screen.getByText(/待答：如果 A、B 互斥/)).toBeTruthy();
    expect(screen.getByText(/已提供提示，本次将按提示后完成记录/)).toBeTruthy();
    expect(screen.queryByText("书页识别")).toBeNull();
  });

  it("旧状态显示只读说明并保留可解释阶段", () => {
    render(<StudyProgress
      study={{ subsection_id: "s", stage: "tutoring", state_version: 4 }}
      historyStatus={{
        status: "legacy",
        version: 3,
        message: "此学习记录采用旧格式（版本 3）；已按原记录只读展示。",
      }}
    />);
    expect(screen.getByRole("status").textContent).toContain("旧格式");
    expect(screen.getByText("辅导")).toBeTruthy();
  });

  it("未知状态版本显示说明，不虚构学习阶段", () => {
    render(<StudyProgress
      historyStatus={{
        status: "unsupported",
        version: 5,
        message: "此学习记录采用当前不支持的格式（版本 5）；消息历史仍可查看，学习阶段暂无法解读。",
      }}
    />);
    expect(screen.getByRole("status").textContent).toContain("当前不支持");
    expect(screen.queryByText("书页识别")).toBeNull();
  });

  it("已删除材料的内容明确标记为历史识别记录", () => {
    render(<StudyProgress
      study={{
        subsection_id: "s", stage: "tutoring", state_version: 4,
        materials: [{ object_id: "photo-1", status: "deleted" }],
        pages: [{ ordinal: 1, object_id: "photo-1", content_hash: "hash", model_id: "test",
          same_section: true, fragments: [{ fragment_id: "f1", kind: "text",
            position: "页首", text: "历史材料内容", confidence: 1, source: "photo",
            recognition_path: "vision", interpretation: "" }], unclear: [] }],
        summary: { scope_version_id: "", points: [{ kind: "learned",
          text: "线性函数斜率", fragment_ids: ["f1"] }] },
      }}
    />);
    expect(screen.getByText(/原材料已删除，以下内容仅是保留的历史识别记录/)).toBeTruthy();
    expect(screen.getByText(/已删除材料的历史记录：上传第1页/)).toBeTruthy();
  });

  it("开始操作等待请求完成，失败后仍可重试", async () => {
    let finish!: (value: boolean) => void;
    const onAction = vi.fn(() => new Promise<boolean>((resolve) => { finish = resolve; }));
    render(<StudyProgress study={{ subsection_id: "s", stage: "tutoring", state_version: 4 }}
      onAction={onAction} />);
    const button = screen.getByRole("button", { name: "开始复盘" });
    fireEvent.click(button);
    expect(onAction).toHaveBeenCalledWith("开始复盘");
    expect(button.hasAttribute("disabled")).toBe(true);
    finish(false);
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
  });

  it("复盘中可暂停，生成时禁用阶段操作", () => {
    const onAction = vi.fn(async () => true);
    const { rerender } = render(<StudyProgress study={{ subsection_id: "s", stage: "review",
      state_version: 4 }} onAction={onAction} busy />);
    const button = screen.getByRole("button", { name: "暂停复盘回辅导" });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("复盘").getAttribute("aria-current")).toBe("step");
    rerender(<StudyProgress study={{ subsection_id: "s", stage: "tutoring", state_version: 4,
      review: { complete: false, needs_replan: false,
        scope_version_id: "", protocol_version: "study-review-v1" } }}
      onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: "继续复盘" }));
    expect(onAction).toHaveBeenCalledWith("继续复盘");
  });

  it("复盘结束不重新开题，追加页待确认时不提供复盘操作", () => {
    const onAction = vi.fn(async () => true);
    const { rerender } = render(<StudyProgress study={{ subsection_id: "s", stage: "tutoring",
      state_version: 4,
      review: { complete: true, needs_replan: false,
        scope_version_id: "", protocol_version: "study-review-v1" } }} onAction={onAction} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("复盘已结束");
    rerender(<StudyProgress study={{ subsection_id: "s", stage: "tutoring", state_version: 4,
      page_update: { pages: [] } }} onAction={onAction} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("刷新后展示等待位置和用户补录来源，阶段不可点击", () => {
    render(
      <StudyProgress
        study={{
          subsection_id: "section-1",
          stage: "awaiting_pages",
          state_version: 4,
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
                  recognition_path: "user",
                  interpretation: "",
                },
              ],
              unclear: [
                { position: "右上角", reason: "符号模糊", kind: "unclear", critical: false },
              ],
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
      subsection_id: "section-1", stage: "awaiting_pages", state_version: 4,
      wait_reason: "page_order",
      pages: [{ ordinal: 1, object_id: "photo-1", content_hash: "hash", model_id: "test",
        page_number: 12, same_section: true, fragments: [], unclear: [] }],
    }} />);
    expect(screen.getByRole("status").textContent).toContain("调整页序");
    expect(screen.getByText(/书上第12页/)).toBeTruthy();
    expect(screen.queryByText(/待补拍/)).toBeNull();
  });

  it("追加页未确认时保留辅导阶段，并明确不属于已确认范围", () => {
    render(<StudyProgress study={{
      subsection_id: "section-1", stage: "tutoring", state_version: 4, pages: [],
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

  it("总结阶段分述四段并逐条给出题目判定与书页依据", () => {
    render(<StudyProgress study={{
      subsection_id: "section-1", stage: "summary", state_version: 4,
      pages: [{ ordinal: 1, object_id: "photo-1", content_hash: "hash", model_id: "test",
        page_number: 12, same_section: true, replaced_object_ids: [], unclear: [],
        fragments: [{ fragment_id: "photo-1:1", kind: "formula", position: "中部公式",
          text: "y=ax+b", confidence: 0.9, source: "photo",
          recognition_path: "vision", interpretation: "" }] }],
      review: { complete: true, needs_replan: false,
        scope_version_id: "", protocol_version: "study-review-v1", questions: [
        { question_id: "q1", question: "a 的含义是什么？", coverage_units: ["线性函数"],
          fragment_ids: ["photo-1:1"], asked: true, unanswered: false, judgement: "correct",
          scope_version_id: "", incomplete_basis: "", incorrect_basis: "",
          conditions: "", legacy: false },
        { question_id: "q2", question: "b 如何影响图像？", coverage_units: ["线性函数"],
          fragment_ids: ["photo-1:1"], asked: true, unanswered: false, judgement: "incorrect",
          scope_version_id: "", incomplete_basis: "", incorrect_basis: "",
          conditions: "", legacy: false },
        { question_id: "q3", question: "c 的作用是什么？", coverage_units: ["线性函数"],
          fragment_ids: ["photo-1:1"], asked: true, unanswered: false, answer: "还没判定",
          scope_version_id: "", incomplete_basis: "", incorrect_basis: "",
          conditions: "", legacy: false },
      ] },
      summary: { scope_version_id: "", points: [
        { kind: "learned", text: "本节讲线性函数 y=ax+b。", fragment_ids: ["photo-1:1"] },
        { kind: "mastered", text: "能解释斜率。", question_ids: ["q1"] },
        { kind: "gap", text: "截距的几何意义需要补。", question_ids: ["q2"] },
        { kind: "unanswered", text: "第3题已作答尚未判定。", question_ids: ["q3"] },
      ] },
    }} />);

    expect(screen.getByText("总结").getAttribute("aria-current")).toBe("step");
    expect(screen.getByText("学到了什么")).toBeTruthy();
    expect(screen.getByText("复盘已掌握")).toBeTruthy();
    expect(screen.getByText("还需补的点")).toBeTruthy();
    expect(screen.getByText("未作答或尚未判定")).toBeTruthy();
    expect(screen.getByText(/第1题「a 的含义是什么？」判定为正确/)).toBeTruthy();
    expect(screen.getByText(/第2题「b 如何影响图像？」判定为错误/)).toBeTruthy();
    expect(screen.getByText(/第3题「c 的作用是什么？」已作答（尚未判定）/)).toBeTruthy();
    expect(screen.getByText(/上传第1页（书上第12页） · 中部公式/)).toBeTruthy();
    expect(screen.getByText(/学新小节请新建学习对话/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("全部答对时待补段只陈述实际判定，不宣称有漏洞", () => {
    render(<StudyProgress study={{
      subsection_id: "section-1", stage: "summary", state_version: 4, pages: [],
      review: { complete: true, needs_replan: false,
        scope_version_id: "", protocol_version: "study-review-v1", questions: [
        { question_id: "q1", question: "a 的含义是什么？", coverage_units: ["线性函数"],
          fragment_ids: [], asked: true, unanswered: false, judgement: "correct",
          scope_version_id: "", incomplete_basis: "", incorrect_basis: "",
          conditions: "", legacy: false },
      ] },
      summary: { scope_version_id: "", points: [
        { kind: "learned", text: "本节讲线性函数。", fragment_ids: [] },
        { kind: "mastered", text: "能解释斜率。", question_ids: ["q1"] },
      ] },
    }} />);

    expect(screen.getByText("本次没有判定不完整、错误或依据需重新确认的题目。")).toBeTruthy();
    expect(screen.getByText("本次复盘没有未作答或尚未判定的题目。")).toBeTruthy();
    expect(screen.queryByText(/本次复盘没有判定为正确的题目/)).toBeNull();
  });
});

describe("系统性目标与学习路线", () => {
  const goal = {
    goal_id: "goal-1",
    statement: "我想学线性代数，主要为了理解机器学习里的矩阵",
    observable_result: "能读懂机器学习里的矩阵运算",
    scope: "线性代数基础",
    constraints: "不要布置作业",
    status: "active" as const,
    version: 1,
    source_message_id: "user-1",
    created_at: "2026-10-13T00:00:00Z",
    updated_at: "2026-10-13T00:00:00Z",
    route: {
      version: 2,
      nodes: [
        { node_id: "n1", title: "向量与线性组合", prerequisites: [], depth: "基础" },
        { node_id: "n2", title: "矩阵与矩阵乘法", prerequisites: ["n1"], depth: "基础" },
        { node_id: "n3", title: "特征值与特征向量", prerequisites: ["n2"], depth: "加深" },
      ],
      current_node_id: "n2",
      revision_reason: "按你的要求先学矩阵",
      origin: "user_request" as const,
      created_at: "2026-10-13T00:00:00Z",
    },
  };

  it("顶部展示目标与当前知识点，路线按需展开且不宣称已掌握", () => {
    render(<StudyProgress studyGoal={goal} />);

    expect(screen.getByText(`目标：${goal.statement}`)).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("当前知识点：矩阵与矩阵乘法");
    screen.getByText(/学习路线（版本 2，3 个知识点）/).click();
    expect(screen.getByText(/^矩阵与矩阵乘法/).getAttribute("aria-current")).toBe("step");
    expect(screen.getByText(/^向量与线性组合/).getAttribute("aria-current")).toBeNull();
    expect(screen.getByText(/前置：向量与线性组合/)).toBeTruthy();
    expect(screen.getByText(/修订依据：按你的要求先学矩阵/)).toBeTruthy();
    expect(screen.getByText(/跳到某节点不代表前面的内容已完成/)).toBeTruthy();
    expect(screen.queryByText(/已掌握/)).toBeNull();
  });

  it("尚未形成路线时如实说明，不虚构知识点", () => {
    render(<StudyProgress studyGoal={{ ...goal, route: null }} />);

    expect(screen.getByText(`目标：${goal.statement}`)).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("学习路线尚未形成");
    expect(screen.queryByText(/学习路线（版本/)).toBeNull();
  });

  it("有目标时同时展示最近关注，刷新后状态一致", () => {
    const localProgress = {
      topic: "线性代数",
      knowledge_context: "矩阵乘法规则。",
      current_focus: "继续讲矩阵乘法",
      source_message_ids: ["user-1", "assistant-1"],
      version: 2,
      runtime_run_id: "run-1",
      updated_at: "2026-10-13T00:00:00Z",
    };
    const { unmount } = render(
      <StudyProgress studyGoal={goal} studyLocalProgress={localProgress} />,
    );
    expect(screen.getByText("当前关注：继续讲矩阵乘法")).toBeTruthy();
    expect(screen.queryByText("正在学习：线性代数")).toBeNull();
    unmount();
    render(<StudyProgress studyGoal={goal} studyLocalProgress={localProgress} />);
    expect(screen.getByText(`目标：${goal.statement}`)).toBeTruthy();
    expect(screen.getByText("当前关注：继续讲矩阵乘法")).toBeTruthy();
  });
});

describe("后台核验与学习档案", () => {
  const pendingTask = {
    task_id: "dx-1",
    status: "pending" as const,
    question: "A、B 互斥时 P(A∩B) 是多少？",
    knowledge_context: "互斥与独立",
    evidence_message_id: "assistant-2",
    failure_reason: "",
    attempts: 0,
    retryable: false,
    created_at: "2026-10-14T00:00:00Z",
    updated_at: "2026-10-14T00:00:00Z",
  };
  const record = {
    record_id: "lr-1",
    subject: "概率论",
    title: "互斥与独立",
    usage_context: "理解事件关系",
    verdict: "independent_completion" as const,
    performance: "能说明互斥不等于独立。",
    supporting: ["给出了正确的反例"],
    refuting: [],
    uncertainty: "只在单题上验证过。",
    next_validation: "换一个条件概率题再验证。",
    pending_confirmation: false,
    evidence_count: 1,
    evidences: [{
      evidence_id: "ev-1",
      question: "A、B 互斥时 P(A∩B) 是多少？",
      knowledge_context: "互斥与独立",
      assistance: "independent" as const,
      verdict: "independent_completion" as const,
      answer_message_id: "user-9",
      feedback_message_id: "assistant-9",
      occurred_at: "2026-10-14T00:00:00Z",
    }],
    created_at: "2026-10-14T00:00:00Z",
    updated_at: "2026-10-14T00:00:00Z",
  };

  it("展示待核验真实状态与档案判断，不渲染百分比或能力标签", () => {
    render(<StudyProgress
      studyLocalProgress={{
        topic: "互斥与独立",
        knowledge_context: "两个事件不能同时发生。",
        current_focus: "互斥是不是独立？",
        source_message_ids: ["user-1", "assistant-1"],
        version: 1,
        runtime_run_id: "run-1",
        updated_at: "2026-10-14T00:00:00Z",
      }}
      studyDiagnosisTasks={[pendingTask]}
      studyLearningRecord={[record]}
    />);

    expect(screen.getByLabelText("学习档案")).toBeTruthy();
    expect(screen.getByText("核验状态")).toBeTruthy();
    expect(screen.getByText(/「A、B 互斥时 P\(A∩B\) 是多少？」：待后台核验/)).toBeTruthy();
    expect(screen.getByText(/后台独立核验只针对当时的实际作答/)).toBeTruthy();
    expect(screen.getByText(/（概率论 · 理解事件关系）：独立完成/)).toBeTruthy();
    expect(screen.getByText(/表现：能说明互斥不等于独立/)).toBeTruthy();
    expect(screen.getByText(/依据与证据（1 条）/)).toBeTruthy();
    expect(screen.getByText("支持：给出了正确的反例")).toBeTruthy();
    expect(screen.getByText("不确定性：只在单题上验证过。")).toBeTruthy();
    expect(screen.getByText(/提示后作答|作答前无提示|无提示/)).toBeTruthy();
    expect(screen.queryByText(/掌握度|百分比|能力等级|人格/)).toBeNull();
  });

  it("失败任务可手动重试，来源失效如实标注", async () => {
    const onRetry = vi.fn(async () => {});
    render(<StudyProgress
      studyLocalProgress={{
        topic: "互斥与独立",
        knowledge_context: "两个事件不能同时发生。",
        current_focus: "互斥是不是独立？",
        source_message_ids: ["user-1", "assistant-1"],
        version: 2,
        runtime_run_id: "run-2",
        updated_at: "2026-10-14T00:00:00Z",
      }}
      studyDiagnosisTasks={[
        { ...pendingTask, task_id: "dx-failed", status: "failed" as const,
          failure_reason: "诊断能力未配置或不可用。", attempts: 3, retryable: true },
        { ...pendingTask, task_id: "dx-invalid", status: "invalidated" as const,
          failure_reason: "来源证据已失效，未形成诊断。", retryable: false },
      ]}
      onRetryDiagnosis={onRetry}
    />);

    expect(screen.getByText(/核验失败 · 诊断能力未配置或不可用/)).toBeTruthy();
    expect(screen.getByText(/来源已失效，未形成诊断/)).toBeTruthy();
    const button = screen.getByRole("button", { name: "重试核验" });
    fireEvent.click(button);
    await waitFor(() => expect(onRetry).toHaveBeenCalledWith("dx-failed"));
  });

  it("冲突证据标注待进一步验证，不伪造成单一结论", () => {
    render(<StudyProgress
      studyLocalProgress={{
        topic: "互斥与独立",
        knowledge_context: "两个事件不能同时发生。",
        current_focus: "互斥是不是独立？",
        source_message_ids: ["user-1", "assistant-1"],
        version: 3,
        runtime_run_id: "run-3",
        updated_at: "2026-10-14T00:00:00Z",
      }}
      studyLearningRecord={[{ ...record, verdict: "misconception" as const,
        pending_confirmation: true }]}
    />);

    expect(screen.getByText(/存在具体误解，证据方向冲突，待进一步验证/)).toBeTruthy();
  });

  it("没有任务与档案时不渲染学习档案面板", () => {
    render(<StudyProgress
      studyLocalProgress={{
        topic: "互斥与独立",
        knowledge_context: "两个事件不能同时发生。",
        current_focus: "互斥是不是独立？",
        source_message_ids: ["user-1", "assistant-1"],
        version: 4,
        runtime_run_id: "run-4",
        updated_at: "2026-10-14T00:00:00Z",
      }}
      studyDiagnosisTasks={[]}
      studyLearningRecord={[]}
    />);

    expect(screen.queryByLabelText("学习档案")).toBeNull();
  });
});
