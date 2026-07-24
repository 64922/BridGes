import { StatusBadge } from "@/components/design-system/StatusBadge";

interface ProjectHeaderProps {
  projectId: string;
}

/**
 * Project header shown inside the project main shell.
 *
 * Communicates project identity, object domain, role, and active task status.
 * All status indicators use redundant text + icon + color encoding.
 */
export function ProjectHeader({ projectId }: ProjectHeaderProps) {
  return (
    <header
      style={{
        backgroundColor: "var(--color-surface)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-lg)",
        padding: "var(--space-5)",
        marginBottom: "var(--space-6)",
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: "var(--space-4)",
        }}
      >
        <div style={{ flex: "1 1 0", minWidth: 0 }}>
          <p className="sc-landmark-label">科学项目空间</p>
          <h1
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: "var(--text-2xl)",
              fontWeight: 600,
              marginTop: "var(--space-1)",
              minWidth: 0,
            }}
          >
            示例项目 #{projectId}
          </h1>
          <p
            style={{
              color: "var(--color-text-secondary)",
              marginTop: "var(--space-1)",
              maxWidth: "100%",
              overflowWrap: "break-word",
            }}
          >
            所有者：当前用户 · 对象域：个人保险库 · 角色：所有者
          </p>
        </div>
        <div
          style={{
            display: "flex",
            flex: "0 0 auto",
            flexWrap: "wrap",
            gap: "var(--space-2)",
            alignItems: "center",
          }}
        >
          <StatusBadge status="running" label="运行 RUNNING" />
          <StatusBadge status="evidence_bound" label="产物 EVIDENCE_BOUND" />
        </div>
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "var(--space-4)",
          marginTop: "var(--space-4)",
          paddingTop: "var(--space-4)",
          borderTop: "1px solid var(--color-border)",
          fontSize: "var(--text-sm)",
          color: "var(--color-text-secondary)",
        }}
      >
        <span>领域包：通用科学</span>
        <span>同步状态：已同步</span>
        <span>最近版本：T002</span>
        <span>人工待办：0</span>
      </div>
    </header>
  );
}
