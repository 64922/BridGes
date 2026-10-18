"use client";

import { useEffect, useState, type CSSProperties } from "react";

import { Icon, type IconName } from "@/components/design-system/Icon";
import { chatModuleLabel } from "@/lib/chat-modules";
import type { TurnResultBlock, TurnResultProjection } from "@/lib/api";
import type { ChatMessageView } from "@/lib/chat-message-view";

/**
 * 已交付、未完成、待补充、可信限制与真实恢复的就近说明。
 *
 * 这里不是固定结果卡：不重复抄写完整结果投影，只保留用户此刻需要判断
 * “哪些已交付、哪些还缺、现在能做什么”的信息。恢复按钮只在服务端投影
 * 明确标记可重试且存在真实续跑命令时出现；冷却结束只解除禁用，不自动请求。
 */
export function DeliveryStatus({
  message,
  onRetry,
}: {
  message: ChatMessageView;
  onRetry?: (id: string) => void;
}) {
  const result = message.turnResult;
  const stopped = message.status === "stopped";
  if (!result && !stopped) return null;
  if (stopped) {
    return (
      <DeliveryNote
        message={message}
        result={result ?? null}
        outcome="cancelled"
        label="本轮已停止，不会自动继续；已生成的内容保留。"
        icon="stopSquare"
        tone="var(--color-text-secondary)"
        showDelivered
        action={
          onRetry && hasRealRetryCommand(message, result ?? null)
            ? { label: "继续生成", onContinue: () => onRetry(message.id) }
            : undefined
        }
      />
    );
  }
  if (!result) return null;

  if (result.outcome === "running") {
    if ((result.delivered ?? []).length === 0) return null;
    const interrupted = message.status !== "streaming";
    return (
      <DeliveryNote
        message={message}
        result={result}
        outcome={interrupted ? "partial" : "running"}
        label={
          interrupted
            ? message.status === "error"
              ? "生成中断，已交付的部分保留。"
              : message.status === "stopped"
                ? "本轮已停止，已交付的部分保留。"
                : "本轮已生成部分内容，最终交付状态以刷新后的结果为准。"
            : result.outcome_label
        }
        icon={interrupted ? "alert" : "info"}
        tone={
          interrupted ? "var(--color-status-wait)" : "var(--color-accent-primary)"
        }
        showDelivered
        showTrust
      />
    );
  }

  if (result.outcome === "needs_input") {
    return (
      <DeliveryNote
        message={message}
        result={result}
        outcome="needs_input"
        label={
          result.wait_reason ? `等待你补充：${result.wait_reason}` : result.outcome_label
        }
        icon="info"
        tone="var(--color-status-info)"
      />
    );
  }

  if (result.outcome === "failed") {
    return (
      <DeliveryNote
        message={message}
        result={result}
        outcome="failed"
        label={result.outcome_label}
        icon="alert"
        tone="var(--color-status-error)"
        showRecovery
        onRetry={hasRealRetryCommand(message, result) ? onRetry : undefined}
      />
    );
  }

  if (result.outcome === "cancelled") {
    return (
      <DeliveryNote
        message={message}
        result={result}
        outcome="cancelled"
        label={result.outcome_label}
        icon="stopSquare"
        tone="var(--color-text-secondary)"
        showDelivered
      />
    );
  }

  if (result.outcome === "complete") {
    const capability = capabilitySummary(result);
    const limitedTrust = result.trust === "evidence_bound";
    if (!capability && !limitedTrust) return null;
    return (
      <DeliveryNote
        message={message}
        result={result}
        outcome="complete"
        label={limitedTrust ? result.outcome_label : undefined}
        icon="check"
        tone={
          limitedTrust ? "var(--color-status-wait)" : "var(--color-status-success)"
        }
        showTrust
        showCapability
      />
    );
  }

  return (
    <DeliveryNote
      message={message}
      result={result}
      outcome={result.outcome}
      label={result.outcome_label}
      icon="alert"
      tone="var(--color-status-wait)"
      showDelivered
      showBlocked
      showGaps
      showTrust
      showCapability
      showRecovery
      onRetry={hasRealRetryCommand(message, result) ? onRetry : undefined}
    />
  );
}

/** 重试接口明确拒绝的退役模块；历史结果只读，不再给恢复入口。 */
const RETIRED_RETRY_MODULES = ["career", "resources", "github"] as const;

export function hasRealRetryCommand(
  message: ChatMessageView,
  result: TurnResultProjection | null
): boolean {
  // 人味化与 SKILL 写路径已退役；历史消息没有真实续跑命令。
  if (message.skill != null) return false;
  if (message.humanizer != null || message.humanizerProcess != null) return false;
  if (
    message.careerPlan != null ||
    message.careerPlanning != null ||
    message.learningResources != null ||
    message.githubProjects != null
  ) {
    return false;
  }
  const moduleIds = [
    result?.requested_module_id,
    result?.actual_module_id,
    ...(result?.capability_list ?? []),
    ...(result?.delivered ?? []).map((block) => block.module_id),
    ...(result?.blocked ?? []).map((block) => block.module_id),
  ];
  return !moduleIds.some(
    (moduleId) =>
      moduleId != null &&
      (RETIRED_RETRY_MODULES as readonly string[]).indexOf(moduleId) !== -1
  );
}

const BLOCK_STATE_LABEL: Record<string, string> = {
  success: "已完成",
  completed: "已完成",
  links_only: "仅有链接",
  metadata_only: "仅元数据",
  unverified: "未核实",
  clarification: "待补充",
  empty: "无结果",
  error: "失败",
  failed: "失败",
  blocked: "已阻塞",
  invalidated: "已失效",
  stopped: "已停止",
};

/** 只说明请求能力与实际执行不一致；不重复正文或完整投影。 */
function capabilitySummary(result: TurnResultProjection): string | null {
  const requested = result.requested_module_id
    ? (chatModuleLabel(result.requested_module_id) ?? result.requested_module_id)
    : null;
  const actual = result.actual_module_id
    ? (chatModuleLabel(result.actual_module_id) ?? result.actual_module_id)
    : null;
  const capabilityLabels = (result.capability_list ?? [])
    .map((item) => chatModuleLabel(item) ?? item)
    .filter((item, index, all) => all.indexOf(item) === index);
  const parts: string[] = [];
  if (requested && actual && requested !== actual) {
    parts.push(`请求模块：${requested}`);
    parts.push(`实际执行：${actual}`);
  }
  if (capabilityLabels.length > 1) {
    parts.push(`实际能力：${capabilityLabels.join("、")}`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

function DeliveryNote({
  message,
  result,
  outcome,
  label,
  icon,
  tone,
  showDelivered = false,
  showBlocked = false,
  showGaps = false,
  showTrust = false,
  showCapability = false,
  showRecovery = false,
  onRetry,
  action,
}: {
  message: ChatMessageView;
  result: TurnResultProjection | null;
  outcome: string;
  label?: string;
  icon: IconName;
  tone: string;
  showDelivered?: boolean;
  showBlocked?: boolean;
  showGaps?: boolean;
  showTrust?: boolean;
  showCapability?: boolean;
  showRecovery?: boolean;
  onRetry?: (id: string) => void;
  action?: { label: string; onContinue: () => void };
}) {
  const delivered = result?.delivered ?? [];
  const blocked = result?.blocked ?? [];
  const gaps = uniqueItems(result?.gaps ?? []).filter(
    (gap) => !blocked.some((block) => block.detail === gap)
  );
  const capability = showCapability ? capabilitySummary(result as TurnResultProjection) : null;

  return (
    <section
      data-testid="delivery-status"
      data-outcome={outcome}
      aria-label="本轮交付状态"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
        margin: "var(--space-3) 0 0",
        padding: "var(--space-2) 0",
        borderTop: "1px solid var(--color-border)",
        borderBottom: "1px solid var(--color-border)",
        fontSize: "var(--text-sm)",
        color: "var(--color-text-secondary)",
        overflowWrap: "break-word",
      }}
    >
      {label && (
        <p
          data-testid="delivery-outcome"
          style={{
            margin: 0,
            display: "flex",
            alignItems: "flex-start",
            gap: "var(--space-2)",
            color: tone,
            fontWeight: 600,
          }}
        >
          <span aria-hidden="true" style={{ flexShrink: 0, paddingTop: 2 }}>
            <Icon name={icon} size={16} />
          </span>
          <span>{label}</span>
        </p>
      )}

      {showDelivered && delivered.length > 0 && (
        <p data-testid="delivery-delivered" style={{ margin: 0 }}>
          <span style={{ color: "var(--color-text-tertiary)" }}>已交付：</span>
          {delivered.map((block, index) => (
            <span key={block.module_id}>
              {index > 0 ? "、" : ""}
              {blockSummary(block)}
            </span>
          ))}
        </p>
      )}

      {showBlocked && blocked.length > 0 && (
        <ul
          data-testid="delivery-blocked"
          style={{
            margin: 0,
            paddingLeft: "var(--space-5)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-1)",
          }}
        >
          {blocked.map((block) => (
            <li key={block.module_id}>
              {blockSummary(block)}
              {block.detail ? `：${block.detail}` : ""}
            </li>
          ))}
        </ul>
      )}

      {showGaps && gaps.length > 0 && (
        <p data-testid="delivery-gaps" style={{ margin: 0 }}>
          <span style={{ color: "var(--color-text-tertiary)" }}>待补齐：</span>
          {gaps.join("；")}
        </p>
      )}

      {showTrust && result?.trust_label && (
        <p
          data-testid="delivery-trust"
          style={{
            margin: 0,
            color: "var(--color-text-tertiary)",
            fontSize: "var(--text-xs)",
          }}
        >
          {result.trust_label}
        </p>
      )}

      {capability && (
        <p
          data-testid="delivery-capability"
          style={{
            margin: 0,
            color: "var(--color-text-tertiary)",
            fontSize: "var(--text-xs)",
          }}
        >
          {capability}
        </p>
      )}

      {showRecovery && result?.recovery && (
        <RecoveryLine
          recovery={result.recovery}
          retryLabel={message.status === "stopped" ? "继续生成" : "重试本轮"}
          onRetry={onRetry ? () => onRetry(message.id) : undefined}
        />
      )}

      {action && (
        <div>
          <button
            type="button"
            data-testid="delivery-retry"
            onClick={action.onContinue}
            style={retryButtonStyle(false)}
          >
            {action.label}
          </button>
        </div>
      )}
    </section>
  );
}

function blockSummary(block: TurnResultBlock): string {
  const state = BLOCK_STATE_LABEL[block.state] ?? "未核实";
  return `${block.label}（${state}）`;
}

function uniqueItems(items: string[]): string[] {
  const unique: string[] = [];
  for (const item of items) {
    if (item.trim().length > 0 && unique.indexOf(item) === -1) {
      unique.push(item);
    }
  }
  return unique;
}

function RecoveryLine({
  recovery,
  retryLabel,
  onRetry,
}: {
  recovery: NonNullable<TurnResultProjection["recovery"]>;
  retryLabel: string;
  onRetry?: () => void;
}) {
  const availableAfter = recovery.available_after ?? null;
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!availableAfter) return;
    const remaining = new Date(availableAfter).getTime() - Date.now();
    if (!(remaining > 0)) return;
    // 冷却到时只刷新可执行状态，不自动发起任何请求。
    const timer = window.setTimeout(
      () => setRevision((value) => value + 1),
      Math.min(remaining + 50, 2_147_483_647)
    );
    return () => window.clearTimeout(timer);
  }, [availableAfter, revision]);
  const waiting = isWaiting(availableAfter);
  return (
    <div
      data-testid="delivery-recovery"
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: "var(--space-2)",
        color: "var(--color-text-secondary)",
      }}
    >
      <span>
        {recovery.label}
        {availableAfter
          ? `可在此时间后重试：${formatLocalTime(availableAfter)}`
          : ""}
      </span>
      {recovery.retryable && onRetry && (
        <button
          type="button"
          data-testid="delivery-retry"
          onClick={onRetry}
          disabled={waiting}
          style={retryButtonStyle(waiting)}
        >
          {waiting ? "冷却结束后重试" : retryLabel}
        </button>
      )}
    </div>
  );
}

function retryButtonStyle(disabled: boolean): CSSProperties {
  return {
    minHeight: "var(--target-size)",
    padding: "var(--space-1) var(--space-3)",
    border: "1px solid var(--color-accent-primary)",
    borderRadius: "var(--radius-md)",
    backgroundColor: disabled ? "transparent" : "var(--color-accent-primary-soft)",
    color: disabled ? "var(--color-text-tertiary)" : "var(--color-text-primary)",
    font: "inherit",
    fontWeight: 600,
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.6 : 1,
  };
}

/** 真实恢复时刻按用户本机时区展示（ISO 带时区；无真实时刻不展示）。 */
function formatLocalTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(parsed);
}

/** 冷却未到时重试入口不可点击，避免在限流窗口内重复触发真实调用。 */
function isWaiting(availableAfter: string | null): boolean {
  if (!availableAfter) return false;
  const parsed = new Date(availableAfter);
  return !Number.isNaN(parsed.getTime()) && parsed.getTime() > Date.now();
}
