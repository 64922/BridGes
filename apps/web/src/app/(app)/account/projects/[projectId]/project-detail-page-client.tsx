"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { IngestionStatusChip } from "@/components/bridges/AttachmentIngestion";
import { Dialog } from "@/components/bridges/Dialog";
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
import { RemoveFromProjectDialog } from "@/components/learning-projects/RemoveFromProjectDialog";
import {
  ApiError,
  classifyApiError,
  createChatConversation,
  deleteLearningProject,
  deleteLearningProjectFile,
  downloadLearningProjectFile,
  getLearningProject,
  getLearningProjectMigration,
  listLearningProjectFiles,
  retryLearningProjectMigration,
  updateLearningProject,
  uploadLearningProjectFile,
  type LearningProjectConversation,
  type LearningProjectDetail,
  type LearningProjectFile,
  type LearningProjectMigrationSummary,
} from "@/lib/api";
import { LEARNING_PROJECTS_CHANGED_EVENT, changeConversationLearningProject } from "@/lib/learning-projects";
import { CHAT_LIST_CHANGED_EVENT } from "@/lib/recent-conversations";
import { formatAbsoluteTime, formatRelativeTime, formatSize } from "@/lib/format";

const MAX_FILE_BYTES = 10 * 1024 * 1024;
const ACCEPT_ATTRIBUTE = ".pdf,.docx,.txt,.md,.markdown,.png,.jpg,.jpeg,.gif,.webp";
const SUPPORTED_EXTENSION = /\.(pdf|docx|txt|md|markdown|png|jpe?g|gif|webp)$/i;
const POLL_INTERVAL_MS = 2500;

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function newId(prefix: string): string {
  return `${prefix}-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`;
}

function conversationModeLabel(mode: LearningProjectConversation["mode"]): string {
  return mode === "study" ? "学习模式" : "日常陪伴";
}

interface PendingUpload {
  id: string;
  filename: string;
  progress: number;
  status: "uploading" | "error";
  error?: string;
}

type DialogState =
  | { kind: "rename" }
  | { kind: "delete" }
  | { kind: "removeConversation"; conversation: LearningProjectConversation }
  | { kind: "removeFile"; file: LearningProjectFile }
  | null;

type LoadFailure = { kind: "error" | "permission" | "notfound"; message: string } | null;

/**
 * 学习项目详情页（Issue 19）。
 *
 * 仅包含三个区块：项目头部（改名/删除）、相关对话（移出项目）、项目文件
 * （上传/轮询/下载/移除）。项目文件仅归属于该学习项目，与全局知识库材料
 * （对所有对话生效）通过页内说明文案区分。
 */
export default function ProjectDetailPageClient() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;
  const router = useRouter();

  const [detail, setDetail] = useState<LearningProjectDetail | null>(null);
  const [migration, setMigration] = useState<LearningProjectMigrationSummary | null>(null);
  const [migrationLoaded, setMigrationLoaded] = useState(false);
  const [files, setFiles] = useState<LearningProjectFile[] | null>(null);
  const [loadFailure, setLoadFailure] = useState<LoadFailure>(null);
  const [filesError, setFilesError] = useState("");
  const [pendingUploads, setPendingUploads] = useState<PendingUpload[]>([]);
  const [actionError, setActionError] = useState("");
  const [dialog, setDialog] = useState<DialogState>(null);
  const [dialogError, setDialogError] = useState("");
  const [busy, setBusy] = useState(false);
  const [creatingChat, setCreatingChat] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadControllersRef = useRef(new Map<string, AbortController>());

  const reloadDetail = useCallback(async () => {
    try {
      const projection = await getLearningProject(projectId);
      setDetail(projection);
      setLoadFailure(null);
      return true;
    } catch (error) {
      const message = errorMessage(error, "学习项目加载失败，请稍后重试。");
      if (error instanceof ApiError && error.status === 404) {
        setLoadFailure({ kind: "notfound", message: "该学习项目不存在或已被删除。" });
      } else {
        const kind = classifyApiError(error) === "other" ? "error" : "permission";
        setLoadFailure({ kind, message });
      }
      return false;
    }
  }, [projectId]);

  const reloadFiles = useCallback(
    async (silent: boolean) => {
      try {
        const list = await listLearningProjectFiles(projectId);
        setFiles(list);
        setFilesError("");
      } catch (error) {
        if (silent) return; // 轮询失败保留现有列表，下一轮继续
        setFilesError(errorMessage(error, "项目文件加载失败，请稍后重试。"));
        setFiles((current) => current ?? []);
      }
    },
    [projectId]
  );

  const reloadMigration = useCallback(async () => {
    try {
      setMigration(await getLearningProjectMigration());
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        setMigration(null);
      }
    } finally {
      setMigrationLoaded(true);
    }
  }, []);

  useEffect(() => {
    setDetail(null);
    setFiles(null);
    setMigration(null);
    setMigrationLoaded(false);
    setLoadFailure(null);
    void (async () => {
      const ok = await reloadDetail();
      if (ok) await reloadFiles(false);
    })();
    void reloadMigration();
  }, [reloadDetail, reloadFiles, reloadMigration]);

  // 文件处于等待/解析/恢复中时轮询，稳定后自动停止；卸载时清理计时器。
  const needsPolling = useMemo(
    () =>
      (files ?? []).some((file) =>
        ["queued", "processing", "recovery"].includes(file.status)
      ),
    [files]
  );

  useEffect(() => {
    if (!needsPolling) return;
    const timer = window.setTimeout(() => void reloadFiles(true), POLL_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [needsPolling, files, reloadFiles]);

  useEffect(() => {
    const controllers = uploadControllersRef.current;
    return () => {
      controllers.forEach((controller) => controller.abort());
      controllers.clear();
    };
  }, []);

  // -------------------------------------------------------------------------
  // 项目级操作
  // -------------------------------------------------------------------------

  const notifyProjectsChanged = () => {
    window.dispatchEvent(new Event(LEARNING_PROJECTS_CHANGED_EVENT));
  };

  const openDialog = (next: NonNullable<DialogState>) => {
    setDialogError("");
    setDialog(next);
  };

  const doRename = async (values: { name: string; description: string }) => {
    setBusy(true);
    setDialogError("");
    try {
      await updateLearningProject(projectId, {
        name: values.name,
        description: values.description || null,
      });
      setDialog(null);
      notifyProjectsChanged();
      await reloadDetail();
    } catch (error) {
      setDialogError(errorMessage(error, "保存学习项目失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async (contents: ProjectDeleteContents) => {
    setBusy(true);
    setDialogError("");
    try {
      await deleteLearningProject(projectId, contents);
      setDialog(null);
      notifyProjectsChanged();
      window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      router.push("/account/projects");
    } catch (error) {
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

  const createStudyChat = async () => {
    if (creatingChat) return;
    setCreatingChat(true);
    setActionError("");
    try {
      const conversation = await createChatConversation(undefined, "study", projectId);
      window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      router.push(`/chat/${conversation.conversation_id}`);
    } catch (error) {
      setActionError(errorMessage(error, "创建学习对话失败，请稍后重试。"));
      setCreatingChat(false);
    }
  };

  // -------------------------------------------------------------------------
  // 相关对话
  // -------------------------------------------------------------------------

  const removeConversation = async (conversation: LearningProjectConversation) => {
    setBusy(true);
    setDialogError("");
    try {
      await changeConversationLearningProject(conversation.conversation_id, null);
      setDialog(null);
      await reloadDetail();
    } catch (error) {
      setDialogError(errorMessage(error, "移出学习项目失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  };

  // -------------------------------------------------------------------------
  // 项目文件
  // -------------------------------------------------------------------------

  const updatePendingUpload = (id: string, update: Partial<PendingUpload>) => {
    setPendingUploads((current) =>
      current.map((entry) => (entry.id === id ? { ...entry, ...update } : entry))
    );
  };

  const removePendingUpload = (id: string) => {
    uploadControllersRef.current.get(id)?.abort();
    uploadControllersRef.current.delete(id);
    setPendingUploads((current) => current.filter((entry) => entry.id !== id));
  };

  const startUpload = async (entry: PendingUpload, file: File) => {
    const controller = new AbortController();
    uploadControllersRef.current.set(entry.id, controller);
    try {
      await uploadLearningProjectFile(
        projectId,
        file,
        entry.id,
        (loaded, total) =>
          updatePendingUpload(entry.id, {
            progress: total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0,
          }),
        controller.signal
      );
      uploadControllersRef.current.delete(entry.id);
      setPendingUploads((current) => current.filter((item) => item.id !== entry.id));
      void reloadFiles(true);
    } catch (error) {
      uploadControllersRef.current.delete(entry.id);
      if (error instanceof DOMException && error.name === "AbortError") {
        setPendingUploads((current) => current.filter((item) => item.id !== entry.id));
        return;
      }
      updatePendingUpload(entry.id, {
        status: "error",
        error: errorMessage(error, "上传失败，请重试。"),
      });
    }
  };

  const onFilesSelected = (filesToUpload: FileList | null) => {
    if (!filesToUpload || filesToUpload.length === 0) return;
    setActionError("");
    const entries: { entry: PendingUpload; file: File | null }[] = Array.from(filesToUpload).map(
      (file) => {
        const id = newId("project-upload");
        if (!SUPPORTED_EXTENSION.test(file.name)) {
          return {
            entry: {
              id,
              filename: file.name,
              progress: 0,
              status: "error" as const,
              error: "不支持的文件类型：仅支持 PDF、DOCX、TXT、Markdown 与 PNG/JPEG/GIF/WebP 图片。",
            },
            file: null,
          };
        }
        if (file.size === 0 || file.size > MAX_FILE_BYTES) {
          return {
            entry: {
              id,
              filename: file.name,
              progress: 0,
              status: "error" as const,
              error:
                file.size === 0 ? "文件为空，无法上传。" : "文件超过 10 MB 大小限制，请压缩后重试。",
            },
            file: null,
          };
        }
        return { entry: { id, filename: file.name, progress: 0, status: "uploading" as const }, file };
      }
    );
    setPendingUploads((current) => [...current, ...entries.map((item) => item.entry)]);
    entries.forEach(({ entry, file }) => {
      if (file) void startUpload(entry, file);
    });
  };

  const doDownload = async (file: LearningProjectFile) => {
    setActionError("");
    try {
      await downloadLearningProjectFile(projectId, file.object_id, file.filename);
    } catch (error) {
      setActionError(errorMessage(error, "下载失败，请稍后重试。"));
    }
  };

  const doRemoveFile = async (file: LearningProjectFile) => {
    setBusy(true);
    setDialogError("");
    try {
      await deleteLearningProjectFile(projectId, file.object_id);
      setDialog(null);
      await reloadFiles(true);
    } catch (error) {
      // 409 material_processing 等可恢复错误：对话框内展示中文原因，文件保留在列表中
      setDialogError(errorMessage(error, "移除失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  };

  // -------------------------------------------------------------------------
  // 渲染
  // -------------------------------------------------------------------------

  const fileRowMenuItems = (file: LearningProjectFile): MenuItem[] => [
    { label: "下载", icon: "download", onSelect: () => void doDownload(file) },
    {
      label: "移除",
      icon: "trash",
      danger: true,
      returnFocus: false,
      onSelect: () => openDialog({ kind: "removeFile", file }),
    },
  ];

  if ((detail === null || !migrationLoaded) && !loadFailure) {
    return (
      <MainContent>
        <StateBlock kind="loading" title="正在加载学习项目" description="读取项目详情、对话与文件。" />
      </MainContent>
    );
  }

  if (loadFailure?.kind === "notfound") {
    return (
      <MainContent>
        <StateBlock
          kind="empty"
          title="学习项目不存在"
          description="该学习项目不存在或已被删除，可能已在其他页面被移除。"
          actionLabel="返回学习项目列表"
          actionHref="/account/projects"
        />
      </MainContent>
    );
  }

  if (loadFailure && detail === null) {
    return (
      <MainContent>
        <StateBlock
          kind={loadFailure.kind === "permission" ? "permission" : "error"}
          title={loadFailure.kind === "permission" ? "暂时无法访问学习项目" : "学习项目加载失败"}
          description={
            loadFailure.kind === "permission"
              ? `${loadFailure.message} 请重新登录后再试。`
              : loadFailure.message
          }
          actionLabel="重试"
          onAction={() => void reloadDetail()}
        />
      </MainContent>
    );
  }

  if (!detail) {
    return (
      <MainContent>
        <StateBlock
          kind="error"
          title="学习项目加载失败"
          description="请稍后重试。"
          actionLabel="重试"
          onAction={() => void reloadDetail()}
        />
      </MainContent>
    );
  }

  if (migration) {
    const migrationItems = (migration.items ?? []).filter(
      (item) => item.project_id === projectId
    );
    const migrationConversations = (migration.conversations ?? []).filter(
      (conversation) => conversation.project_id === projectId
    );
    const statusLabel =
      migration.status === "completed"
        ? "已完成"
        : migration.status === "failed"
          ? "存在失败项"
          : "处理中";
    return (
      <MainContent>
        <section
          data-testid="learning-project-migration-view"
          aria-labelledby="learning-project-migration-title"
          style={{ maxWidth: "52rem", marginInline: "auto", padding: "0 var(--space-4)" }}
        >
          <p style={{ marginBottom: "var(--space-3)" }}>
            <Link href="/account/projects" style={{ color: "var(--color-text-secondary)" }}>
              返回学习项目列表
            </Link>
          </p>
          <h1 id="learning-project-migration-title" className="sc-section-title">
            {detail.name}：资料迁移状态
          </h1>
          <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
            旧项目已进入只读兼容窗口。资料会迁移到你的全局知识库，原文、对话和失败原因均保留可审计记录。
          </p>
          <div
            style={{
              marginTop: "var(--space-4)",
              padding: "var(--space-4)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-surface)",
            }}
          >
            <p style={{ margin: 0, fontWeight: 600 }}>迁移{statusLabel}</p>
            <p style={{ margin: "var(--space-2) 0 0", color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
              已完成 {migration.completed_files}/{migration.total_files} 个文件，失败 {migration.failed_files} 个；历史对话 {migration.detached_conversation_count} 个。
            </p>
            {migration.failed_files > 0 && (
              <Button
                variant="secondary"
                size="sm"
                onClick={async () => {
                  const next = await retryLearningProjectMigration();
                  setMigration(next);
                }}
                style={{ marginTop: "var(--space-3)" }}
              >
                重试失败项
              </Button>
            )}
          </div>
          <p style={{ marginTop: "var(--space-4)" }}>
            <Link href="/knowledge-base" style={{ color: "var(--color-accent-primary)" }}>
              打开全局知识库
            </Link>
          </p>
          <h2 style={{ marginTop: "var(--space-8)", fontSize: "var(--text-base)" }}>文件迁移明细</h2>
          <ul role="list" style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
            {migrationItems.map((item) => (
              <li
                key={item.migration_id}
                style={{
                  padding: "var(--space-3) var(--space-4)",
                  border: "1px solid var(--color-border)",
                  borderRadius: "var(--radius-md)",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-3)" }}>
                  <span>{item.filename}</span>
                  <span style={{ color: item.status === "failed" ? "var(--color-status-error)" : "var(--color-text-secondary)" }}>
                    {item.status}
                  </span>
                </div>
                {item.failure_reason && (
                  <p role="alert" style={{ margin: "var(--space-2) 0 0", color: "var(--color-status-error)", fontSize: "var(--text-sm)" }}>
                    {item.failure_reason}
                  </p>
                )}
              </li>
            ))}
          </ul>
          {migrationConversations.length > 0 && (
            <>
              <h2 style={{ marginTop: "var(--space-8)", fontSize: "var(--text-base)" }}>历史对话</h2>
              <ul role="list">
                {migrationConversations.map((conversation) => (
                  <li key={conversation.conversation_id}>
                    <Link href={`/chat/${conversation.conversation_id}`}>
                      {conversation.project_name} · {conversation.conversation_id}
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      </MainContent>
    );
  }

  const conversations = detail.conversations ?? [];

  return (
    <MainContent>
      <section
        aria-labelledby="learning-project-title"
        style={{ maxWidth: "52rem", marginInline: "auto", padding: "0 var(--space-4)" }}
      >
        <p style={{ marginBottom: "var(--space-3)" }}>
          <Link
            href="/account/projects"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "var(--space-1)",
              minHeight: "var(--target-size)",
              color: "var(--color-text-secondary)",
              fontSize: "var(--text-sm)",
              textDecoration: "none",
            }}
          >
            返回学习项目列表
          </Link>
        </p>

        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: "var(--space-4)",
            flexWrap: "wrap",
          }}
        >
          <div style={{ display: "flex", alignItems: "flex-start", gap: "var(--space-3)", minWidth: 0 }}>
            <span
              style={{
                color: "var(--color-text-tertiary)",
                flexShrink: 0,
                display: "inline-flex",
                marginTop: "var(--space-1)",
              }}
            >
              <Icon name="learningProject" size={28} aria-hidden />
            </span>
            <div style={{ minWidth: 0 }}>
              <h1 id="learning-project-title" className="sc-section-title">
                {detail.name}
              </h1>
              {detail.description ? (
                <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
                  {detail.description}
                </p>
              ) : (
                <p style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
                  还没有项目说明，可通过「改名」补充。
                </p>
              )}
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
            <Button
              data-testid="learning-project-new-chat"
              onClick={() => void createStudyChat()}
              isLoading={creatingChat}
            >
              <Icon name="newChat" size={18} aria-hidden />
              新建学习对话
            </Button>
            <Menu
              trigger={<Icon name="more" size={20} aria-hidden />}
              ariaLabel={`项目操作：${detail.name}`}
              items={[
                {
                  label: "改名",
                  icon: "edit",
                  returnFocus: false,
                  onSelect: () => openDialog({ kind: "rename" }),
                },
                {
                  label: "删除",
                  icon: "trash",
                  danger: true,
                  returnFocus: false,
                  onSelect: () => openDialog({ kind: "delete" }),
                },
              ]}
            />
          </div>
        </div>

        {actionError && (
          <p
            role="alert"
            style={{
              marginTop: "var(--space-4)",
              padding: "var(--space-3) var(--space-4)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-status-error)",
              backgroundColor: "var(--color-status-error-bg)",
              color: "var(--color-status-error)",
              fontSize: "var(--text-sm)",
            }}
          >
            {actionError}
          </p>
        )}

        {/* 相关对话 */}
        <section aria-labelledby="project-conversations-title" style={{ marginTop: "var(--space-8)" }}>
          <h2
            id="project-conversations-title"
            style={{
              fontFamily: "var(--font-sans)",
              fontSize: "var(--text-base)",
              fontWeight: 600,
              color: "var(--color-text-primary)",
              marginBottom: "var(--space-3)",
            }}
          >
            相关对话
          </h2>
          {conversations.length === 0 ? (
            <p style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
              还没有关联的对话，点击上方「新建学习对话」开始。
            </p>
          ) : (
            <ul role="list" style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
              {conversations.map((conversation) => {
                const title = conversation.title || "未命名对话";
                return (
                  <li
                    key={conversation.conversation_id}
                    data-testid={`project-conversation-row-${conversation.conversation_id}`}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-2)",
                      borderRadius: "var(--radius-md)",
                      border: "1px solid var(--color-border)",
                      backgroundColor: "var(--color-surface)",
                    }}
                  >
                    <Link
                      href={`/chat/${conversation.conversation_id}`}
                      aria-label={`打开对话 ${title}`}
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
                          {title}
                        </span>
                        <span
                          style={{
                            display: "block",
                            marginTop: "2px",
                            fontSize: "var(--text-xs)",
                            color: "var(--color-text-tertiary)",
                          }}
                        >
                          {conversationModeLabel(conversation.mode)} ·{" "}
                          <span title={formatAbsoluteTime(conversation.updated_at)}>
                            更新于 {formatRelativeTime(conversation.updated_at)}
                          </span>
                        </span>
                      </span>
                    </Link>
                    <div style={{ flexShrink: 0, paddingRight: "var(--space-2)" }}>
                      <Menu
                        trigger={<Icon name="more" size={20} aria-hidden />}
                        ariaLabel={`对话操作：${title}`}
                        items={[
                          {
                            label: "移出学习项目",
                            icon: "close",
                            returnFocus: false,
                            onSelect: () => openDialog({ kind: "removeConversation", conversation }),
                          },
                        ]}
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* 项目文件 */}
        <section aria-labelledby="project-files-title" style={{ marginTop: "var(--space-8)" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "var(--space-3)",
              flexWrap: "wrap",
              marginBottom: "var(--space-2)",
            }}
          >
            <h2
              id="project-files-title"
              style={{
                fontFamily: "var(--font-sans)",
                fontSize: "var(--text-base)",
                fontWeight: 600,
                color: "var(--color-text-primary)",
              }}
            >
              项目文件
            </h2>
            <Button
              variant="secondary"
              size="sm"
              data-testid="project-file-upload-button"
              onClick={() => fileInputRef.current?.click()}
            >
              <Icon name="uploadFile" size={16} aria-hidden />
              上传文件
            </Button>
          </div>
          <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
            项目文件仅归属于该学习项目，仅本项目的对话可使用；
            <Link
              href="/knowledge-base"
              style={{ color: "var(--color-accent-primary)", textDecoration: "underline" }}
            >
              本地知识库
            </Link>
            的全局材料对你所有对话生效。
          </p>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPT_ATTRIBUTE}
            data-testid="project-file-input"
            aria-label="选择要上传的项目文件"
            style={{ display: "none" }}
            onChange={(event) => {
              onFilesSelected(event.target.files);
              event.target.value = "";
            }}
          />

          {pendingUploads.length > 0 && (
            <ul
              role="list"
              aria-label="正在上传的项目文件"
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-2)",
                marginTop: "var(--space-3)",
              }}
            >
              {pendingUploads.map((entry) => (
                <li
                  key={entry.id}
                  data-testid="project-upload-entry"
                  style={{
                    padding: "var(--space-3) var(--space-4)",
                    borderRadius: "var(--radius-md)",
                    border: `1px solid ${entry.status === "error" ? "var(--color-status-error)" : "var(--color-border)"}`,
                    backgroundColor: "var(--color-surface)",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
                    <span
                      style={{
                        minWidth: 0,
                        flex: 1,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        fontWeight: 500,
                      }}
                      title={entry.filename}
                    >
                      {entry.filename}
                    </span>
                    {entry.status === "uploading" ? (
                      <>
                        <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                          {entry.progress}%
                        </span>
                        <Button variant="ghost" size="sm" onClick={() => removePendingUpload(entry.id)}>
                          取消
                        </Button>
                      </>
                    ) : (
                      <button
                        type="button"
                        aria-label={`清除上传错误：${entry.filename}`}
                        onClick={() => removePendingUpload(entry.id)}
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          justifyContent: "center",
                          minWidth: "var(--target-size)",
                          minHeight: "var(--target-size)",
                          border: "none",
                          backgroundColor: "transparent",
                          color: "var(--color-text-tertiary)",
                          cursor: "pointer",
                        }}
                      >
                        <Icon name="cross" size={16} aria-hidden />
                      </button>
                    )}
                  </div>
                  {entry.status === "uploading" ? (
                    <div
                      role="progressbar"
                      aria-valuenow={entry.progress}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-label={`上传进度：${entry.filename}`}
                      style={{
                        marginTop: "var(--space-2)",
                        height: "0.375rem",
                        borderRadius: "999px",
                        backgroundColor: "var(--color-border)",
                        overflow: "hidden",
                      }}
                    >
                      <div
                        style={{
                          width: `${entry.progress}%`,
                          height: "100%",
                          backgroundColor: "var(--color-accent-primary)",
                          transition: "width var(--motion-duration-base) var(--motion-easing)",
                        }}
                      />
                    </div>
                  ) : (
                    <p role="alert" style={{ marginTop: "var(--space-1)", fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
                      {entry.error}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}

          <div style={{ marginTop: "var(--space-3)" }}>
            {files === null && !filesError ? (
              <StateBlock kind="loading" title="正在加载项目文件" description="读取该项目的文件列表。" />
            ) : filesError ? (
              <div role="alert" style={{ fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
                {filesError}
                <Button variant="ghost" size="sm" onClick={() => void reloadFiles(false)}>
                  重试
                </Button>
              </div>
            ) : (files ?? []).length === 0 ? (
              <p style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
                还没有项目文件，上传后仅归属于该学习项目。
              </p>
            ) : (
              <ul role="list" style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                {(files ?? []).map((file) => (
                  <li
                    key={file.object_id}
                    data-testid="project-file-row"
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-3)",
                      padding: "var(--space-3) var(--space-4)",
                      borderRadius: "var(--radius-md)",
                      border: "1px solid var(--color-border)",
                      backgroundColor: "var(--color-surface)",
                    }}
                  >
                    <span style={{ color: "var(--color-text-tertiary)", flexShrink: 0, display: "inline-flex" }}>
                      <Icon
                        name={file.media_type.startsWith("image/") ? "uploadImage" : "uploadFile"}
                        size={22}
                        aria-hidden
                      />
                    </span>
                    <div style={{ minWidth: 0, flex: 1, display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", minWidth: 0 }}>
                        <span
                          title={file.filename}
                          style={{
                            minWidth: 0,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                            fontWeight: 500,
                            color: "var(--color-text-primary)",
                          }}
                        >
                          {file.filename}
                        </span>
                        <IngestionStatusChip status={file.status} />
                      </div>
                      <div
                        style={{
                          display: "flex",
                          flexWrap: "wrap",
                          alignItems: "center",
                          columnGap: "var(--space-3)",
                          rowGap: "var(--space-1)",
                          fontSize: "var(--text-xs)",
                          color: "var(--color-text-tertiary)",
                        }}
                      >
                        <span>{formatSize(file.content_length)}</span>
                        <span title={formatAbsoluteTime(file.created_at)}>
                          上传于 {formatRelativeTime(file.created_at)}
                        </span>
                        {file.status === "error" && (
                          <span role="alert" style={{ color: "var(--color-status-error)" }}>
                            处理失败{file.error ? `：${file.error}` : "，可移除后重新上传。"}
                          </span>
                        )}
                      </div>
                    </div>
                    <div style={{ flexShrink: 0 }}>
                      <Menu
                        trigger={<Icon name="more" size={20} aria-hidden />}
                        ariaLabel={`文件操作：${file.filename}`}
                        items={fileRowMenuItems(file)}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </section>

      {dialog?.kind === "rename" && (
        <LearningProjectFormDialog
          mode="rename"
          initialName={detail.name}
          initialDescription={detail.description}
          busy={busy}
          error={dialogError}
          onSubmit={(values) => void doRename(values)}
          onClose={() => setDialog(null)}
        />
      )}

      {dialog?.kind === "delete" && (
        <LearningProjectDeleteDialog
          projectName={detail.name}
          busy={busy}
          error={dialogError}
          onConfirm={(contents) => void doDelete(contents)}
          onClose={() => setDialog(null)}
        />
      )}

      {dialog?.kind === "removeConversation" && (
        <RemoveFromProjectDialog
          conversationTitle={dialog.conversation.title || "未命名对话"}
          busy={busy}
          error={dialogError}
          onConfirm={() => void removeConversation(dialog.conversation)}
          onClose={() => setDialog(null)}
        />
      )}

      {dialog?.kind === "removeFile" && (
        <Dialog
          open
          onClose={() => {
            if (!busy) setDialog(null);
          }}
          title="移除项目文件？"
          description={`将从本学习项目移除并删除「${dialog.file.filename}」；此操作与任何对话无关，对话历史不受影响。`}
        >
          {dialogError && (
            <p role="alert" style={{ fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
              {dialogError}
            </p>
          )}
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              gap: "var(--space-2)",
              marginTop: "var(--space-4)",
            }}
          >
            <Button variant="ghost" size="sm" onClick={() => setDialog(null)} disabled={busy}>
              取消
            </Button>
            <Button
              variant="danger"
              size="sm"
              isLoading={busy}
              onClick={() => void doRemoveFile(dialog.file)}
            >
              确认移除
            </Button>
          </div>
        </Dialog>
      )}
    </MainContent>
  );
}
