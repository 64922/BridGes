"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import {
  cancelVideoTask,
  deleteVideoAsset,
  getVideoAsset,
  getVideoTask,
  retryVideoTask,
  updateVideoDescription,
  videoUrl,
  type VideoAssetProjection,
  type VideoTaskProjection,
  type VideoTaskStatus,
} from "@/lib/api";

/** 轮询间隔（毫秒）：任务进行中每 5 秒查询一次云端进度。 */
const POLL_INTERVAL_MS = 5000;

/** 固定模型快照展示文案（ADR-0007：Wan 是矩阵唯一非 Qwen 系列例外）。 */
const VIDEO_MODEL_LABEL = "wan2.7-t2v-2026-06-12";

interface VideoTaskCardProps {
  conversationId: string;
  task: VideoTaskProjection;
  /** 任务成功（资产落库）时通知宿主刷新消息列表（正文与投影同步）。 */
  onSucceeded?: () => void;
}

/** 八态状态芯片文案与视觉映射（与项目状态色 tokens 对齐）。 */
const STATUS_META: Record<VideoTaskStatus, { label: string; color: string; bg: string }> = {
  queued: {
    label: "排队中",
    color: "var(--color-status-wait)",
    bg: "var(--color-status-wait-bg)",
  },
  submitting: {
    label: "提交中",
    color: "var(--color-status-info)",
    bg: "var(--color-status-info-bg)",
  },
  generating: {
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
  cancelling: {
    label: "取消中",
    color: "var(--color-status-wait)",
    bg: "var(--color-status-wait-bg)",
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
 * 视频任务状态卡（Issue 32），嵌入助手消息流。
 *
 * 呈现排队/提交中/生成中/恢复中/成功/失败/取消中/已取消八态：进行中
 * 每 5 秒轮询任务投影（任务表是权威，消息投影是快照），成功后拉取资产
 * 详情渲染资产卡；取消后迟到结果不会发布为成功资产（后端条件发布保证）。
 * 失败可重试同一输入，能力不可用等不可重试错误只显示原因。卸载/切换时
 * 停止轮询，不遗留跨对话/跨账户的请求。
 */
export function VideoTaskCard({ conversationId, task: initialTask, onSucceeded }: VideoTaskCardProps) {
  const [task, setTask] = useState(initialTask);
  const [asset, setAsset] = useState<VideoAssetProjection | null>(null);
  const [assetError, setAssetError] = useState("");
  const [busy, setBusy] = useState<string>("");
  const [deleted, setDeleted] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const notifiedRef = useRef(false);

  // 消息投影变化时同步（刷新/重登后从消息投影恢复）。
  useEffect(() => {
    setTask(initialTask);
    if (initialTask.status !== "succeeded") {
      setAsset(null);
      setAssetError("");
    }
  }, [initialTask]);

  // 成功态：拉取资产详情（可访问文字说明/提示/模型/供应商任务标识）。
  useEffect(() => {
    if (task.status !== "succeeded" || !task.asset_id) return;
    let cancelled = false;
    getVideoAsset(conversationId, task.asset_id)
      .then((result) => {
        if (!cancelled) setAsset(result);
      })
      .catch(() => {
        if (!cancelled) setAssetError("视频详情加载失败，请稍后重试。");
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, task.status, task.asset_id]);

  // 进行中：轮询任务投影直到终态；成功时通知宿主刷新消息列表。
  useEffect(() => {
    if (["queued", "submitting", "generating", "recovery", "cancelling"].includes(task.status) === false)
      return;
    let cancelled = false;
    const tick = async () => {
      try {
        const latest = await getVideoTask(conversationId, task.task_id);
        if (cancelled) return;
        setTask(latest);
        if (latest.status === "succeeded" && !notifiedRef.current) {
          notifiedRef.current = true;
          onSucceeded?.();
        }
      } catch {
        // 轮询失败静默：下一轮重试；终态由下次查询或刷新恢复。
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [conversationId, task.task_id, task.status, onSucceeded]);

  const handleCancel = useCallback(async () => {
    if (busy) return;
    setBusy("cancel");
    try {
      setTask(await cancelVideoTask(conversationId, task.task_id));
    } catch (error) {
      setAssetError(error instanceof Error ? error.message : "取消失败，请重试。");
    } finally {
      setBusy("");
    }
  }, [busy, conversationId, task.task_id]);

  const handleRetry = useCallback(async () => {
    if (busy) return;
    setBusy("retry");
    try {
      setTask(await retryVideoTask(conversationId, task.task_id));
      notifiedRef.current = false;
    } catch (error) {
      setAssetError(error instanceof Error ? error.message : "重试失败，请重试。");
    } finally {
      setBusy("");
    }
  }, [busy, conversationId, task.task_id]);

  const handleDelete = async () => {
    if (busy || !task.asset_id) return;
    setBusy("delete");
    try {
      await deleteVideoAsset(conversationId, task.asset_id);
      setDeleted(true);
      setShowDeleteConfirm(false);
    } catch (error) {
      setAssetError(error instanceof Error ? error.message : "删除失败，请重试。");
    } finally {
      setBusy("");
    }
  };

  if (deleted || task.deleted) {
    return (
      <div style={cardStyle} data-testid="video-asset-deleted">
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
          <Icon name="videoClapper" size={18} aria-hidden />
          <span>该视频已删除。</span>
        </div>
      </div>
    );
  }

  if (task.status === "succeeded") {
    return (
      <div style={cardStyle} data-testid="video-asset-card">
        {asset ? (
          <VideoAssetContent
            conversationId={conversationId}
            asset={asset}
            busy={busy}
            onDeleteConfirmOpen={() => setShowDeleteConfirm(true)}
          />
        ) : (
          <div role="status" style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
            {assetError || "正在加载视频…"}
          </div>
        )}
        {showDeleteConfirm && (
          <DeleteConfirm
            busy={busy === "delete"}
            onCancel={() => setShowDeleteConfirm(false)}
            onConfirm={() => void handleDelete()}
          />
        )}
        {assetError && (
          <p role="alert" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
            {assetError}
          </p>
        )}
      </div>
    );
  }

  const meta = STATUS_META[task.status];
  const canCancel = ["queued", "submitting", "generating", "recovery"].includes(task.status);
  const canRetry = task.status === "failed" && task.retryable;
  return (
    <div style={cardStyle} data-testid="video-task-card">
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <Icon name="videoClapper" size={18} aria-hidden />
        <span
          role="status"
          aria-live="polite"
          data-testid="video-task-status"
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
          视频生成 · 固定模型 {VIDEO_MODEL_LABEL}
        </span>
      </div>
      <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-primary)", wordBreak: "break-word" }}>
        {task.prompt}
      </p>
      {task.status === "failed" && (
        <p role="alert" data-testid="video-task-error" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
          {task.error_message || "视频生成失败。"}
        </p>
      )}
      {task.status === "recovery" && (
        <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          上次处理被中断，后台正在恢复任务；若长时间无进展请稍后重试。
        </p>
      )}
      {task.status === "cancelling" && (
        <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          正在取消生成任务，已生成的云端结果不会被保存。
        </p>
      )}
      {(canCancel || canRetry) && (
        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          {canRetry && (
            <Button variant="secondary" size="sm" onClick={() => void handleRetry()} disabled={busy !== ""} data-testid="video-task-retry">
              <Icon name="retry" size={14} aria-hidden />
              {busy === "retry" ? "正在重试…" : "重试"}
            </Button>
          )}
          {canCancel && (
            <Button variant="secondary" size="sm" onClick={() => void handleCancel()} disabled={busy !== ""} data-testid="video-task-cancel">
              {busy === "cancel" ? "正在取消…" : "取消"}
            </Button>
          )}
        </div>
      )}
      {assetError && (
        <p role="alert" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
          {assetError}
        </p>
      )}
    </div>
  );
}

/** 资产卡内容：视频预览、可访问文字说明编辑、下载、删除入口。 */
function VideoAssetContent({
  conversationId,
  asset,
  busy,
  onDeleteConfirmOpen,
}: {
  conversationId: string;
  asset: VideoAssetProjection;
  busy: string;
  onDeleteConfirmOpen: () => void;
}) {
  const [editingDescription, setEditingDescription] = useState(false);
  const [descriptionDraft, setDescriptionDraft] = useState(asset.description);
  const [descriptionSaving, setDescriptionSaving] = useState(false);
  const [descriptionError, setDescriptionError] = useState("");

  // 资产更新（说明保存后）时同步本地视图。
  useEffect(() => {
    setDescriptionDraft(asset.description);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅资产整体变化时校正
  }, [asset]);

  const saveDescription = async () => {
    if (descriptionSaving) return;
    const cleaned = descriptionDraft.trim();
    if (!cleaned) {
      setDescriptionError("可访问文字说明不能为空。");
      return;
    }
    setDescriptionSaving(true);
    setDescriptionError("");
    try {
      await updateVideoDescription(conversationId, asset.asset_id, cleaned);
      setEditingDescription(false);
    } catch (error) {
      setDescriptionError(error instanceof Error ? error.message : "保存失败，请重试。");
    } finally {
      setDescriptionSaving(false);
    }
  };

  return (
    <div style={{ display: "grid", gap: "var(--space-3)" }}>
      {/* 视频预览：不自动播放（省流量/省电），由用户点击播放。 */}
      <video
        src={videoUrl(conversationId, asset.asset_id)}
        controls
        preload="none"
        aria-label={asset.description || "生成的视频"}
        data-testid="video-asset-preview"
        style={{
          display: "block",
          width: "100%",
          maxWidth: "28rem",
          aspectRatio: "16 / 9",
          borderRadius: "var(--radius-md)",
          border: "1px solid var(--color-border)",
          background: "var(--color-bg)",
        }}
      />
      <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        {asset.prompt}
        {asset.model_id ? ` · ${asset.model_id}` : ""}
        {asset.cloud_task_id ? ` · 供应商任务 ${asset.cloud_task_id.slice(0, 8)}…` : ""}
      </p>
      <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        创建于 {asset.created_at.slice(0, 16).replace("T", " ")}
      </p>

      {/* 可访问文字说明 */}
      <div data-testid="video-description">
        {editingDescription ? (
          <div style={{ display: "grid", gap: "var(--space-2)" }}>
            <label htmlFor="video-description-input" style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>
              可访问文字说明（用于无障碍描述，会随视频一同保存）
            </label>
            <textarea
              id="video-description-input"
              data-testid="video-description-input"
              value={descriptionDraft}
              onChange={(event) => setDescriptionDraft(event.target.value)}
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
            {descriptionError && (
              <p role="alert" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
                {descriptionError}
              </p>
            )}
            <div style={{ display: "flex", gap: "var(--space-2)" }}>
              <Button variant="primary" size="sm" onClick={() => void saveDescription()} disabled={descriptionSaving} data-testid="video-description-save">
                {descriptionSaving ? "正在保存…" : "保存"}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  setEditingDescription(false);
                  setDescriptionError("");
                  setDescriptionDraft(asset.description);
                }}
                data-testid="video-description-cancel"
              >
                取消
              </Button>
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "flex-start", gap: "var(--space-2)" }}>
            <p style={{ margin: 0, flex: 1, fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>
              <span style={{ fontWeight: 600 }}>可访问文字说明：</span>
              {asset.description || "（空）"}
            </p>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setDescriptionDraft(asset.description);
                setDescriptionError("");
                setEditingDescription(true);
              }}
              data-testid="video-description-edit"
            >
              修改
            </Button>
          </div>
        )}
      </div>

      {/* 下载 / 删除 */}
      <div style={{ display: "flex", gap: "var(--space-2)" }}>
        <a
          href={videoUrl(conversationId, asset.asset_id, true)}
          download
          data-testid="video-download"
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
          下载视频
        </a>
        <Button variant="secondary" size="sm" onClick={onDeleteConfirmOpen} disabled={busy === "delete"} data-testid="video-delete">
          删除视频
        </Button>
      </div>
    </div>
  );
}

/** 带影响说明的删除确认区（对象数 + 消息引用说明）。 */
function DeleteConfirm({
  busy,
  onCancel,
  onConfirm,
}: {
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      role="alertdialog"
      aria-label="删除视频确认"
      data-testid="video-delete-confirm"
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
        删除该视频将移除其本地对象，对话中引用此视频的消息将显示「已删除」；此操作不可撤销。
      </p>
      <div style={{ display: "flex", gap: "var(--space-2)" }}>
        <Button variant="danger" size="sm" onClick={onConfirm} disabled={busy} data-testid="video-delete-confirm-button">
          {busy ? "正在删除…" : "确认删除"}
        </Button>
        <Button variant="secondary" size="sm" onClick={onCancel} data-testid="video-delete-cancel">
          保留视频
        </Button>
      </div>
    </div>
  );
}
