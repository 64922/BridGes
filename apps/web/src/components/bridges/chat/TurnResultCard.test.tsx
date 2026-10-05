import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TurnResultCard } from "./TurnResultCard";
import type { TurnResultProjection } from "@/lib/api";

afterEach(cleanup);

function projection(
  overrides: Partial<TurnResultProjection> = {}
): TurnResultProjection {
  return {
    version: "turn-result-v1",
    outcome: "complete",
    outcome_label: "本轮已完成。",
    trust: "qualified",
    trust_label: "结果已通过核验。",
    capability_list: ["paper"],
    actual_module_id: "paper",
    requested_module_id: null,
    route_source: "body_intent",
    delivered: [
      {
        module_id: "paper",
        label: "论文",
        state: "success",
        trust: "qualified",
        detail: "",
      },
    ],
    blocked: [],
    gaps: [],
    recovery: null,
    wait_reason: null,
    task_id: null,
    task_version: null,
    ...overrides,
  };
}

function textOf(testId: string): string {
  return screen.getByTestId(testId).textContent ?? "";
}

describe("TurnResultCard（工单 38）", () => {
  it("展示交付分类、可信状态与实际能力", () => {
    render(<TurnResultCard result={projection()} />);
    expect(screen.getByTestId("turn-result").getAttribute("data-outcome")).toBe(
      "complete"
    );
    expect(textOf("turn-result-outcome")).toContain("本轮已完成。");
    expect(textOf("turn-result-trust")).toContain("结果已通过核验。");
    expect(textOf("turn-result-capability")).toContain("实际执行：论文搜索");
    expect(textOf("turn-result-delivered")).toContain("论文（已完成）");
    expect(screen.queryByTestId("turn-result-recovery")).toBeNull();
  });

  it("请求模块提示与实际执行分开呈现", () => {
    render(
      <TurnResultCard
        result={projection({
          requested_module_id: "github",
          actual_module_id: "resources",
          capability_list: ["resources"],
        })}
      />
    );
    const capability = textOf("turn-result-capability");
    expect(capability).toContain("请求模块：GitHub 项目推荐");
    expect(capability).toContain("实际执行：学习资料推荐");
  });

  it("部分交付如实列出已交付与阻塞项及缺口", () => {
    render(
      <TurnResultCard
        result={projection({
          outcome: "partial",
          outcome_label: "本轮先交付有效部分，其余未完成。",
          trust: "evidence_bound",
          trust_label: "结果已附证据，仍有未完成核验的范围。",
          delivered: [
            { module_id: "paper", label: "论文", state: "success", trust: "qualified", detail: "" },
            { module_id: "resources", label: "学习资料", state: "links_only", trust: "evidence_bound", detail: "" },
          ],
          blocked: [
            { module_id: "github", label: "开源项目", state: "error", trust: "evidence_bound", detail: "接口限流" },
          ],
          gaps: ["接口限流"],
        })}
      />
    );
    expect(textOf("turn-result-delivered")).toContain("论文（已完成）");
    expect(textOf("turn-result-delivered")).toContain("学习资料（仅有链接）");
    expect(textOf("turn-result-blocked")).toContain("开源项目（失败）：接口限流");
    expect(textOf("turn-result-gaps")).toContain("接口限流");
  });

  it("可重试失败给出真实恢复与用户时区的可用时间", () => {
    const onContinue = vi.fn();
    render(
      <TurnResultCard
        onContinue={onContinue}
        result={projection({
          outcome: "failed",
          outcome_label: "本轮未完成。",
          trust: null,
          trust_label: null,
          delivered: [],
          capability_list: [],
          actual_module_id: null,
          recovery: {
            action: "wait",
            label: "请稍后重试。",
            retryable: true,
            available_after: "2026-10-05T12:00:00+00:00",
          },
        })}
      />
    );
    const recovery = textOf("turn-result-recovery");
    expect(recovery).toContain("请稍后重试。");
    expect(recovery).toContain("可在此时间后重试：");
    fireEvent.click(screen.getByTestId("turn-result-retry"));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("冷却未到时禁用重试入口，到点后才可继续", () => {
    const onContinue = vi.fn();
    const future = new Date(Date.now() + 60 * 60 * 1000).toISOString();
    render(
      <TurnResultCard
        onContinue={onContinue}
        result={projection({
          outcome: "failed",
          outcome_label: "本轮未完成。",
          trust: null,
          trust_label: null,
          delivered: [],
          capability_list: [],
          actual_module_id: null,
          recovery: {
            action: "wait",
            label: "请稍后重试。",
            retryable: true,
            available_after: future,
          },
        })}
      />
    );
    const retry = screen.getByTestId<HTMLButtonElement>("turn-result-retry");
    expect(retry.disabled).toBe(true);
    fireEvent.click(retry);
    expect(onContinue).not.toHaveBeenCalled();
  });

  it("复合步骤状态用中文短标签呈现，不泄露内部英文值", () => {
    render(
      <TurnResultCard
        result={projection({
          outcome: "partial",
          outcome_label: "本轮先交付有效部分，其余未完成。",
          trust: "evidence_bound",
          trust_label: "结果已附证据，仍有未完成核验的范围。",
          delivered: [
            { module_id: "paper", label: "论文", state: "completed", trust: "qualified", detail: "" },
          ],
          blocked: [
            { module_id: "github", label: "开源项目", state: "invalidated", trust: "evidence_bound", detail: "上游步骤失效" },
          ],
        })}
      />
    );
    expect(textOf("turn-result-delivered")).toContain("论文（已完成）");
    expect(textOf("turn-result-blocked")).toContain("开源项目（已失效）");
  });

  it("待输入展示等待原因且不提供自动续跑", () => {
    render(
      <TurnResultCard
        result={projection({
          outcome: "needs_input",
          outcome_label: "等待你补充信息后继续；任务保持待输入。",
          trust: null,
          trust_label: null,
          delivered: [],
          blocked: [],
          capability_list: [],
          actual_module_id: null,
          wait_reason: "需要明确学习方向",
        })}
      />
    );
    expect(textOf("turn-result-wait")).toContain("需要明确学习方向");
    expect(screen.queryByTestId("turn-result-retry")).toBeNull();
    expect(screen.queryByTestId("turn-result-recovery")).toBeNull();
  });

  it("取消展示停止文案且没有恢复入口", () => {
    render(
      <TurnResultCard
        result={projection({
          outcome: "cancelled",
          outcome_label: "本轮已停止，不会自动继续。",
          trust: null,
          trust_label: null,
          delivered: [],
          capability_list: [],
          actual_module_id: null,
        })}
      />
    );
    expect(textOf("turn-result-outcome")).toContain("不会自动继续");
    expect(screen.queryByTestId("turn-result-retry")).toBeNull();
  });
});
