"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Menu, type MenuItem } from "@/components/bridges/Menu";
import { StateBlock } from "@/components/bridges/StateBlock";
import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { MainContent } from "@/components/layout/MainContent";
import {
  LearningProjectDeleteDialog,
  type ProjectDeleteContents,
} from "@/components/learning-projects/LearningProjectDeleteDialog";
import { LearningProjectFormDialog } from "@/components/learning-projects/LearningProjectFormDialog";
import {
  ApiError,
  classifyApiError,
  createLearningProject,
  deleteLearningProject,
  listLearningProjects,
  updateLearningProject,
  type LearningProjectSummary,
} from "@/lib/api";
import { LEARNING_PROJECTS_CHANGED_EVENT } from "@/lib/learning-projects";
import { CHAT_LIST_CHANGED_EVENT } from "@/lib/recent-conversations";
import { formatAbsoluteTime, formatRelativeTime } from "@/lib/format";

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

type DialogState =
  | { kind: "create" }
  | { kind: "rename"; project: LearningProjectSummary }
  | { kind: "delete"; project: LearningProjectSummary }
  | null;

/**
 * 学习项目列表页（Issue 19，文件夹式学习项目）。
 *
 * ChatGPT Projects 式桌面列表：文件夹图标 + 名称 + 计数/更新时间元信息，
 * 整行可点击/键盘聚焦进入详情页，行尾菜单提供改名与删除。新建、改名、
 * 删除走共享对话框；加载/空/错误/权限状态齐全，失败绝不呈现为空列表。
 */
export default function ProjectsPageClient() {
  const router = useRouter();
  const [projects, setProjects] = useState<LearningProjectSummary[] | null>(null);
  const [loadError, setLoadError] = useState<{ kind: "error" | "permission"; message: string } | null>(null);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [dialogError, setDialogError] = useState("");
  const [busy, setBusy] = useState(false);

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

  const notifyChanged = () => {
    window.dispatchEvent(new Event(LEARNING_PROJECTS_CHANGED_EVENT));
  };

  const doCreate = async (values: { name: string; description: string }) => {
    setBusy(true);
    setDialogError("");
    try {
      const project = await createLearningProject(values.name, values.description || undefined);
      setDialog(null);
      notifyChanged();
      router.push(`/account/projects/${project.project_id}`);
    } catch (error) {
      setDialogError(errorMessage(error, "创建学习项目失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  };

  const doRename = async (
    project: LearningProjectSummary,
    values: { name: string; description: string }
  ) => {
    setBusy(true);
    setDialogError("");
    try {
      await updateLearningProject(project.project_id, {
        name: values.name,
        description: values.description || null,
      });
      setDialog(null);
      notifyChanged();
      await reload();
    } catch (error) {
      setDialogError(errorMessage(error, "保存学习项目失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async (project: LearningProjectSummary, contents: ProjectDeleteContents) => {
    setBusy(true);
    setDialogError("");
    try {
      await deleteLearningProject(project.project_id, contents);
      setDialog(null);
      notifyChanged();
      // 对话归属随删除变化（保留对话解绑 / 一并删除移除），侧栏列表同步刷新
      window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      await reload();
    } catch (error) {
      // 409 generation_in_progress 等可恢复错误：对话框内展示服务端中文原因
      const message = errorMessage(error, "删除学习项目失败，请稍后重试。");
      setDialogError(
        error instanceof ApiError && error.status === 409
          ? `${message} 请先停止正在生成的回答，再回到这里重试删除。`
          : message
      );
    } finally {
      setBusy(false);
    }
  };

  const openDialog = (next: NonNullable<DialogState>) => {
    setDialogError("");
    setDialog(next);
  };

  const rowMenuItems = (project: LearningProjectSummary): MenuItem[] => [
    {
      label: "改名",
      icon: "edit",
      returnFocus: false,
      onSelect: () => openDialog({ kind: "rename", project }),
    },
    {
      label: "删除",
      icon: "trash",
      danger: true,
      returnFocus: false,
      onSelect: () => openDialog({ kind: "delete", project }),
    },
  ];

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
              把相关对话与项目文件组织在一起的文件夹，仅当前账户可见。
            </p>
          </div>
          <Button data-testid="learning-project-create" onClick={() => openDialog({ kind: "create" })}>
            <Icon name="plus" size={18} aria-hidden />
            新建项目
          </Button>
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
                description="创建一个学习项目，把同一主题的对话与项目文件放在一起管理。"
              />
              <div style={{ display: "flex", justifyContent: "center" }}>
                <Button onClick={() => openDialog({ kind: "create" })}>
                  <Icon name="plus" size={18} aria-hidden />
                  新建项目
                </Button>
              </div>
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
                    <div style={{ flexShrink: 0, paddingRight: "var(--space-2)" }}>
                      <Menu
                        trigger={<Icon name="more" size={20} aria-hidden />}
                        ariaLabel={`项目操作：${project.name}`}
                        items={rowMenuItems(project)}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </section>

      {dialog?.kind === "create" && (
        <LearningProjectFormDialog
          mode="create"
          busy={busy}
          error={dialogError}
          onSubmit={(values) => void doCreate(values)}
          onClose={() => setDialog(null)}
        />
      )}

      {dialog?.kind === "rename" && (
        <LearningProjectFormDialog
          mode="rename"
          initialName={dialog.project.name}
          initialDescription={dialog.project.description}
          busy={busy}
          error={dialogError}
          onSubmit={(values) => void doRename(dialog.project, values)}
          onClose={() => setDialog(null)}
        />
      )}

      {dialog?.kind === "delete" && (
        <LearningProjectDeleteDialog
          projectName={dialog.project.name}
          busy={busy}
          error={dialogError}
          onConfirm={(contents) => void doDelete(dialog.project, contents)}
          onClose={() => setDialog(null)}
        />
      )}
    </MainContent>
  );
}
