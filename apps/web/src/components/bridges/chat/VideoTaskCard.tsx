"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/design-system/Icon";
import {
  getVideoAsset,
  getVideoTask,
  videoUrl,
  type VideoAssetProjection,
  type VideoTaskProjection,
  type VideoTaskStatus,
} from "@/lib/api";
import { useApiQuery } from "@/lib/data";

/** 轮询间隔（毫秒）：任务进行中每 5 秒查询一次云端进度。 */
const POLL_INTERVAL_MS = 5000;

/** 进行中状态：轮询条件（终态停轮询）。 */
const ACTIVE_STATUSES: VideoTaskStatus[] = [
  "queued",
  "submitting",
  "generating",
  "recovery",
  "cancelling",
];

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
 * 旧视频任务只读卡（Issue 32 引入，Issue 21 退役写入口）。
 *
 * 视频生成是 ADR-0030 列明的退役能力：本卡只负责历史结果的只读呈现——
 * 状态芯片、提示词、预览与下载；取消、重试、说明修改与删除一律不再提供
 * （对应写入口稳定返回 410）。进行中的历史任务仍每 5 秒轮询一次投影，
 * 保证状态显示与任务表一致。
 */
export function VideoTaskCard({ conversationId, task: initialTask, onSucceeded }: VideoTaskCardProps) {
  // 轮询任务投影（任务表是权威，消息投影是快照）：首次立即拉取、每 5 秒
  // 一轮、终态停轮询、卸载自动清理。
  const { data: polled } = useApiQuery(
    `video-task:${conversationId}:${initialTask.task_id}`,
    () => getVideoTask(conversationId, initialTask.task_id),
    {
      pollMs: POLL_INTERVAL_MS,
      stopWhen: (latest) => !ACTIVE_STATUSES.includes(latest.status),
    }
  );
  // 换任务（key 变化）后、新数据到达前的旧轮询结果不用于渲染。
  const task = polled?.task_id === initialTask.task_id ? polled : initialTask;

  const [asset, setAsset] = useState<VideoAssetProjection | null>(null);
  const [assetError, setAssetError] = useState("");
  const notifiedRef = useRef(false);

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

  // 轮询发现任务成功（资产落库）时通知宿主刷新消息列表（正文与投影同步）。
  useEffect(() => {
    if (polled?.status === "succeeded" && !notifiedRef.current) {
      notifiedRef.current = true;
      onSucceeded?.();
    }
  }, [polled?.status, onSucceeded]);

  if (task.deleted) {
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
          <VideoAssetContent conversationId={conversationId} asset={asset} />
        ) : (
          <div role="status" style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
            {assetError || "正在加载视频…"}
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

  const meta = STATUS_META[task.status];
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
          上次处理被中断，后台正在恢复任务。
        </p>
      )}
      {task.status === "cancelling" && (
        <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          该历史任务已请求取消，已生成的云端结果不会被保存。
        </p>
      )}
      {/* Issue 21：视频生成入口已退役，历史任务只展示状态，不再提供重试或取消。 */}
      <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        视频生成已退役，这条历史任务只能查看。
      </p>
    </div>
  );
}

/** 历史资产只读内容：视频预览、可访问文字说明与下载。 */
function VideoAssetContent({
  conversationId,
  asset,
}: {
  conversationId: string;
  asset: VideoAssetProjection;
}) {
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

      <div data-testid="video-description">
        <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>
          <span style={{ fontWeight: 600 }}>可访问文字说明：</span>
          {asset.description || "（空）"}
        </p>
      </div>

      <div style={{ display: "flex" }}>
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
      </div>
    </div>
  );
}
