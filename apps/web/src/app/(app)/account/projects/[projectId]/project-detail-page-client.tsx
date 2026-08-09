"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { IngestionStatusChip } from "@/components/bridges/AttachmentIngestion";
import { Menu, type MenuItem } from "@/components/bridges/Menu";
import { StateBlock } from "@/components/bridges/StateBlock";
import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { MainContent } from "@/components/layout/MainContent";
import {
  ApiError,
  classifyApiError,
  downloadLearningProjectFile,
  getLearningProject,
  getLearningProjectMigration,
  listLearningProjectFiles,
  retryLearningProjectMigration,
  type LearningProjectConversation,
  type LearningProjectDetail,
  type LearningProjectFile,
  type LearningProjectMigrationSummary,
} from "@/lib/api";
import { formatAbsoluteTime, formatRelativeTime, formatSize } from "@/lib/format";

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function conversationModeLabel(mode: LearningProjectConversation["mode"]): string {
  return mode === "study" ? "学习模式" : "日常陪伴";
}

type LoadFailure = { kind: "error" | "permission" | "notfound"; message: string } | null;

/**
 * 学习项目详情页（Issue 19）。
 *
 * 仅读取项目头部、相关对话、历史项目文件及迁移记录；所有项目文件写操作
 * 与项目归属修改均已退役，新的文件统一进入全局知识库。
 */
export default function ProjectDetailPageClient() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;

  const [detail, setDetail] = useState<LearningProjectDetail | null>(null);
  const [migration, setMigration] = useState<LearningProjectMigrationSummary | null>(null);
  const [migrationLoaded, setMigrationLoaded] = useState(false);
  const [files, setFiles] = useState<LearningProjectFile[] | null>(null);
  const [loadFailure, setLoadFailure] = useState<LoadFailure>(null);
  const [filesError, setFilesError] = useState("");
  const [actionError, setActionError] = useState("");

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

  // 历史文件的摄取状态仍可变化，页面只读轮询其派生状态。
  const POLL_INTERVAL_MS = 2500;
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

  const doDownload = async (file: LearningProjectFile) => {
    setActionError("");
    try {
      await downloadLearningProjectFile(projectId, file.object_id, file.filename);
    } catch (error) {
      setActionError(errorMessage(error, "下载失败，请稍后重试。"));
    }
  };

  // -------------------------------------------------------------------------
  // 渲染
  // -------------------------------------------------------------------------

  const fileRowMenuItems = (file: LearningProjectFile): MenuItem[] => [
    { label: "下载历史文件", icon: "download", onSelect: () => void doDownload(file) },
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
                  这是历史项目；新的文件请统一进入全局知识库。
                </p>
              )}
            </div>
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
              还没有历史关联对话。
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
          </div>
          <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
            项目文件写入已退役；历史文件仅保留只读查看与迁移记录。
            <Link
              href="/knowledge-base"
              style={{ color: "var(--color-accent-primary)", textDecoration: "underline" }}
            >
              本地知识库
            </Link>
            的全局材料对你所有对话生效。
          </p>

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
                还没有历史项目文件。
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
                            处理失败{file.error ? `：${file.error}` : "；请在全局知识库中使用新的材料。"}
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

    </MainContent>
  );
}
