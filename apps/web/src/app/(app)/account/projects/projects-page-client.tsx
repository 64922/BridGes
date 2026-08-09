"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { StateBlock } from "@/components/bridges/StateBlock";
import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { MainContent } from "@/components/layout/MainContent";
import {
  classifyApiError,
  listLearningProjects,
  type LearningProjectSummary,
} from "@/lib/api";
import { formatAbsoluteTime, formatRelativeTime } from "@/lib/format";

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

/**
 * 学习项目列表页（Issue 19，文件夹式学习项目）。
 *
 * ChatGPT Projects 式桌面列表：文件夹图标 + 名称 + 计数/更新时间元信息，
 * 整行可点击/键盘聚焦进入详情页；写操作已退役，历史数据只读展示。
 */
export default function ProjectsPageClient() {
  const [projects, setProjects] = useState<LearningProjectSummary[] | null>(null);
  const [loadError, setLoadError] = useState<{ kind: "error" | "permission"; message: string } | null>(null);

  const reload = useCallback(async () => {
    try {
      const list = await listLearningProjects();
      setProjects(list);
      setLoadError(null);
    } catch (error) {
      const message = errorMessage(error, "学习项目列表加载失败，请稍后重试。");
      const kind = classifyApiError(error) === "other" ? "error" : "permission";
      setLoadError({ kind, message });
      // 加载失败时不覆盖已有列表；首次失败保持 projects 为 null 以呈现错误态而非空态
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <MainContent>
      <section
        aria-labelledby="learning-projects-title"
        style={{ maxWidth: "52rem", marginInline: "auto", padding: "0 var(--space-4)" }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: "var(--space-4)",
            flexWrap: "wrap",
          }}
        >
          <div>
            <h1 id="learning-projects-title" className="sc-section-title">
              学习项目
            </h1>
            <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
              历史项目与文件仅保留只读查看；新的文件请统一进入全局知识库。
            </p>
          </div>
        </div>

        <div style={{ marginTop: "var(--space-6)" }}>
          {projects === null && !loadError ? (
            <StateBlock kind="loading" title="正在加载学习项目" description="读取当前账户的项目列表。" />
          ) : loadError && projects === null ? (
            <StateBlock
              kind={loadError.kind}
              title={loadError.kind === "permission" ? "暂时无法访问学习项目" : "学习项目加载失败"}
              description={
                loadError.kind === "permission"
                  ? `${loadError.message} 请重新登录后再试。`
                  : loadError.message
              }
              actionLabel="重试"
              onAction={() => void reload()}
            />
          ) : (projects ?? []).length === 0 ? (
            <div>
              <StateBlock
                kind="empty"
                title="还没有学习项目"
                description="学习项目已退役；历史项目仅保留只读查看与迁移记录。"
              />
            </div>
          ) : (
            <>
              {loadError && (
                <p
                  role="alert"
                  style={{
                    marginBottom: "var(--space-3)",
                    padding: "var(--space-3) var(--space-4)",
                    borderRadius: "var(--radius-md)",
                    border: "1px solid var(--color-status-error)",
                    backgroundColor: "var(--color-status-error-bg)",
                    color: "var(--color-status-error)",
                    fontSize: "var(--text-sm)",
                  }}
                >
                  {loadError.message}
                  <Button variant="ghost" size="sm" onClick={() => void reload()}>
                    重试
                  </Button>
                </p>
              )}
              <ul role="list" style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                {(projects ?? []).map((project) => (
                  <li
                    key={project.project_id}
                    data-testid={`learning-project-row-${project.project_id}`}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-2)",
                      borderRadius: "var(--radius-md)",
                      border: "1px solid var(--color-border)",
                      backgroundColor: "var(--color-surface)",
                      transition:
                        "background-color var(--motion-duration-fast) var(--motion-easing)",
                    }}
                  >
                    <Link
                      href={`/account/projects/${project.project_id}`}
                      aria-label={`打开学习项目 ${project.name}`}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "var(--space-3)",
                        flex: 1,
                        minWidth: 0,
                        minHeight: "var(--target-size)",
                        padding: "var(--space-3) var(--space-4)",
                        textDecoration: "none",
                        borderRadius: "var(--radius-md)",
                      }}
                    >
                      <span
                        style={{ color: "var(--color-text-tertiary)", flexShrink: 0, display: "inline-flex" }}
                      >
                        <Icon name="learningProject" size={22} aria-hidden />
                      </span>
                      <span style={{ minWidth: 0, flex: 1 }}>
                        <span
                          style={{
                            display: "block",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                            fontWeight: 500,
                            color: "var(--color-text-primary)",
                          }}
                        >
                          {project.name}
                        </span>
                        <span
                          style={{
                            display: "block",
                            marginTop: "2px",
                            fontSize: "var(--text-xs)",
                            color: "var(--color-text-tertiary)",
                          }}
                        >
                          {project.conversation_count} 个对话 · {project.file_count} 个文件 ·{" "}
                          <span title={formatAbsoluteTime(project.updated_at)}>
                            更新于 {formatRelativeTime(project.updated_at)}
                          </span>
                        </span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </section>

    </MainContent>
  );
}
