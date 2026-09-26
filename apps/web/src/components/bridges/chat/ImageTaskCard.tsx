"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/design-system/Icon";
import {
  getImageAsset,
  getImageTask,
  imageVersionUrl,
  type ImageAssetProjection,
  type ImageTaskProjection,
  type ImageTaskStatus,
  type ImageVersionProjection,
} from "@/lib/api";
import { useApiQuery } from "@/lib/data";

/** 轮询间隔（毫秒）：任务进行中每 5 秒查询一次云端进度。 */
const POLL_INTERVAL_MS = 5000;

/** 进行中状态：轮询条件（终态停轮询）。 */
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
 * 旧图片任务只读卡（Issue 31 引入，Issue 21 退役写入口）。
 *
 * 图片生成是 ADR-0030 列明的退役能力：本卡只负责历史结果的只读呈现——
 * 状态芯片、提示词、版本链与图片字节流；取消、重试、替代文本修改与删除
 * 一律不再提供（对应写入口稳定返回 410）。进行中的历史任务仍每 5 秒轮询
 * 一次投影，保证状态显示与任务表一致。
 */
export function ImageTaskCard({ conversationId, task: initialTask, onSucceeded }: ImageTaskCardProps) {
  // 轮询任务投影（任务表是权威，消息投影是快照）：首次立即拉取、每 5 秒
  // 一轮、终态停轮询、卸载自动清理。
  const { data: polled } = useApiQuery(
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
  const notifiedRef = useRef(false);

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

  if (task.deleted) {
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
          <ImageAssetContent conversationId={conversationId} asset={asset} />
        ) : (
          <div role="status" style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
            {assetError || "正在加载图片…"}
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
      {/* Issue 21：图片生成入口已退役，历史任务只展示状态，不再提供重试或取消。 */}
      <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        图片生成已退役，这张历史任务只能查看。
      </p>
    </div>
  );
}

/** 历史资产只读内容：图片显示、替代文本与版本链浏览、下载。 */
function ImageAssetContent({
  conversationId,
  asset,
}: {
  conversationId: string;
  asset: ImageAssetProjection;
}) {
  const versions = asset.versions ?? [];
  const currentVersion =
    versions.find((version) => version.version_id === asset.current_version_id) ??
    versions[versions.length - 1];
  const [activeVersion, setActiveVersion] = useState<ImageVersionProjection | undefined>(currentVersion);

  // 资产更新（刷新/重登）时同步本地视图。
  useEffect(() => {
    setActiveVersion((previous) => previous ?? currentVersion);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅资产整体变化时校正
  }, [asset]);

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

      <div data-testid="image-alt-text">
        <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>
          <span style={{ fontWeight: 600 }}>替代文本：</span>
          {asset.alt_text || "（空）"}
        </p>
      </div>

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

      {active && (
        <div style={{ display: "flex" }}>
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
        </div>
      )}
    </div>
  );
}
