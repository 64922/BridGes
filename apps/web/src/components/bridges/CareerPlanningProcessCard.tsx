"use client";

import { Icon } from "@/components/design-system/Icon";
import type { ChatStreamCareerData } from "@/lib/api";

/**
 * 生涯规划过程卡（Issue 29 / Issue 09）。
 *
 * 四阶段轨迹：收集信息 → 形成路径 → 校验计划 → 完成；当前阶段高亮，
 * 已完成阶段打勾。五态：
 * - loading —— 正在执行编排步骤，展示阶段轨迹与当前步骤说明；
 * - clarify —— 信息不足，先问一个关键澄清问题（Issue 09 intake，不启动
 *   完整规划生成）；用户直接回复答案即可继续；
 * - empty —— 没有可用画像与材料证据（回答只基于用户陈述并明确标注未知）；
 * - error —— 失败说明（含安全替代步骤）；
 * - permission —— 能力不可用；
 * - recovery —— 失败后可重试（输入保留）。
 * 终态由 done/error 事件携带结果投影接管。
 */

/** 四阶段中文标签（与后端步骤轨迹语义对齐）。 */
const PHASES = ["收集信息", "形成路径", "校验计划", "完成"] as const;

/**
 * 由已完成步骤轨迹推算当前阶段索引（0-based；澄清态停在「收集信息」）。
 * 步骤轨迹来自后端生涯服务（编译可用证据 / 生成六类规划结果 / 复核证据与边界）。
 */
function phaseIndex(steps: readonly string[], state: string): number {
  if (state === "done") return PHASES.length;
  let index = 0;
  if (steps.includes("编译可用证据")) index = Math.max(index, 1);
  if (steps.includes("生成六类规划结果")) index = Math.max(index, 2);
  if (steps.includes("复核证据与边界")) index = Math.max(index, 3);
  return index;
}

function PhaseTrack({ current }: { current: number }) {
  return (
    <ol
      aria-label="生涯规划阶段"
      style={{
        display: "flex",
        gap: "var(--space-1)",
        margin: 0,
        padding: 0,
        listStyle: "none",
        flexWrap: "wrap",
      }}
    >
      {PHASES.map((phase, index) => {
        const completed = index < current;
        const active = index === current;
        return (
          <li
            key={phase}
            aria-current={active ? "step" : undefined}
            data-testid={`career-phase-${index + 1}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "var(--space-1)",
              padding: "var(--space-1) var(--space-2)",
              borderRadius: "var(--radius-full)",
              fontSize: "var(--text-xs)",
              border: `1px solid ${
                active ? "var(--color-accent-primary)" : "var(--color-border)"
              }`,
              color: completed
                ? "var(--color-text-tertiary)"
                : active
                  ? "var(--color-accent-primary)"
                  : "var(--color-text-tertiary)",
            }}
          >
            {completed ? (
              <Icon name="check" size={12} aria-hidden />
            ) : (
              <span aria-hidden style={{ opacity: active ? 1 : 0.5 }}>
                {index + 1}
              </span>
            )}
            {phase}
          </li>
        );
      })}
    </ol>
  );
}

export function CareerPlanningProcessCard({
  data,
  streaming,
  onRetry,
}: {
  data: ChatStreamCareerData | null;
  streaming: boolean;
  onRetry?: () => void;
}) {
  // 流式进行中且尚无任何过程数据：loading 初始态
  const state = data?.state ?? (streaming ? "loading" : "error");
  const steps = data?.progress_steps ?? [];
  const stepLabel = data?.step_label ?? "正在准备生涯规划…";
  const detail = data?.detail ?? null;
  const retryable = data?.retryable ?? false;

  const style: React.CSSProperties = {
    display: "flex",
    alignItems: "flex-start",
    gap: "var(--space-2)",
    padding: "var(--space-3) var(--space-4)",
    borderRadius: "var(--radius-md)",
    border: "1px solid var(--color-border)",
    backgroundColor: "var(--color-surface)",
    fontSize: "var(--text-sm)",
    color: "var(--color-text-primary)",
    marginTop: "var(--space-2)",
  };
  const iconStyle: React.CSSProperties = {
    color: "var(--color-accent-primary)",
    display: "inline-flex",
    marginTop: 2,
  };

  if (state === "clarify") {
    return (
      <section role="status" data-testid="career-process-clarify" style={style}>
        <PhaseTrack current={0} />
        <span style={iconStyle}>
          <Icon name="career" size={18} aria-hidden />
        </span>
        <div>
          <strong>需要补充一个关键信息</strong>
          <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)" }}>
            {detail ?? "请补充你的目标方向与当前阶段，我好为你规划。"}
          </p>
          <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-tertiary)" }}>
            直接回复你的答案即可继续规划。
          </p>
        </div>
      </section>
    );
  }

  if (state === "empty") {
    return (
      <section role="status" data-testid="career-process-empty" style={style}>
        <span style={iconStyle}>
          <Icon name="info" size={18} aria-hidden />
        </span>
        <div>
          <strong>没有可用画像与材料证据</strong>
          <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)" }}>
            {detail ??
              "本轮没有找到可用的画像、学习记录或检索材料，回答将只基于你的本次陈述，并明确标注需要核查的未知。"}
          </p>
        </div>
      </section>
    );
  }

  if (state === "error" || state === "permission") {
    const permission = state === "permission";
    return (
      <section
        role="alert"
        data-testid={permission ? "career-process-permission" : "career-process-error"}
        style={{ ...style, borderColor: "var(--color-status-error)" }}
      >
        <span style={{ ...iconStyle, color: "var(--color-status-error)" }}>
          <Icon name="alert" size={18} aria-hidden />
        </span>
        <div>
          <strong>{permission ? "生涯规划能力暂不可用" : "生涯规划未完成"}</strong>
          <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)" }}>
            {detail ??
              (permission
                ? "核心对话能力当前不可用，请检查启动服务的全局百炼配置与权限。"
                : "规划生成失败，请重试。")}
          </p>
          {retryable && onRetry && (
            <button
              type="button"
              data-testid="career-process-retry"
              onClick={onRetry}
              style={{
                marginTop: "var(--space-2)",
                padding: "var(--space-1) var(--space-3)",
                border: "1px solid var(--color-border-strong)",
                borderRadius: "var(--radius-md)",
                background: "transparent",
                color: "var(--color-text-primary)",
                cursor: "pointer",
                fontSize: "var(--text-sm)",
              }}
            >
              重试（原问题已保留）
            </button>
          )}
        </div>
      </section>
    );
  }

  if (state === "recovery") {
    return (
      <section role="status" data-testid="career-process-recovery" style={{ ...style, borderColor: "var(--color-border-strong)" }}>
        <span style={iconStyle}>
          <Icon name="retry" size={18} aria-hidden />
        </span>
        <div>
          <strong>规划失败，可从原问题重试</strong>
          <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)" }}>
            {detail ?? "规划生成失败，你的问题与输入已保留。"}
          </p>
          {onRetry && (
            <button
              type="button"
              data-testid="career-process-recovery-retry"
              onClick={onRetry}
              style={{
                marginTop: "var(--space-2)",
                padding: "var(--space-1) var(--space-3)",
                border: "1px solid var(--color-border-strong)",
                borderRadius: "var(--radius-md)",
                background: "transparent",
                color: "var(--color-text-primary)",
                cursor: "pointer",
                fontSize: "var(--text-sm)",
              }}
            >
              重试
            </button>
          )}
        </div>
      </section>
    );
  }

  // loading（含初始态）：展示四阶段轨迹 + 当前步骤说明
  const current = phaseIndex(steps, state);
  return (
    <section role="status" data-testid="career-process-loading" style={style}>
      <PhaseTrack current={current} />
      <span style={iconStyle} className="humanizer-spin" aria-hidden="true">
        <Icon name="career" size={18} />
      </span>
      <div>
        <strong>{stepLabel}</strong>
        {steps.length > 0 && (
          <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)" }}>
            已完成的步骤：{steps.join(" → ")}
          </p>
        )}
        {detail && (
          <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)" }}>{detail}</p>
        )}
      </div>
    </section>
  );
}
