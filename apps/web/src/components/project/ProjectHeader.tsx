"use client";

import { useEffect, useState } from "react";

import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { StatusBadge } from "@/components/design-system/StatusBadge";
import { getProject, type Project } from "@/lib/api";

interface ProjectHeaderProps {
  projectId: string;
}

function domainLabel(domain: string): string {
  switch (domain) {
    case "personal_vault":
      return "个人保险库";
    case "shared_project":
      return "显式共享项目";
    case "institution_owned":
      return "机构自有项目";
    default:
      return domain;
  }
}

function roleLabel(role: string): string {
  switch (role) {
    case "owner":
      return "所有者";
    case "editor":
      return "编辑者";
    case "reviewer":
      return "审阅者";
    case "viewer":
      return "查看者";
    default:
      return role;
  }
}

/**
 * Project header shown inside the project main shell.
 *
 * Communicates project identity, object domain, role, and active task status.
 * All status indicators use redundant text + icon + color encoding. The
 * projection is re-authenticated on every load so deep links and refreshes
 * restore the same owned project state.
 */
export function ProjectHeader({ projectId }: ProjectHeaderProps) {
  const [project, setProject] = useState<Project | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    getProject(projectId)
      .then((data) => {
        if (!cancelled) {
          setProject(data);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "加载项目失败");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  if (isLoading) {
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
        <LoadingStatus message="正在加载项目上下文…" />
      </header>
    );
  }

  if (error || project === null) {
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
        <h1
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: "var(--text-xl)",
            fontWeight: 600,
            marginBottom: "var(--space-3)",
          }}
        >
          无法加载项目
        </h1>
        <ErrorSummary errors={[error || "项目不存在或没有访问权限。"]} />
      </header>
    );
  }

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
            {project.name}
          </h1>
          <p
            style={{
              color: "var(--color-text-secondary)",
              marginTop: "var(--space-1)",
              maxWidth: "100%",
              overflowWrap: "break-word",
            }}
          >
            所有者：当前用户 · 对象域：{domainLabel(project.object_domain)} · 角色：
            {roleLabel(project.role)} · 版本 {project.version}
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
        <span>最近版本：T004</span>
        <span>人工待办：0</span>
      </div>
    </header>
  );
}
