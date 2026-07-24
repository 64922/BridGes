import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { StatusBadge } from "@/components/design-system/StatusBadge";

/**
 * Task stage card.
 *
 * Displays the current workflow goal, run status, artifact status, current
 * node, and available human actions. This component replaces implicit progress
 * hidden in chat messages.
 */
export function TaskStage() {
  return (
    <section
      aria-labelledby="task-stage-title"
      style={{
        backgroundColor: "var(--color-surface)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-lg)",
        padding: "var(--space-5)",
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "var(--space-4)",
          marginBottom: "var(--space-4)",
        }}
      >
        <div>
          <p className="sc-landmark-label">任务舞台</p>
          <h2
            id="task-stage-title"
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: "var(--text-xl)",
              fontWeight: 600,
              marginTop: "var(--space-1)",
            }}
          >
            整理项目证据并生成首份表达草稿
          </h2>
        </div>
        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          <StatusBadge status="running" />
          <StatusBadge status="evidence_bound" />
        </div>
      </div>

      <ol
        aria-label="任务节点"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(8rem, 1fr))",
          gap: "var(--space-3)",
          marginBottom: "var(--space-4)",
        }}
      >
        {[
          { label: "确认目标", state: "completed" as const },
          { label: "收集来源", state: "completed" as const },
          { label: "建立 Claim", state: "current" as const },
          { label: "事实锁映射", state: "pending" as const },
          { label: "生成草稿", state: "pending" as const },
        ].map((step, index) => (
          <li
            key={step.label}
            aria-current={step.state === "current" ? "step" : undefined}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              padding: "var(--space-3)",
              borderRadius: "var(--radius-md)",
              backgroundColor:
                step.state === "current"
                  ? "var(--color-status-info-bg)"
                  : step.state === "completed"
                    ? "var(--color-status-success-bg)"
                    : "var(--color-bg-secondary)",
              color:
                step.state === "current"
                  ? "var(--color-status-info)"
                  : step.state === "completed"
                    ? "var(--color-status-success)"
                    : "var(--color-text-secondary)",
              fontWeight: step.state === "current" ? 600 : 400,
              border: `1px solid ${
                step.state === "current"
                  ? "var(--color-status-info)"
                  : step.state === "completed"
                    ? "var(--color-status-success)"
                    : "var(--color-border)"
              }`,
            }}
          >
            <span aria-hidden="true" style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)" }}>
              {index + 1}
            </span>
            {step.state === "completed" && <Icon name="check" size={16} ariaLabel="已完成" />}
            {step.state === "current" && <Icon name="info" size={16} ariaLabel="当前" />}
            <span>{step.label}</span>
          </li>
        ))}
      </ol>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "var(--space-3)",
          alignItems: "center",
        }}
      >
        <Button variant="secondary" aria-label="取消任务">
          取消
        </Button>
        <Button variant="secondary" aria-label="重试当前节点">
          重试
        </Button>
        <p role="status" aria-live="polite" style={{ color: "var(--color-text-secondary)" }}>
          当前节点：建立 Claim
        </p>
      </div>
    </section>
  );
}
