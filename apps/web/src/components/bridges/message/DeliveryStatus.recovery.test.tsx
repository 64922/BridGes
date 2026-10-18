import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeliveryStatus } from "./DeliveryStatus";
import { message, projection, textOf } from "./DeliveryStatus.test-support";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("DeliveryStatus 恢复入口（工单 07）", () => {
  it("可重试失败显示真实恢复与用户时区可用时间", () => {
    const onRetry = vi.fn();
    render(
      <DeliveryStatus
        onRetry={onRetry}
        message={message({
          status: "error",
          turnResult: projection({
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
          }),
        })}
      />
    );
    const recovery = textOf("delivery-recovery");
    expect(recovery).toContain("请稍后重试。");
    expect(recovery).toContain("可在此时间后重试：");
    fireEvent.click(screen.getByTestId("delivery-retry"));
    expect(onRetry).toHaveBeenCalledWith("assistant-1");
  });

  it("不可重试失败不提供可点击的虚假恢复按钮", () => {
    render(
      <DeliveryStatus
        onRetry={vi.fn()}
        message={message({
          status: "error",
          turnResult: projection({
            outcome: "failed",
            outcome_label: "本轮未完成。",
            trust: null,
            trust_label: null,
            delivered: [],
            capability_list: [],
            actual_module_id: null,
            recovery: {
              action: "none",
              label: "该能力已退役，无法重试。",
              retryable: false,
              available_after: null,
            },
          }),
        })}
      />
    );
    expect(textOf("delivery-outcome")).toContain("本轮未完成");
    expect(textOf("delivery-recovery")).toContain("无法重试");
    expect(screen.queryByTestId("delivery-retry")).toBeNull();
  });

  it("退役能力的可重试失败只保留说明，不提供会被拒绝的重试按钮", () => {
    render(
      <DeliveryStatus
        onRetry={vi.fn()}
        message={message({
          status: "error",
          turnResult: projection({
            outcome: "failed",
            outcome_label: "本轮未完成。",
            trust: null,
            trust_label: null,
            capability_list: ["career"],
            actual_module_id: "career",
            requested_module_id: "career",
            recovery: {
              action: "wait",
              label: "请稍后重试。",
              retryable: true,
              available_after: "2099-10-05T12:00:00+00:00",
            },
          }),
        })}
      />
    );
    expect(textOf("delivery-recovery")).toContain("请稍后重试");
    expect(screen.queryByTestId("delivery-retry")).toBeNull();
  });

  it("没有恢复投影的失败如实显示终态且不伪造操作", () => {
    render(
      <DeliveryStatus
        onRetry={vi.fn()}
        message={message({
          status: "error",
          turnResult: projection({
            outcome: "failed",
            outcome_label: "本轮未完成。",
            trust: null,
            trust_label: null,
            delivered: [],
            capability_list: [],
            actual_module_id: null,
            recovery: null,
          }),
        })}
      />
    );
    expect(textOf("delivery-outcome")).toContain("本轮未完成");
    expect(screen.queryByTestId("delivery-recovery")).toBeNull();
    expect(screen.queryByTestId("delivery-retry")).toBeNull();
  });

  it("冷却未到时禁用重试，到时只解除限制而不自动请求", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-06T00:00:00Z"));
    const onRetry = vi.fn();
    const future = new Date(Date.now() + 60 * 60 * 1000).toISOString();
    render(
      <DeliveryStatus
        onRetry={onRetry}
        message={message({
          status: "error",
          turnResult: projection({
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
          }),
        })}
      />
    );
    const retry = screen.getByTestId<HTMLButtonElement>("delivery-retry");
    expect(retry.disabled).toBe(true);
    fireEvent.click(retry);
    expect(onRetry).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(60 * 60 * 1000 - 60));
    expect(retry.disabled).toBe(true);
    act(() => vi.advanceTimersByTime(120));
    expect(retry.disabled).toBe(false);
    expect(onRetry).not.toHaveBeenCalled();
    fireEvent.click(retry);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
