"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import {
  cancelImageTask,
  deleteImageAsset,
  getImageAsset,
  getImageTask,
  imageVersionUrl,
  retryImageTask,
  updateImageAltText,
  type ImageAssetProjection,
  type ImageTaskProjection,
  type ImageTaskStatus,
  type ImageVersionProjection,
} from "@/lib/api";
import { useApiMutation, useApiQuery } from "@/lib/data";

/** 轮询间隔（毫秒）：任务进行中每 5 秒查询一次云端进度。 */
const POLL_INTERVAL_MS = 5000;

/** 进行中状态：轮询与取消入口的条件（终态停轮询）。 */
const ACTIVE_STATUSES: ImageTaskStatus[] = ["queued", "running", "recovery"];

interface ImageTaskCardProps {
  conversationId: string;
  task: ImageTaskProjection;
  /** 任务成功（资产落库）时通知宿主刷新消息列表（正文与投影同步）。 */
  onSucceeded?: () => void;
}

/** 六态状态芯片文案与视觉映射（与项目状态色 tokens 对齐）。 */
const STATUS_META: Record<ImageTaskStatus, { label: string; color: string; bg: string }> = {
  queued: {
    label: "排队中",
    color: "var(--color-status-wait)",
    bg: "var(--color-status-wait-bg)",
  },
  running: {
    label: "生成中",
    color: "var(--color-status-info)",
    bg: "var(--color-status-info-bg)",
  },
  recovery: {
    label: "恢复中",
    color: "var(--color-status-wait)",
    bg: "var(--color-status-wait-bg)",
  },
  succeeded: {
    label: "已完成",
    color: "var(--color-status-success)",
    bg: "var(--color-status-success-bg)",
  },
  failed: {
    label: "失败",
    color: "var(--color-status-error)",
    bg: "var(--color-status-error-bg)",
  },
  cancelled: {
    label: "已取消",
    color: "var(--color-status-unknown)",
    bg: "var(--color-status-unknown-bg)",
  },
};

const cardStyle: React.CSSProperties = {
  display: "grid",
  gap: "var(--space-2)",
  marginTop: "var(--space-2)",
  padding: "var(--space-3)",
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-lg)",
  background: "var(--color-surface)",
};

/**
 * 图片任务状态卡（Issue 31），嵌入助手消息流。
 *
 * 呈现排队/运行/恢复/成功/失败/取消六态：进行中每 5 秒轮询任务投影
 * （任务表是权威，消息投影是快照），成功后拉取资产详情渲染资产卡；
 * 取消后迟到结果不会发布为成功资产（后端条件更新保证）。失败可重试
 * 同一输入，能力不可用等不可重试错误只显示原因。卸载/切换时停止轮询，
 * 不遗留跨对话/跨账户的请求。
 */
export function ImageTaskCard({ conversationId, task: initialTask, onSucceeded }: ImageTaskCardProps) {
  // 轮询任务投影（任务表是权威，消息投影是快照）：useApiQuery 统一
  // loading/error/reload 生命周期——首次立即拉取、每 5 秒一轮、终态停
  // 轮询、卸载自动清理。
  const { data: polled, reload } = useApiQuery(
    `image-task:${conversationId}:${initialTask.task_id}`,
    () => getImageTask(conversationId, initialTask.task_id),
    {
      pollMs: POLL_INTERVAL_MS,
      stopWhen: (latest) => !ACTIVE_STATUSES.includes(latest.status),
    }
  );
  // 换任务（key 变化）后、新数据到达前的旧轮询结果不用于渲染。
  const task = polled?.task_id === initialTask.task_id ? polled : initialTask;

  const [asset, setAsset] = useState<ImageAssetProjection | null>(null);
  const [assetError, setAssetError] = useState("");
  const [deleted, setDeleted] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const notifiedRef = useRef(false);

  // 取消/重试/删除：pending 与 error 由 useApiMutation 统一管理。
  const { run: runCancel, pending: cancelling, error: cancelError } = useApiMutation(() =>
    cancelImageTask(conversationId, task.task_id)
  );
  const { run: runRetry, pending: retrying, error: retryError } = useApiMutation(() =>
    retryImageTask(conversationId, task.task_id)
  );
  const { run: runDelete, pending: deleting, error: deleteError } = useApiMutation(() =>
    deleteImageAsset(conversationId, task.asset_id ?? "")
  );

  const busy = cancelling ? "cancel" : retrying ? "retry" : deleting ? "delete" : "";
  const actionError = cancelError ?? retryError ?? deleteError;

  // 消息投影变化时同步（刷新/重登后从消息投影恢复）。
  useEffect(() => {
    if (initialTask.status !== "succeeded") {
      setAsset(null);
      setAssetError("");
    }
  }, [initialTask]);

  // 成功态：拉取资产详情（版本链/替代文本）。
  useEffect(() => {
    if (task.status !== "succeeded" || !task.asset_id) return;
    let cancelled = false;
    getImageAsset(conversationId, task.asset_id)
      .then((result) => {
        if (!cancelled) setAsset(result);
      })
      .catch(() => {
        if (!cancelled) setAssetError("图片详情加载失败，请稍后重试。");
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, task.status, task.asset_id]);

  // 轮询发现任务成功（资产落库）时通知宿主刷新消息列表（正文与投影同步）。
  useEffect(() => {
    if (polled?.status === "succeeded" && !notifiedRef.current) {
      notifiedRef.current = true;
      onSucceeded?.();
    }
  }, [polled?.status, onSucceeded]);

  const handleCancel = async () => {
    if (busy) return;
    try {
      await runCancel();
      reload();
    } catch {
      // 错误已进入 mutation error，由下方提示区展示。
    }
  };

  const handleRetry = async () => {
    if (busy) return;
    try {
      await runRetry();
      notifiedRef.current = false;
      reload();
    } catch {
      // 错误已进入 mutation error，由下方提示区展示。
    }
  };

  const handleDelete = async () => {
    if (busy || !task.asset_id) return;
    try {
      await runDelete();
      setDeleted(true);
      setShowDeleteConfirm(false);
    } catch {
      // 错误已进入 mutation error，由下方提示区展示。
    }
  };

  if (deleted || task.deleted) {
    return (
      <div style={cardStyle} data-testid="image-asset-deleted">
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
          <Icon name="imagePicture" size={18} aria-hidden />
          <span>该图片已删除。</span>
        </div>
      </div>
    );
  }

  if (task.status === "succeeded") {
    return (
      <div style={cardStyle} data-testid="image-asset-card">
        {asset ? (
          <ImageAssetContent
            conversationId={conversationId}
            asset={asset}
            busy={busy}
            onDeleteConfirmOpen={() => setShowDeleteConfirm(true)}
          />
        ) : (
          <div role="status" style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
            {assetError || actionError?.message || "正在加载图片…"}
          </div>
        )}
        {showDeleteConfirm && (
          <DeleteConfirm
            versionCount={asset?.version_count ?? 0}
            busy={busy === "delete"}
            onCancel={() => setShowDeleteConfirm(false)}
            onConfirm={() => void handleDelete()}
          />
        )}
        {(assetError || actionError) && (
          <p role="alert" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
            {assetError || actionError?.message}
          </p>
        )}
      </div>
    );
  }

  const meta = STATUS_META[task.status];
  const canCancel = ["queued", "running", "recovery"].includes(task.status);
  const canRetry = task.status === "failed" && task.retryable;
  return (
    <div style={cardStyle} data-testid="image-task-card">
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <Icon name="imagePicture" size={18} aria-hidden />
        <span
          role="status"
          aria-live="polite"
          data-testid="image-task-status"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "var(--space-1)",
            padding: "var(--space-0-5, 2px) var(--space-2)",
            borderRadius: "var(--radius-full, 999px)",
            fontSize: "var(--text-xs)",
            fontWeight: 600,
            color: meta.color,
            backgroundColor: meta.bg,
          }}
        >
          {meta.label}
        </span>
        <span style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-xs)" }}>
          {task.kind === "edit" ? "图片编辑" : "图片生成"} · 固定模型 qwen-image-2.0-pro-2026-06-22
        </span>
      </div>
      <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-primary)", wordBreak: "break-word" }}>
        {task.prompt}
      </p>
      {task.status === "failed" && (
        <p role="alert" data-testid="image-task-error" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
          {task.error_message || "图片生成失败。"}
        </p>
      )}
      {task.status === "recovery" && (
        <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          上次处理被中断，后台正在恢复任务；若长时间无进展请稍后重试。
        </p>
      )}
      {(canCancel || canRetry) && (
        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          {canRetry && (
            <Button variant="secondary" size="sm" onClick={() => void handleRetry()} disabled={busy !== ""} data-testid="image-task-retry">
              <Icon name="retry" size={14} aria-hidden />
              {busy === "retry" ? "正在重试…" : "重试"}
            </Button>
          )}
          {canCancel && (
            <Button variant="secondary" size="sm" onClick={() => void handleCancel()} disabled={busy !== ""} data-testid="image-task-cancel">
              {busy === "cancel" ? "正在取消…" : "取消"}
            </Button>
          )}
        </div>
      )}
      {(assetError || actionError) && (
        <p role="alert" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
          {assetError || actionError?.message}
        </p>
      )}
    </div>
  );
}

/** 资产卡内容：图片显示、替代文本编辑、版本切换、下载、删除入口。 */
function ImageAssetContent({
  conversationId,
  asset,
  busy,
  onDeleteConfirmOpen,
}: {
  conversationId: string;
  asset: ImageAssetProjection;
  busy: string;
  onDeleteConfirmOpen: () => void;
}) {
  const versions = asset.versions ?? [];
  const currentVersion =
    versions.find((version) => version.version_id === asset.current_version_id) ??
    versions[versions.length - 1];
  const [activeVersion, setActiveVersion] = useState<ImageVersionProjection | undefined>(currentVersion);
  const [editingAlt, setEditingAlt] = useState(false);
  const [altDraft, setAltDraft] = useState(asset.alt_text);
  const [altSaving, setAltSaving] = useState(false);
  const [altError, setAltError] = useState("");

  // 资产更新（替代文本保存后）时同步本地视图。
  useEffect(() => {
    setAltDraft(asset.alt_text);
    setActiveVersion((previous) => previous ?? currentVersion);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅资产整体变化时校正
  }, [asset]);

  const saveAltText = async () => {
    if (altSaving) return;
    const cleaned = altDraft.trim();
    if (!cleaned) {
      setAltError("替代文本不能为空。");
      return;
    }
    setAltSaving(true);
    setAltError("");
    try {
      await updateImageAltText(conversationId, asset.asset_id, cleaned);
      setEditingAlt(false);
    } catch (error) {
      setAltError(error instanceof Error ? error.message : "保存失败，请重试。");
    } finally {
      setAltSaving(false);
    }
  };

  const active = activeVersion ?? currentVersion;
  return (
    <div style={{ display: "grid", gap: "var(--space-3)" }}>
      <img
        src={active ? imageVersionUrl(conversationId, asset.asset_id, active.version_id) : undefined}
        alt={asset.alt_text || "生成的图片"}
        data-testid="image-asset-preview"
        style={{
          display: "block",
          width: "100%",
          maxWidth: "24rem",
          height: "auto",
          borderRadius: "var(--radius-md)",
          border: "1px solid var(--color-border)",
          background: "var(--color-bg)",
        }}
      />
      {active && (
        <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          {active.kind === "edit" ? "编辑版本" : "生成版本"} · {active.prompt}
          {active.model_id ? ` · ${active.model_id}` : ""}
        </p>
      )}

      {/* 替代文本 */}
      <div data-testid="image-alt-text">
        {editingAlt ? (
          <div style={{ display: "grid", gap: "var(--space-2)" }}>
            <label htmlFor="image-alt-input" style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>
              替代文本（用于无障碍描述，会随图片一同保存）
            </label>
            <textarea
              id="image-alt-input"
              data-testid="image-alt-input"
              value={altDraft}
              onChange={(event) => setAltDraft(event.target.value)}
              rows={2}
              style={{
                width: "100%",
                padding: "var(--space-2) var(--space-3)",
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-md)",
                background: "var(--color-surface)",
                color: "var(--color-text)",
                fontSize: "var(--text-sm)",
                fontFamily: "inherit",
                boxSizing: "border-box",
                resize: "vertical",
              }}
            />
            {altError && (
              <p role="alert" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
                {altError}
              </p>
            )}
            <div style={{ display: "flex", gap: "var(--space-2)" }}>
              <Button variant="primary" size="sm" onClick={() => void saveAltText()} disabled={altSaving} data-testid="image-alt-save">
                {altSaving ? "正在保存…" : "保存"}
              </Button>
              <Button variant="secondary" size="sm" onClick={() => { setEditingAlt(false); setAltError(""); setAltDraft(asset.alt_text); }} data-testid="image-alt-cancel">
                取消
              </Button>
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "flex-start", gap: "var(--space-2)" }}>
            <p style={{ margin: 0, flex: 1, fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>
              <span style={{ fontWeight: 600 }}>替代文本：</span>
              {asset.alt_text || "（空）"}
            </p>
            <Button variant="secondary" size="sm" onClick={() => { setAltDraft(asset.alt_text); setAltError(""); setEditingAlt(true); }} data-testid="image-alt-edit">
              修改
            </Button>
          </div>
        )}
      </div>

      {/* 版本切换 */}
      {versions.length > 1 && (
        <div data-testid="image-version-switcher">
          <span style={{ display: "block", fontSize: "var(--text-xs)", color: "var(--color-text-secondary)", marginBottom: "var(--space-1)" }}>
            版本记录（{versions.length}）
          </span>
          <div role="group" aria-label="版本切换" style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-1)" }}>
            {versions.map((version, index) => {
              const isActive = active?.version_id === version.version_id;
              return (
                <button
                  key={version.version_id}
                  type="button"
                  data-testid="image-version-button"
                  aria-pressed={isActive}
                  onClick={() => setActiveVersion(version)}
                  style={{
                    padding: "var(--space-0-5, 2px) var(--space-2)",
                    border: `1px solid ${isActive ? "var(--color-accent-primary)" : "var(--color-border)"}`,
                    borderRadius: "var(--radius-full, 999px)",
                    background: isActive ? "var(--color-accent-primary-soft)" : "transparent",
                    color: "var(--color-text-primary)",
                    cursor: "pointer",
                    fontSize: "var(--text-xs)",
                  }}
                >
                  v{index + 1} · {version.kind === "edit" ? "编辑" : "生成"}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* 下载 / 删除 */}
      <div style={{ display: "flex", gap: "var(--space-2)" }}>
        {active && (
          <a
            href={imageVersionUrl(conversationId, asset.asset_id, active.version_id, true)}
            data-testid="image-download"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "var(--space-1)",
              padding: "var(--space-1) var(--space-2)",
              border: "1px solid var(--color-border)",
              borderRadius: "var(--radius-md)",
              color: "var(--color-text-primary)",
              textDecoration: "none",
              fontSize: "var(--text-sm)",
            }}
          >
            <Icon name="download" size={14} aria-hidden />
            下载当前版本
          </a>
        )}
        <Button variant="secondary" size="sm" onClick={onDeleteConfirmOpen} disabled={busy === "delete"} data-testid="image-delete">
          删除图片
        </Button>
      </div>
    </div>
  );
}

/** 带影响说明的删除确认区（版本数 + 消息引用说明）。 */
function DeleteConfirm({
  versionCount,
  busy,
  onCancel,
  onConfirm,
}: {
  versionCount: number;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      role="alertdialog"
      aria-label="删除图片确认"
      data-testid="image-delete-confirm"
      style={{
        display: "grid",
        gap: "var(--space-2)",
        padding: "var(--space-3)",
        border: "1px solid var(--color-status-error)",
        borderRadius: "var(--radius-md)",
        background: "var(--color-status-error-bg)",
      }}
    >
      <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-primary)" }}>
        删除该图片将移除全部 {versionCount} 个版本及其本地对象，对话中引用此图片的消息将显示「已删除」；此操作不可撤销。
      </p>
      <div style={{ display: "flex", gap: "var(--space-2)" }}>
        <Button variant="danger" size="sm" onClick={onConfirm} disabled={busy} data-testid="image-delete-confirm-button">
          {busy ? "正在删除…" : "确认删除"}
        </Button>
        <Button variant="secondary" size="sm" onClick={onCancel} data-testid="image-delete-cancel">
          保留图片
        </Button>
      </div>
    </div>
  );
}
