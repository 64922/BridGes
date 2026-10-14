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
        source_status: "available",
        version: 1,
        source_message_id: "assistant-2",
        created_at: "2026-10-13T00:00:00Z",
      }}
    />);

    expect(screen.getByText(/待答：如果 A、B 互斥/)).toBeTruthy();
    expect(screen.getByText(/已提供提示，本次将按提示后完成记录/)).toBeTruthy();
    expect(screen.queryByText(/将按未验证处理/)).toBeNull();
    expect(screen.queryByText("书页识别")).toBeNull();
  });

  it("资料失效的待答问题提示将按未验证处理，不按旧依据判定", () => {
    render(<StudyProgress
      studyLocalProgress={{
        topic: "线性函数",
        knowledge_context: "照片里的推导。",
        current_focus: "斜率为什么是 a？",
        source_message_ids: ["user-1", "assistant-1"],
        version: 3,
        runtime_run_id: "run-3",
        updated_at: "2026-10-13T00:00:00Z",
      }}
      studyPendingCheck={{
        check_id: "check-2",
        kind: "open_probe",
        question: "照片里把 a 判成斜率的依据是什么？",
        knowledge_context: "线性函数 y=ax+b",
        assistance: "none",
        source_status: "unavailable",
        version: 2,
        source_message_id: "assistant-3",
        created_at: "2026-10-13T00:00:00Z",
      }}
    />);

    expect(screen.getByText(/将按未验证处理，不按旧依据判定/)).toBeTruthy();
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

  it("旧记录只读展示当前阶段，不提供启动五阶段的操作", () => {
    const { rerender } = render(<StudyProgress study={{ subsection_id: "s", stage: "review",
      state_version: 4 }} busy />);
    expect(screen.getByText("复盘").getAttribute("aria-current")).toBe("step");
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/按原始记录只读展示的历史/)).toBeTruthy();
    rerender(<StudyProgress study={{ subsection_id: "s", stage: "tutoring", state_version: 4,
      review: { complete: false, needs_replan: false,
        scope_version_id: "", protocol_version: "study-review-v1" } }} />);
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
    expect(screen.getByText(/本节为只读历史记录，继续学习请使用下方「主动续学」/)).toBeTruthy();
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

describe("Issue 04 学习控制、暂停与阶段小结", () => {
  const localProgress = {
    topic: "互斥与独立",
    knowledge_context: "两个事件不能同时发生。",
    current_focus: "今天先到这里吧。",
    source_message_ids: ["user-1", "assistant-1"],
    version: 3,
    runtime_run_id: "run-1",
    updated_at: "2026-10-14T00:00:00Z",
  };
  const question = "如果 A、B 互斥，P(A∩B) 是多少？为什么？";

  it("暂停状态如实展示可继续位置与保留问题，小结按需展开", () => {
    render(
      <StudyProgress
        studyLocalProgress={localProgress}
        studyPause={{
          pause_id: "pause-1",
          topic: "互斥与独立",
          position: "今天先到这里吧。",
          pending_question: question,
          progress_version: 3,
          source_message_id: "user-2",
          paused_at: "2026-10-14T00:00:00Z",
        }}
        studySummary={{
          summary_id: "summary-1",
          trigger: "pause",
          status: "complete",
          content: "【本阶段小结】\n本次验证（即时反馈）：无。",
          source_message_id: "user-2",
          assistant_message_id: "assistant-2",
          created_at: "2026-10-14T00:00:00Z",
        }}
      />,
    );

    const status = screen.getByLabelText("暂停状态");
    expect(status.textContent).toContain("已暂停在「今天先到这里吧。」");
    expect(status.textContent).toContain("进展已保存");
    expect(status.textContent).toContain(`保留问题「${question}」保持未验证`);
    expect(screen.getByText(/暂停时生成/)).toBeTruthy();
    expect(screen.getByText(/已随回复完整交付/)).toBeTruthy();
    expect(screen.getByText(/【本阶段小结】/)).toBeTruthy();
  });

  it("跳过与直接讲解标注保持未验证，解除等待时提示可以继续", () => {
    const { rerender } = render(
      <StudyProgress
        studyLocalProgress={localProgress}
        studyLastControl={{
          control_id: "control-1",
          kind: "full_explanation",
          question,
          released: true,
          created_at: "2026-10-14T00:00:00Z",
        }}
      />,
    );

    let status = screen.getByLabelText("跳过状态");
    expect(status.textContent).toContain("已直接讲解");
    expect(status.textContent).toContain(`「${question}」保持未验证`);
    expect(status.textContent).toContain("可以继续下一步");

    rerender(
      <StudyProgress
        studyLocalProgress={localProgress}
        studyLastControl={{
          control_id: "control-2",
          kind: "skip",
          question,
          released: false,
          created_at: "2026-10-14T00:00:00Z",
        }}
      />,
    );
    status = screen.getByLabelText("跳过状态");
    expect(status.textContent).toContain("已跳过");
    expect(status.textContent).not.toContain("可以继续下一步");
  });

  it("小结未完整交付时如实标注 partial 并提示重试", () => {
    render(
      <StudyProgress
        studyLocalProgress={localProgress}
        studySummary={{
          summary_id: "summary-2",
          trigger: "explicit_request",
          status: "partial",
          content: "【本阶段小结】\n本次没有已完成验证的作答记录。",
          source_message_id: "user-3",
          assistant_message_id: "assistant-3",
          created_at: "2026-10-14T00:00:00Z",
        }}
      />,
    );

    expect(screen.getByText(/按你的要求生成/)).toBeTruthy();
    expect(screen.getByText(/回复未完整包含原文，可重试该消息补全/)).toBeTruthy();
    expect(screen.queryByLabelText("暂停状态")).toBeNull();
    expect(screen.queryByLabelText("跳过状态")).toBeNull();
  });

  it("没有控制状态时不虚构暂停或小结", () => {
    render(<StudyProgress studyLocalProgress={localProgress} />);

    expect(screen.queryByLabelText("暂停状态")).toBeNull();
    expect(screen.queryByLabelText("跳过状态")).toBeNull();
    expect(screen.queryByText(/阶段小结/)).toBeNull();
  });
});

describe("Issue 08 目标通知与暂停状态", () => {
  const goal = {
    goal_id: "goal-1",
    statement: "我想系统学概率论，为了做数据分析",
    observable_result: "",
    scope: "",
    constraints: "",
    status: "active" as const,
    version: 1,
    source_message_id: "user-1",
    created_at: "2026-10-14T00:00:00Z",
    updated_at: "2026-10-14T00:00:00Z",
    route: null,
  };
  const localProgress = {
    topic: "概率论",
    knowledge_context: "随机变量。",
    current_focus: "继续讲随机变量",
    source_message_ids: ["user-1", "assistant-1"],
    version: 1,
    runtime_run_id: "run-1",
    updated_at: "2026-10-14T00:00:00Z",
  };
  const restoredNotice = {
    kind: "restored" as const,
    message: "已恢复学习目标「我想系统学概率论，为了做数据分析」，按已保存的进展与位置继续。",
    goal_statement: "我想系统学概率论，为了做数据分析",
    created_at: "2026-10-14T00:00:00Z",
  };

  it("恢复通知由权威事件派生，展示在目标顶部", () => {
    render(<StudyProgress studyGoal={goal} studyGoalNotice={restoredNotice} />);

    expect(screen.getByLabelText("目标通知").textContent).toContain("已恢复学习目标");
    expect(screen.getByText(`目标：${goal.statement}`)).toBeTruthy();
  });

  it("目标被其他会话暂停时如实展示恢复方式，不虚构已恢复", () => {
    render(
      <StudyProgress
        studyGoal={{ ...goal, status: "paused" as const }}
      />,
    );

    const status = screen.getByLabelText("目标暂停状态");
    expect(status.textContent).toContain("该目标已在其他会话被暂停");
    expect(status.textContent).toContain("明确说「继续学」即可在此恢复");
    expect(screen.queryByLabelText("目标通知")).toBeNull();
  });

  it("冲突通知在局部进展视图同样可见，与消息交付分开呈现", () => {
    render(
      <StudyProgress
        studyLocalProgress={localProgress}
        studyGoalNotice={{
          kind: "conflict" as never,
          message: "目标状态已被其他会话更新，本次恢复或切换未提交；请重试或重新选择目标。",
          goal_statement: goal.statement,
          created_at: "2026-10-14T00:00:00Z",
        }}
      />,
    );

    expect(screen.getByLabelText("目标通知").textContent).toContain("本次恢复或切换未提交");
  });

  it("没有通知时不渲染任何目标状态行", () => {
    render(<StudyProgress studyLocalProgress={localProgress} />);

    expect(screen.queryByLabelText("目标通知")).toBeNull();
    expect(screen.queryByLabelText("目标暂停状态")).toBeNull();
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
      source_available: true,
      source_note: "",
    }],
    created_at: "2026-10-14T00:00:00Z",
    updated_at: "2026-10-14T00:00:00Z",
    record_version: 1,
    state: "active" as const,
    user_note: "",
    corrected_at: null,
    superseded_verdict: null,
    superseded_performance: "",
    source_available: true,
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

  const localProgress = {
    topic: "互斥与独立",
    knowledge_context: "两个事件不能同时发生。",
    current_focus: "互斥是不是独立？",
    source_message_ids: ["user-1", "assistant-1"],
    version: 9,
    runtime_run_id: "run-9",
    updated_at: "2026-10-14T00:00:00Z",
  };

  it("纠正按记录版本提交用户自评，成功后关闭编辑", async () => {
    const onCorrect = vi.fn(async () => true);
    render(<StudyProgress
      studyLocalProgress={localProgress}
      studyLearningRecord={[record]}
      onCorrectRecord={onCorrect}
    />);

    fireEvent.click(screen.getByRole("button", { name: "纠正" }));
    fireEvent.change(screen.getByLabelText("纠正内容"), {
      target: { value: "我当时其实靠猜的。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(onCorrect).toHaveBeenCalledWith(
      "lr-1", "我当时其实靠猜的。", 1,
    ));
    await waitFor(() => expect(screen.queryByLabelText("纠正内容")).toBeNull());
  });

  it("纠正失败保留输入草稿不清空", async () => {
    const onCorrect = vi.fn(async () => false);
    render(<StudyProgress
      studyLocalProgress={localProgress}
      studyLearningRecord={[record]}
      onCorrectRecord={onCorrect}
    />);

    fireEvent.click(screen.getByRole("button", { name: "纠正" }));
    const textarea = screen.getByLabelText("纠正内容") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "保留这段草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(onCorrect).toHaveBeenCalled());
    expect((screen.getByLabelText("纠正内容") as HTMLTextAreaElement).value)
      .toBe("保留这段草稿");
  });

  it("纠正态展示自评、被取代的原结论与并存提示", () => {
    render(<StudyProgress
      studyLocalProgress={localProgress}
      studyLearningRecord={[{
        ...record,
        state: "corrected" as const,
        user_note: "我当时其实靠猜的。",
        corrected_at: "2026-10-14T01:00:00Z",
        superseded_verdict: "independent_completion" as const,
        superseded_performance: "能说明互斥不等于独立。",
        pending_confirmation: true,
        record_version: 2,
      }]}
    />);

    expect(screen.getByText(/用户纠正（自评，未经核验）/)).toBeTruthy();
    expect(screen.getByText(/我的纠正（自评，未经核验）：我当时其实靠猜的。/)).toBeTruthy();
    expect(screen.getByText(/原核验结论「独立完成」已被你的纠正取代，不再用于后续教学。?/))
      .toBeTruthy();
    expect(screen.getByText(/原表现：能说明互斥不等于独立/)).toBeTruthy();
    expect(screen.getByText(/你的纠正与后续自动核验并存，待进一步验证/)).toBeTruthy();
  });

  it("删除先确认再按记录版本提交", async () => {
    const onDelete = vi.fn(async () => {});
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    try {
      render(<StudyProgress
        studyLocalProgress={localProgress}
        studyLearningRecord={[record]}
        onDeleteRecord={onDelete}
      />);

      fireEvent.click(screen.getByRole("button", { name: "删除" }));

      await waitFor(() => expect(onDelete).toHaveBeenCalledWith("lr-1", 1));
      expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("不再用于后续教学"));
    } finally {
      confirmSpy.mockRestore();
    }
  });

  it("取消删除确认时不提交", () => {
    const onDelete = vi.fn(async () => {});
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    try {
      render(<StudyProgress
        studyLocalProgress={localProgress}
        studyLearningRecord={[record]}
        onDeleteRecord={onDelete}
      />);

      fireEvent.click(screen.getByRole("button", { name: "删除" }));

      expect(onDelete).not.toHaveBeenCalled();
      expect(screen.getByText("互斥与独立")).toBeTruthy();
    } finally {
      confirmSpy.mockRestore();
    }
  });

  it("来源不可读的证据与记录如实标注，不伪造引文", () => {
    render(<StudyProgress
      studyLocalProgress={localProgress}
      studyLearningRecord={[{
        ...record,
        source_available: false,
        evidences: [
          {
            ...record.evidences[0],
            source_available: false,
            source_note: "回答消息已删除，无法回读原文。",
            occurred_at: null,
          },
          {
            ...record.evidences[0],
            evidence_id: "ev-2",
            question: "",
            source_available: false,
            source_note: "",
            occurred_at: null,
          },
        ],
        evidence_count: 2,
      }]}
    />);

    expect(screen.getByText(/部分依据来源已不可读，仅保留可核对的历史记录/)).toBeTruthy();
    expect(screen.getByText(/回答消息已删除，无法回读原文。/)).toBeTruthy();
    expect(screen.getByText(/一条依据（来源已不可读）/)).toBeTruthy();
  });
});

// Issue 09：学习详情按需查看真实生效的采用依据（采用了哪些学情、按什么
// 方向调整）；不展示模型提示词、内部推理或未生效的采用。
describe("按学情调整教法的可见依据", () => {
  const adoptionProgress = {
    topic: "互斥与独立",
    knowledge_context: "两个事件不能同时发生。",
    current_focus: "互斥是不是独立？",
    source_message_ids: ["user-1", "assistant-1"],
    version: 3,
    runtime_run_id: "run-3",
    updated_at: "2026-10-14T00:00:00Z",
  };

  it("展示真实生效的采用学情与调整方向", () => {
    render(<StudyProgress
      studyLocalProgress={adoptionProgress}
      studyRecordAdoptions={[{
        adoption_id: "sa-1",
        goal_id: "",
        goal_version: 0,
        route_version: 0,
        status: "applied",
        invalid_reason: "",
        uncertainty_note: "",
        created_at: "2026-10-14T00:00:00Z",
        entries: [{
          record_id: "lr-1",
          record_version: 2,
          subject: "概率论",
          title: "互斥与独立",
          usage_context: "理解事件关系",
          verdict: "misconception",
          performance: "把互斥理解为互不影响。",
          adjustments: ["prerequisite_review", "hint_granularity"],
          reason: "已核验存在具体误解：先检查或补讲这个知识点。",
        }],
      }]}
    />);

    expect(screen.getByText(/教学采用的学情依据（1 次）/)).toBeTruthy();
    expect(screen.getAllByText(/存在具体误解/).length).toBeGreaterThan(0);
    expect(screen.getByText(/补讲前置知识点、改用更小粒度提示/)).toBeTruthy();
    expect(screen.getByText(/一次完成不代表长期掌握/)).toBeTruthy();
  });

  it("没有生效采用时不显示依据区域", () => {
    render(<StudyProgress
      studyLocalProgress={adoptionProgress}
      studyRecordAdoptions={[]}
    />);

    expect(screen.queryByText(/教学采用的学情依据/)).toBeNull();
  });
});

// 工单 12：旧学习记录只读退出与主动续学——历史不改写，新对话由用户主动
// 选择目标/材料创建；失败逐项说明失效选择，其余选择保留。
describe("旧记录只读与主动续学", () => {
  const legacyStudy = { subsection_id: "s", stage: "tutoring" as const, state_version: 4 };
  const options = {
    conversation_id: "old-1",
    can_restart: true,
    linked_goal_id: "goal-1",
    goals: [
      { goal_id: "goal-1", statement: "系统学概率论", status: "active" as const,
        current_node_title: "条件概率", route_version: 2,
        updated_at: "2026-10-14T00:00:00Z" },
      { goal_id: "goal-2", statement: "复习线性代数", status: "paused" as const,
        current_node_title: "", route_version: 0,
        updated_at: "2026-10-13T00:00:00Z" },
    ],
    materials: [
      { object_id: "obj-1", original_filename: "书页-1.png", media_type: "image/png",
        content_length: 2048, content_hash: "h1", conversation_id: "old-1",
        status: "bound", ingestion_status: "none", adopted_from_conversation_id: "",
        adopted_from_object_id: "", created_at: "2026-10-12T00:00:00Z",
        updated_at: "2026-10-12T00:00:00Z" },
    ],
  };

  it("旧流程历史只读呈现，主动续学按用户点击读取并提交选择", async () => {
    const onLoadOptions = vi.fn();
    const onSubmit = vi.fn(async () => true);
    const { rerender } = render(<StudyProgress
      study={legacyStudy}
      restart={{ options: null, optionsLoading: false, optionsError: null,
        submitting: false, submitError: null, removedNotes: [],
        onLoadOptions, onSubmit }}
    />);
    expect(screen.getByText(/按原始记录只读展示的历史/)).toBeTruthy();
    expect(onLoadOptions).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "主动续学" }));
    expect(onLoadOptions).toHaveBeenCalledTimes(1);
    rerender(<StudyProgress
      study={legacyStudy}
      restart={{ options, optionsLoading: false, optionsError: null,
        submitting: false, submitError: null, removedNotes: [],
        onLoadOptions, onSubmit }}
    />);
    expect(screen.getByRole("radio", { name: /系统学概率论/, checked: true })).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /书页-1\.png/ }));
    fireEvent.change(screen.getByLabelText("补充说明"),
      { target: { value: "从条件概率继续" } });
    fireEvent.click(screen.getByRole("button", { name: "创建新学习对话" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({
      goalId: "goal-1", objectIds: ["obj-1"], message: "从条件概率继续",
    }));
  });

  it("没有所选材料时必须补充说明，且没有可沿用项时不显示创建动作", () => {
    const onLoadOptions = vi.fn();
    render(<StudyProgress
      study={legacyStudy}
      restart={{ options: { ...options, materials: [] }, optionsLoading: false,
        optionsError: null, submitting: false, submitError: null, removedNotes: [],
        onLoadOptions, onSubmit: vi.fn(async () => true) }}
    />);
    fireEvent.click(screen.getByRole("button", { name: "主动续学" }));
    const button = screen.getByRole("button", { name: "创建新学习对话" });
    fireEvent.change(screen.getByLabelText("补充说明"), { target: { value: "  " } });
    expect(button.hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("补充说明"),
      { target: { value: "继续学概率论" } });
    expect(button.hasAttribute("disabled")).toBe(false);
  });

  it("没有可续学目标与材料时如实说明，不伪造创建入口", () => {
    render(<StudyProgress
      historyStatus={{ status: "legacy", version: 3,
        message: "此学习记录采用旧格式（版本 3）；已按原记录只读展示。" }}
      restart={{ options: { conversation_id: "old-1", can_restart: false,
        linked_goal_id: null, goals: [], materials: [] }, optionsLoading: false,
        optionsError: null, submitting: false, submitError: null, removedNotes: [],
        onLoadOptions: vi.fn(), onSubmit: vi.fn(async () => true) }}
    />);
    expect(screen.getByRole("status").textContent).toContain("旧格式");
    fireEvent.click(screen.getByRole("button", { name: "主动续学" }));
    expect(screen.getByText(/当前没有可沿用的学习目标/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "创建新学习对话" })).toBeNull();
  });

  it("提交失败如实提示并逐项列出已移除的失效选择", () => {
    render(<StudyProgress
      study={legacyStudy}
      restart={{ options: { ...options, materials: [] }, optionsLoading: false,
        optionsError: null, submitting: false,
        submitError: "材料「书页-1.png」已不可访问，已从选择中移除。",
        removedNotes: ["材料「书页-1.png」已不可访问，已从选择中移除。"],
        onLoadOptions: vi.fn(), onSubmit: vi.fn(async () => false) }}
    />);
    fireEvent.click(screen.getByRole("button", { name: "主动续学" }));
    expect(screen.getByRole("alert").textContent)
      .toContain("材料「书页-1.png」已不可访问");
    expect(screen.getByLabelText("已移除的失效选择").textContent)
      .toContain("已从选择中移除");
  });
});
