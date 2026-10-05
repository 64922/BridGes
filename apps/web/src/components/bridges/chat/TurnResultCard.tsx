"use client";

import { Icon } from "@/components/design-system/Icon";
import { chatModuleIcon, chatModuleLabel } from "@/lib/chat-modules";
import type { TurnResultBlock, TurnResultProjection } from "@/lib/api";

/**
 * 公开回合结果卡（改进工单 38）。
 *
 * 只呈现服务端已发布的回合结果投影：交付分类与可信状态分开、请求模块
 * 提示与实际执行能力分开、已交付与被阻塞结果块如实列出、恢复方式只在
 * 真实可用时给出。卡内不出现内部步骤、证据原文、思维链或待核验草稿。
 */
export function TurnResultCard({
  result,
  onContinue,
}: {
  result: TurnResultProjection | null;
  /** 可重试恢复：用户明确继续时创建新运行（绝不自动续跑）。 */
  onContinue?: () => void;
}) {
  if (!result) return null;
  const tone = OUTCOME_TONE[result.outcome] ?? OUTCOME_TONE.unknown;
  const capability = capabilitySummary(result);
  const delivered = result.delivered ?? [];
  const blocked = result.blocked ?? [];
  const gaps = result.gaps ?? [];
  const waiting = isWaiting(result.recovery?.available_after ?? null);
  return (
    <section
      data-testid="turn-result"
      data-outcome={result.outcome}
      aria-label="本轮结果"
      style={{
        margin: "var(--space-3) 0 0",
        padding: "var(--space-3)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-md)",
        backgroundColor: "var(--color-bg-secondary)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
      }}
    >
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "var(--space-2)" }}>
        <span
          data-testid="turn-result-outcome"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "var(--space-1)",
            padding: "2px var(--space-2)",
            borderRadius: "var(--radius-sm)",
            border: `1px solid ${tone.border}`,
            backgroundColor: tone.background,
            color: tone.color,
            fontSize: "var(--text-xs)",
            fontWeight: 600,
          }}
        >
          <Icon name={tone.icon} size={14} aria-hidden />
          {result.outcome_label}
        </span>
        {result.trust_label && (
          <span
            data-testid="turn-result-trust"
            style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}
          >
            {result.trust_label}
          </span>
        )}
      </div>

      {capability && (
        <p
          data-testid="turn-result-capability"
          style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}
        >
          {capability}
        </p>
      )}

      {delivered.length > 0 && (
        <ResultBlocks title="已交付" blocks={delivered} testId="turn-result-delivered" />
      )}
      {blocked.length > 0 && (
        <ResultBlocks title="未完成" blocks={blocked} testId="turn-result-blocked" />
      )}

      {gaps.length > 0 && (
        <div data-testid="turn-result-gaps">
          <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
            待补齐
          </p>
          <ul style={{ margin: "var(--space-1) 0 0", paddingLeft: "var(--space-4)", fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
            {gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </div>
      )}

      {result.wait_reason && (
        <p
          data-testid="turn-result-wait"
          style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-wait)" }}
        >
          等待你补充：{result.wait_reason}
        </p>
      )}

      {result.recovery && (
        <div
          data-testid="turn-result-recovery"
          style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "var(--space-2)" }}
        >
          <span style={{ fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
            {result.recovery.label}
            {result.recovery.available_after
              ? `可在此时间后重试：${formatLocalTime(result.recovery.available_after)}`
              : ""}
          </span>
          {result.recovery.retryable && onContinue && (
            <button
              type="button"
              data-testid="turn-result-retry"
              onClick={onContinue}
              disabled={waiting}
              style={{
                minHeight: "var(--target-size)",
                padding: "var(--space-1) var(--space-3)",
                border: "1px solid var(--color-accent-primary)",
                borderRadius: "var(--radius-md)",
                backgroundColor: "var(--color-accent-primary-soft)",
                color: "var(--color-text-primary)",
                font: "inherit",
                fontWeight: 600,
                cursor: waiting ? "not-allowed" : "pointer",
                opacity: waiting ? 0.6 : 1,
              }}
            >
              继续生成
            </button>
          )}
        </div>
      )}
    </section>
  );
}

const OUTCOME_TONE: Record<
  string,
  {
    color: string;
    background: string;
    border: string;
    icon: "check" | "alert" | "info" | "stopSquare";
  }
> = {
  complete: {
    color: "var(--color-status-success)",
    background: "var(--color-status-success-bg)",
    border: "var(--color-status-success)",
    icon: "check",
  },
  partial: {
    color: "var(--color-status-wait)",
    background: "var(--color-status-wait-bg)",
    border: "var(--color-status-wait)",
    icon: "alert",
  },
  blocked: {
    color: "var(--color-status-wait)",
    background: "var(--color-status-wait-bg)",
    border: "var(--color-status-wait)",
    icon: "alert",
  },
  needs_input: {
    color: "var(--color-status-info)",
    background: "var(--color-status-info-bg)",
    border: "var(--color-status-info)",
    icon: "info",
  },
  cancelled: {
    color: "var(--color-status-unknown)",
    background: "var(--color-status-unknown-bg)",
    border: "var(--color-status-unknown)",
    icon: "stopSquare",
  },
  failed: {
    color: "var(--color-status-error)",
    background: "var(--color-status-error-bg)",
    border: "var(--color-status-error)",
    icon: "alert",
  },
  unknown: {
    color: "var(--color-status-unknown)",
    background: "var(--color-status-unknown-bg)",
    border: "var(--color-status-unknown)",
    icon: "info",
  },
};

/** 结果块状态 → 面向用户的中文短标签（真实领域/复合步骤状态值）。 */
const BLOCK_STATE_LABEL: Record<string, string> = {
  success: "已完成",
  completed: "已完成",
  links_only: "仅有链接",
  metadata_only: "仅元数据",
  unverified: "未核实",
  clarification: "待补充",
  error: "失败",
  failed: "失败",
  blocked: "已阻塞",
  invalidated: "已失效",
  stopped: "已停止",
};

function ResultBlocks({
  title,
  blocks,
  testId,
}: {
  title: string;
  blocks: TurnResultBlock[];
  testId: string;
}) {
  return (
    <div data-testid={testId}>
      <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        {title}
      </p>
      <ul style={{ margin: "var(--space-1) 0 0", padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
        {blocks.map((block) => (
          <li key={block.module_id} style={{ display: "flex", alignItems: "flex-start", gap: "var(--space-2)" }}>
            <Icon name={chatModuleIcon(block.module_id)} size={16} aria-hidden />
            <span style={{ fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
              {block.label}
              <span style={{ color: "var(--color-text-tertiary)" }}>
                （{BLOCK_STATE_LABEL[block.state] ?? block.state}）
              </span>
              {block.detail ? `：${block.detail}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const ROUTE_SOURCE_LABEL: Record<string, string> = {
  ordinary_chat: "普通对话",
  body_intent: "正文意图",
  module_hint: "模块菜单选择",
  suggestion_click: "建议一键启动",
  task_continuation: "任务续接",
  learning_strategy: "学习策略",
};

function capabilitySummary(result: TurnResultProjection): string | null {
  const requested = chatModuleLabel(result.requested_module_id) ?? result.requested_module_id;
  const actual = chatModuleLabel(result.actual_module_id) ?? result.actual_module_id;
  const capabilityLabels = (result.capability_list ?? [])
    .map((item) => chatModuleLabel(item) ?? item)
    .filter((item, index, all) => all.indexOf(item) === index);
  const parts: string[] = [];
  if (requested && actual && requested !== actual) {
    parts.push(`请求模块：${requested}`);
    parts.push(`实际执行：${actual}`);
  } else if (actual) {
    parts.push(`实际执行：${actual}`);
  } else if (capabilityLabels.length > 0) {
    parts.push(`实际能力：${capabilityLabels.join("、")}`);
  }
  const routeSource = result.route_source ? ROUTE_SOURCE_LABEL[result.route_source] : null;
  if (routeSource) parts.push(`来源：${routeSource}`);
  return parts.length > 0 ? parts.join(" · ") : null;
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
