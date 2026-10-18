import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeliveryStatus } from "./DeliveryStatus";
import { message, projection, textOf } from "./DeliveryStatus.test-support";

afterEach(() => {
  cleanup();
});

describe("DeliveryStatus（工单 07）", () => {
  it("无交付限制的完整成功不额外渲染固定结果卡", () => {
    const { container } = render(<DeliveryStatus message={message()} />);
    expect(container.innerHTML).toBe("");
    expect(screen.queryByTestId("delivery-status")).toBeNull();
  });

  it("完整成功只保留可信限制，不重复抄写已交付全文", () => {
    render(
      <DeliveryStatus
        message={message({
          turnResult: projection({
            trust: "evidence_bound",
            trust_label: "结果已附证据，仍有未完成核验的范围。",
          }),
        })}
      />
    );
    expect(screen.getByTestId("delivery-status").getAttribute("data-outcome")).toBe(
      "complete"
    );
    expect(textOf("delivery-trust")).toContain("结果已附证据");
    expect(screen.queryByTestId("delivery-delivered")).toBeNull();
  });

  it("请求能力与实际执行不一致时在正文附近说明", () => {
    render(
      <DeliveryStatus
        message={message({
          turnResult: projection({
            requested_module_id: "github",
            actual_module_id: "resources",
            capability_list: ["resources"],
          }),
        })}
      />
    );
    const capability = textOf("delivery-capability");
    expect(capability).toContain("请求模块：GitHub 项目推荐");
    expect(capability).toContain("实际执行：学习资料推荐");
  });

  it("复合运行展示去重后的实际能力", () => {
    render(
      <DeliveryStatus
        message={message({
          turnResult: projection({
            capability_list: ["paper", "github", "paper"],
          }),
        })}
      />
    );
    expect(textOf("delivery-capability")).toContain(
      "实际能力：论文搜索、GitHub 项目推荐"
    );
  });

  it("部分交付列出已交付、阻塞与去重后的缺口", () => {
    render(
      <DeliveryStatus
        message={message({
          turnResult: projection({
            outcome: "partial",
            outcome_label: "本轮先交付有效部分，其余未完成。",
            trust: "evidence_bound",
            trust_label: "结果已附证据，仍有未完成核验的范围。",
            delivered: [
              {
                module_id: "paper",
                label: "论文",
                state: "success",
                trust: "qualified",
                detail: "",
              },
              {
                module_id: "resources",
                label: "学习资料",
                state: "links_only",
                trust: "evidence_bound",
                detail: "",
              },
            ],
            blocked: [
              {
                module_id: "github",
                label: "开源项目",
                state: "error",
                trust: "evidence_bound",
                detail: "接口限流",
              },
            ],
            gaps: ["接口限流", "接口限流", "等待上游恢复"],
          }),
        })}
      />
    );
    expect(textOf("delivery-delivered")).toContain("论文（已完成）");
    expect(textOf("delivery-delivered")).toContain("学习资料（仅有链接）");
    expect(textOf("delivery-blocked")).toContain("开源项目（失败）：接口限流");
    const gaps = textOf("delivery-gaps");
    expect(gaps).toContain("等待上游恢复");
    expect(gaps).not.toContain("接口限流");
  });

  it("待输入只展示等待原因，不提供自动续跑", () => {
    render(
      <DeliveryStatus
        onRetry={vi.fn()}
        message={message({
          turnResult: projection({
            outcome: "needs_input",
            outcome_label: "等待你补充信息后继续；任务保持待输入。",
            trust: null,
            trust_label: null,
            delivered: [],
            blocked: [],
            capability_list: [],
            actual_module_id: null,
            wait_reason: "需要明确学习方向",
          }),
        })}
      />
    );
    expect(textOf("delivery-outcome")).toContain("需要明确学习方向");
    expect(screen.queryByTestId("delivery-retry")).toBeNull();
    expect(screen.queryByTestId("delivery-recovery")).toBeNull();
  });

  it("停止后保留已交付正文并只在存在真实命令时提供继续", () => {
    const onRetry = vi.fn();
    render(
      <DeliveryStatus
        onRetry={onRetry}
        message={message({
          status: "stopped",
          attemptNumber: 2,
          turnResult: projection({
            outcome: "running",
            outcome_label: "正在生成。",
            delivered: [
              {
                module_id: "paper",
                label: "论文",
                state: "success",
                trust: "qualified",
                detail: "",
              },
            ],
          }),
        })}
      />
    );
    expect(textOf("delivery-outcome")).toContain("已停止");
    expect(textOf("delivery-delivered")).toContain("论文（已完成）");
    fireEvent.click(screen.getByTestId("delivery-retry"));
    expect(onRetry).toHaveBeenCalledWith("assistant-1");
  });

  it("退役模块的停止消息只保留结果，不提供会被拒绝的继续入口", () => {
    render(
      <DeliveryStatus
        onRetry={vi.fn()}
        message={message({
          status: "stopped",
          turnResult: projection({
            outcome: "cancelled",
            outcome_label: "本轮已停止，不会自动继续。",
            trust: null,
            trust_label: null,
            capability_list: ["career"],
            actual_module_id: "career",
            requested_module_id: "career",
          }),
        })}
      />
    );
    expect(textOf("delivery-outcome")).toContain("已停止");
    expect(screen.queryByTestId("delivery-retry")).toBeNull();
  });

  it("缺失结果投影的停止消息仍明确告知不会自动继续", () => {
    render(
      <DeliveryStatus
        message={message({
          status: "stopped",
          turnResult: null,
        })}
      />
    );
    expect(textOf("delivery-outcome")).toContain("不会自动继续");
    expect(screen.queryByTestId("delivery-delivered")).toBeNull();
  });

  it("迟到的运行中快照在错误终态下不再宣称正在运行", () => {
    render(
      <DeliveryStatus
        message={message({
          status: "error",
          turnResult: projection({
            outcome: "running",
            outcome_label: "正在生成。",
            delivered: [],
          }),
        })}
      />
    );
    expect(screen.queryByTestId("delivery-status")).toBeNull();
  });

  it("迟到的运行中快照在错误终态下保留已交付内容并标记中断", () => {
    render(
      <DeliveryStatus
        message={message({
          status: "error",
          turnResult: projection({
            outcome: "running",
            outcome_label: "正在生成。",
            delivered: [
              {
                module_id: "paper",
                label: "论文",
                state: "success",
                trust: "qualified",
                detail: "",
              },
            ],
          }),
        })}
      />
    );
    expect(textOf("delivery-outcome")).toContain("生成中断");
    expect(textOf("delivery-delivered")).toContain("论文（已完成）");
  });

  it("未知状态按未核实展示，不泄露内部英文值", () => {
    render(
      <DeliveryStatus
        message={message({
          turnResult: projection({
            outcome: "partial",
            outcome_label: "本轮先交付有效部分，其余未完成。",
            blocked: [
              {
                module_id: "resources",
                label: "学习资料",
                state: "future_internal_state",
                trust: "evidence_bound",
                detail: "",
              },
            ],
            gaps: [],
          }),
        })}
      />
    );
    const blocked = textOf("delivery-blocked");
    expect(blocked).toContain("学习资料（未核实）");
    expect(blocked).not.toContain("future_internal_state");
  });

  it("取消结果没有恢复入口，状态标签不泄露内部英文值", () => {
    render(
      <DeliveryStatus
        onRetry={vi.fn()}
        message={message({
          status: "error",
          turnResult: projection({
            outcome: "cancelled",
            outcome_label: "本轮已停止，不会自动继续。",
            trust: null,
            trust_label: null,
            delivered: [
              {
                module_id: "github",
                label: "开源项目",
                state: "invalidated",
                trust: "evidence_bound",
                detail: "上游步骤失效",
              },
            ],
            blocked: [],
            capability_list: [],
            actual_module_id: null,
          }),
        })}
      />
    );
    expect(textOf("delivery-outcome")).toContain("不会自动继续");
    expect(screen.queryByTestId("delivery-retry")).toBeNull();
  });
});
