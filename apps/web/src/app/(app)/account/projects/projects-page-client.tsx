"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/design-system/Button";
import { ButtonLink } from "@/components/design-system/ButtonLink";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { MainContent } from "@/components/layout/MainContent";
import { createProject, listProjects, type Project, type ProjectSummary } from "@/lib/api";

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

function statusLabel(status: string): string {
  return status === "archived" ? "已归档" : "活跃";
}

interface ProjectListProps {
  projects: ProjectSummary[];
  emptyMessage: string;
}

function ProjectList({ projects, emptyMessage }: ProjectListProps) {
  const router = useRouter();

  if (projects.length === 0) {
    return <p style={{ color: "var(--color-text-secondary)" }}>{emptyMessage}</p>;
  }

  return (
    <ul
      role="list"
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(16rem, 1fr))",
        gap: "var(--space-4)",
      }}
    >
      {projects.map((project) => (
        <li key={project.ref.object_id}>
          <article
            className="sc-card"
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-3)",
              minHeight: "8rem",
            }}
          >
            <div>
              <h2 style={{ fontFamily: "var(--font-serif)", fontSize: "var(--text-lg)", fontWeight: 600 }}>
                {project.name}
              </h2>
              <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)", marginTop: "var(--space-1)" }}>
                {statusLabel(project.status)} · {domainLabel(project.ref.domain)} · 版本 {project.ref.version}
              </p>
            </div>
            <div style={{ marginTop: "auto" }}>
              <ButtonLink
                href={`/projects/${project.ref.object_id}`}
                ariaLabel={`打开项目 ${project.name}`}
              >
                打开项目
              </ButtonLink>
            </div>
          </article>
        </li>
      ))}
    </ul>
  );
}

export default function ProjectsPageClient() {
  const router = useRouter();
  const [active, setActive] = useState<ProjectSummary[]>([]);
  const [archived, setArchived] = useState<ProjectSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isCreating, setIsCreating] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [newProjectName, setNewProjectName] = useState("");

  const loadProjects = useCallback(async () => {
    try {
      const data = await listProjects();
      setActive(data.active ?? []);
      setArchived(data.archived ?? []);
      setErrors([]);
    } catch (err) {
      setErrors([err instanceof Error ? err.message : "加载项目列表失败"]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!newProjectName.trim()) {
      setErrors(["请输入项目名称。"]);
      return;
    }
    setIsCreating(true);
    setErrors([]);
    try {
      const project: Project = await createProject({ name: newProjectName.trim() });
      router.push(`/projects/${project.id}`);
    } catch (err) {
      setErrors([err instanceof Error ? err.message : "创建项目失败"]);
      setIsCreating(false);
    }
  };

  return (
    <MainContent>
      <section className="sc-card" aria-labelledby="projects-title">
        <h1 id="projects-title" className="sc-section-title">
          学习项目
        </h1>
        <p style={{ color: "var(--color-text-secondary)", maxWidth: "60ch" }}>
          每个学习项目都有明确的所有者、对象域和版本。未选择项目时，不能创建无归属产物。
        </p>
      </section>

      <section aria-labelledby="create-project-title">
        <h2 id="create-project-title" className="sc-section-title">
          创建新项目
        </h2>
        <form
          onSubmit={handleCreate}
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-3)",
            alignItems: "flex-end",
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)", flex: "1 1 16rem" }}>
            <label htmlFor="project-name" style={{ fontSize: "var(--text-sm)", fontWeight: 500 }}>
              项目名称
            </label>
            <input
              id="project-name"
              type="text"
              value={newProjectName}
              onChange={(e) => setNewProjectName(e.target.value)}
              placeholder="例如：量子纠缠科普"
              maxLength={200}
              required
              style={{
                padding: "0.625rem 0.75rem",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                fontSize: "var(--text-base)",
                minHeight: "var(--target-size)",
              }}
            />
          </div>
          <Button type="submit" isLoading={isCreating}>
            创建项目
          </Button>
        </form>
        {errors.length > 0 && (
          <div style={{ marginTop: "var(--space-4)" }}>
            <ErrorSummary errors={errors} />
          </div>
        )}
      </section>

      <section aria-labelledby="active-projects-title">
        <h2 id="active-projects-title" className="sc-section-title">
          活跃项目
        </h2>
        {isLoading ? (
          <LoadingStatus message="正在加载项目列表…" />
        ) : (
          <ProjectList projects={active} emptyMessage="暂无活跃项目。创建一个新项目开始科学学习与表达。" />
        )}
      </section>

      {archived.length > 0 && (
        <section aria-labelledby="archived-projects-title">
          <h2 id="archived-projects-title" className="sc-section-title">
            已归档项目
          </h2>
          <ProjectList projects={archived} emptyMessage="" />
        </section>
      )}
    </MainContent>
  );
}
