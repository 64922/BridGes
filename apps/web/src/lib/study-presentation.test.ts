import { describe, expect, it } from "vitest";

import type { ChatConversationProjection } from "@/lib/api";

import { buildStudyTurns } from "./study-presentation";

const NOW = "2026-10-18T00:00:00Z";

function message(
  messageId: string,
  role: "user" | "assistant",
  content: string,
) {
  return {
    message_id: messageId,
    conversation_id: "c1",
    role,
    attempt_number: 1,
    status: "done",
    content,
    created_at: NOW,
    updated_at: NOW,
  };
}

function conversation(
  overrides: Partial<ChatConversationProjection> = {},
): ChatConversationProjection {
  return {
    conversation_id: "c1",
    title: "学习",
    mode: "study",
    mode_locked: true,
    pinned: false,
    created_at: NOW,
    updated_at: NOW,
    mode_events: [],
    messages: [
      message("u1", "user", "开始学"),
      message("a1", "assistant", "第一个问题"),
      message("u2", "user", "我的回答"),
      message("a2", "assistant", "反馈"),
    ],
    ...overrides,
  } as ChatConversationProjection;
}

describe("buildStudyTurns", () => {
  it("按服务端消息标识归入真实助手回合", () => {
    const turns = buildStudyTurns(conversation({
      study_goal: {
        goal_id: "goal-1",
        statement: "系统学概率论",
        observable_result: "",
        scope: "",
        constraints: "",
        status: "active",
        version: 1,
        source_message_id: "u1",
        created_at: NOW,
        updated_at: NOW,
        route: null,
      },
      study_pending_check: {
        check_id: "check-1",
        kind: "formal_check",
        question: "问题一",
        knowledge_context: "上下文",
        assistance: "none",
        source_status: "available",
        version: 1,
        source_message_id: "a1",
        created_at: NOW,
      },
      study_last_control: {
        control_id: "control-1",
        kind: "skip",
        question: "问题一",
        released: true,
        assistant_message_id: "a1",
        created_at: NOW,
      },
      study_summary: {
        summary_id: "summary-1",
        trigger: "explicit_request",
        status: "complete",
        content: "小结",
        source_message_id: "u2",
        assistant_message_id: "a2",
        created_at: NOW,
      },
      study_record_adoptions: [{
        adoption_id: "ad-1",
        goal_id: "g1",
        goal_version: 1,
        route_version: 1,
        status: "applied",
        invalid_reason: "",
        uncertainty_note: "",
        assistant_message_id: "a2",
        created_at: NOW,
        entries: [{
          record_id: "record-1",
          record_version: 1,
          subject: "数学",
          title: "互斥",
          usage_context: "",
          verdict: "independent_completion",
          performance: "",
          adjustments: ["recheck"],
          reason: "",
        }],
      }],
    }));

    expect(turns.get("a1")?.goal?.statement).toBe("系统学概率论");
    expect(turns.get("a1")?.pending?.question).toBe("问题一");
    expect(turns.get("a1")?.controls).toEqual([
      { kind: "skip", question: "问题一", released: true },
    ]);
    expect(turns.get("a2")?.summary?.trigger).toBe("explicit_request");
    expect(turns.get("a2")?.adoptionCount).toBe(1);
    expect(turns.has("u1")).toBe(false);
  });

  it("诊断失败归到作答后的反馈助手消息，成功任务不重复标注", () => {
    const turns = buildStudyTurns(conversation({
      study_diagnosis_tasks: [
        {
          task_id: "dx-failed",
          status: "failed",
          question: "问题一",
          knowledge_context: "上下文",
          evidence_message_id: "u2",
          failure_reason: "诊断服务不可用。",
          attempts: 2,
          retryable: true,
          created_at: NOW,
          updated_at: NOW,
        },
        {
          task_id: "dx-done",
          status: "succeeded",
          question: "问题一",
          knowledge_context: "上下文",
          evidence_message_id: "u2",
          failure_reason: "",
          attempts: 1,
          retryable: false,
          created_at: NOW,
          updated_at: NOW,
        },
      ],
    }));

    expect(turns.get("a2")?.diagnosis).toHaveLength(1);
    expect(turns.get("a2")?.diagnosis[0]).toMatchObject({
      taskId: "dx-failed",
      status: "failed",
      retryable: true,
    });
  });

  it("小结与记录只覆盖各自回合，不把当前学情套到旧消息", () => {
    const turns = buildStudyTurns(conversation({
      study_summary: {
        summary_id: "summary-1",
        trigger: "pause",
        status: "partial",
        content: "小结",
        source_message_id: "u2",
        assistant_message_id: "a2",
        created_at: NOW,
      },
      study_learning_record: [{
        record_id: "record-1",
        subject: "数学",
        title: "互斥",
        usage_context: "",
        verdict: "misconception",
        performance: "混淆概念。",
        supporting: [],
        refuting: [],
        uncertainty: "",
        next_validation: "",
        pending_confirmation: false,
        evidence_count: 1,
        evidences: [{
          evidence_id: "ev-1",
          question: "问题一",
          knowledge_context: "上下文",
          assistance: "independent",
          verdict: "misconception",
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
      }],
    }));

    expect(turns.get("a1")).toBeUndefined();
    expect(turns.get("a2")?.summary?.status).toBe("partial");
    expect(turns.get("a2")?.records[0]?.verdict).toBe("misconception");
  });

  it("历史回合使用当时证据的判定，不套用后来更新的记录结论", () => {
    const turns = buildStudyTurns(conversation({
      study_learning_record: [{
        record_id: "record-1",
        subject: "数学",
        title: "互斥",
        usage_context: "",
        verdict: "independent_completion",
        performance: "",
        supporting: [],
        refuting: [],
        uncertainty: "",
        next_validation: "",
        pending_confirmation: false,
        evidence_count: 1,
        evidences: [{
          evidence_id: "ev-1",
          question: "问题一",
          knowledge_context: "上下文",
          assistance: "independent",
          verdict: "misconception",
          answer_message_id: "u2",
          feedback_message_id: "a2",
          occurred_at: NOW,
          source_available: false,
          source_note: "",
        }],
        created_at: NOW,
        updated_at: NOW,
        record_version: 2,
        state: "active",
        user_note: "",
        corrected_at: null,
        superseded_verdict: null,
        superseded_performance: "",
        source_available: true,
      }],
    }));

    expect(turns.get("a2")?.records[0]?.verdict).toBe("misconception");
    expect(turns.get("a2")?.records[0]?.sourceAvailable).toBe(false);
  });

  it("只有不确定性说明、没有正式采用条目时不计入采用数", () => {
    const turns = buildStudyTurns(conversation({
      study_record_adoptions: [{
        adoption_id: "ad-1",
        goal_id: "g1",
        goal_version: 1,
        route_version: 1,
        status: "applied",
        invalid_reason: "",
        uncertainty_note: "待进一步验证。",
        assistant_message_id: "a2",
        created_at: NOW,
        entries: [],
      }],
    }));

    expect(turns.get("a2")?.adoptionCount ?? 0).toBe(0);
  });

  it("未知消息标识不凭时间猜测归属", () => {
    const turns = buildStudyTurns(conversation({
      study_last_control: {
        control_id: "control-1",
        kind: "full_explanation",
        question: "问题一",
        released: false,
        assistant_message_id: "missing",
        created_at: NOW,
      },
    }));

    expect(turns.size).toBe(0);
  });
});
