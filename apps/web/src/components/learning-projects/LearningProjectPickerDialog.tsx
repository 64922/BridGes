"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { StateBlock } from "@/components/bridges/StateBlock";
import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { classifyApiError, listLearningProjects, type LearningProjectSummary } from "@/lib/api";

interface LearningProjectPickerDialogProps {
  /** 当前已选中的项目（提供时展示「清除选择」入口）。 */
  selectedProjectId?: string | null;
  /** 选择某个项目；传 null 表示清除选择。 */
  onSelect: (project: LearningProjectSummary | null) => void;
  onClose: () => void;
}

/**
 * 学习项目选择对话框（Issue 19）。
 *
 * 打开时拉取当前账户的项目列表（名称 + 对话/文件计数），整行可键盘
 * 聚焦（Tab）并按 Enter 选择；已选中项目时提供「清除选择」。
 * 加载 / 空 / 错误三态齐全，失败绝不呈现为空列表。
 */
export function LearningProjectPickerDialog({
  selectedProjectId = null,
  onSelect,
  onClose,
}: LearningProjectPickerDialogProps) {
  const [projects, setProjects] = useState<LearningProjectSummary[] | null>(null);
  const [loadError, setLoadError] = useState<{ kind: "error" | "permission"; message: string } | null>(null);

  const load = () => {
    setProjects(null);
    setLoadError(null);
    let cancelled = false;
    listLearningProjects()
      .then((list) => {
        if (!cancelled) setProjects(list);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          // 401/会话类失败归为权限态（与学习项目页同一约定），不混同普通加载错误
          setLoadError({
            kind: classifyApiError(error) === "other" ? "error" : "permission",
            message: error instanceof Error ? error.message : "学习项目列表加载失败。",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  };

  useEffect(() => load(), []);

  return (
    <Dialog
      open
      onClose={onClose}
      title="选择学习项目"
      description="选择后，对话将归属到该学习项目。"
    >
      {selectedProjectId && (
        <div style={{ marginBottom: "var(--space-3)" }}>
          <Button variant="secondary" size="sm" onClick={() => onSelect(null)}>
            <Icon name="close" size={16} aria-hidden />
            清除选择
          </Button>
        </div>
      )}
      {projects === null && !loadError ? (
        <StateBlock kind="loading" title="正在加载学习项目" description="读取当前账户的项目列表。" />
      ) : loadError ? (
        <StateBlock
          kind={loadError.kind}
          title={loadError.kind === "permission" ? "暂时无法访问学习项目" : "学习项目加载失败"}
          description={
            loadError.kind === "permission"
              ? `${loadError.message} 请重新登录后再试。`
              : loadError.message
          }
          actionLabel="重试"
          onAction={() => load()}
        />
      ) : (projects ?? []).length === 0 ? (
        <div style={{ fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
          <p>还没有学习项目，可前往学习项目页创建。</p>
          <p style={{ marginTop: "var(--space-2)" }}>
            <Link
              href="/account/projects"
              onClick={onClose}
              style={{ color: "var(--color-accent-primary)", textDecoration: "underline" }}
            >
              前往学习项目页
            </Link>
          </p>
        </div>
      ) : (
        <ul
          role="list"
          aria-label="学习项目列表"
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-1)",
            maxHeight: "18rem",
            overflowY: "auto",
          }}
        >
          {(projects ?? []).map((project) => {
            const selected = project.project_id === selectedProjectId;
            return (
              <li key={project.project_id}>
                <button
                  type="button"
                  onClick={() => onSelect(project)}
                  aria-pressed={selected}
                  data-testid={`learning-project-option-${project.project_id}`}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-3)",
                    width: "100%",
                    minHeight: "var(--target-size)",
                    padding: "var(--space-2) var(--space-3)",
                    border: `1px solid ${selected ? "var(--color-accent-primary)" : "transparent"}`,
                    borderRadius: "var(--radius-md)",
                    backgroundColor: selected ? "var(--color-accent-primary-soft)" : "transparent",
                    color: "var(--color-text-primary)",
                    fontSize: "var(--text-sm)",
                    textAlign: "left",
                    cursor: "pointer",
                    transition:
                      "background-color var(--motion-duration-fast) var(--motion-easing)",
                  }}
                >
                  <span style={{ color: "var(--color-text-tertiary)", flexShrink: 0, display: "inline-flex" }}>
                    <Icon name="learningProject" size={18} aria-hidden />
                  </span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span
                      style={{
                        display: "block",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        fontWeight: 500,
                      }}
                    >
                      {project.name}
                    </span>
                    <span
                      style={{
                        display: "block",
                        fontSize: "var(--text-xs)",
                        color: "var(--color-text-tertiary)",
                      }}
                    >
                      {project.conversation_count} 个对话 · {project.file_count} 个文件
                    </span>
                  </span>
                  {selected && (
                    <span style={{ color: "var(--color-accent-primary)", flexShrink: 0, display: "inline-flex" }}>
                      <Icon name="check" size={16} aria-hidden />
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Dialog>
  );
}
