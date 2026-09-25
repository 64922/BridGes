"use client";

import { Icon } from "@/components/design-system/Icon";
import type { ChatStreamHumanizerData } from "@/lib/api";

/**
 * 文章人味化过程卡（Issue 28）：中文五态。
 *
 * loading —— 正在执行编排步骤（解析任务契约/提取事实锁/按体裁规则生成/
 * 确定性复核），展示步骤轨迹；empty —— 没有可改写的原文或主题；error ——
 * 失败说明；permission —— 能力不可用；recovery —— 任务已停止（能力已
 * 退役，仅可查看，不再重试）。终态由 done/error 事件携带结果投影接管。
 */
export function HumanizerProcessCard({
  data,
  streaming,
}: {
  data: ChatStreamHumanizerData | null;
  streaming: boolean;
}) {
  // 流式进行中且尚无任何过程数据：loading 初始态
  const state = data?.state ?? (streaming ? "loading" : "error");
  const steps = data?.progress_steps ?? [];
  const stepLabel = data?.step_label ?? "正在准备人味化任务…";
  const detail = data?.detail ?? null;

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

  if (state === "empty") {
    return (
      <section role="status" data-testid="humanizer-process-empty" style={style}>
        <span style={iconStyle}>
          <Icon name="info" size={18} aria-hidden />
        </span>
        <div>
          <strong>人味化任务缺少输入</strong>
          <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)" }}>
            {detail ?? "请粘贴要改写的原文、选择当前账户文件，或填写要生成的文章主题。"}
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
        data-testid={permission ? "humanizer-process-permission" : "humanizer-process-error"}
        style={{ ...style, borderColor: "var(--color-status-error)" }}
      >
        <span style={{ ...iconStyle, color: "var(--color-status-error)" }}>
          <Icon name="alert" size={18} aria-hidden />
        </span>
        <div>
          <strong>{permission ? "人味化能力暂不可用" : "人味化任务未完成"}</strong>
          <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)" }}>
            {detail ??
              (permission
                ? "核心对话能力当前不可用，请检查启动服务的全局百炼配置与权限。"
                : "任务未完成。文章人味化能力已退役，历史输入与结果仍可查看。")}
          </p>
        </div>
      </section>
    );
  }

  if (state === "recovery") {
    return (
      <section role="status" data-testid="humanizer-process-recovery" style={{ ...style, borderColor: "var(--color-border-strong)" }}>
        <span style={iconStyle}>
          <Icon name="retry" size={18} aria-hidden />
        </span>
        <div>
          <strong>任务已停止</strong>
          <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)" }}>
            {detail ?? "文章人味化能力已退役，历史输入与结果仍可查看。"}
          </p>
        </div>
      </section>
    );
  }

  // loading（含初始态）
  return (
    <section role="status" data-testid="humanizer-process-loading" style={style}>
      <span style={iconStyle} className="humanizer-spin" aria-hidden="true">
        <Icon name="humanize" size={18} />
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
