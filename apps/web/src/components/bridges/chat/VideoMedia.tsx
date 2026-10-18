"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/design-system/Icon";
import {
  getVideoAsset,
  getVideoTask,
  videoUrl,
  type VideoAssetProjection,
  type VideoTaskProjection,
} from "@/lib/api";
import { useApiQuery } from "@/lib/data";
import {
  VIDEO_STATUS_LABELS,
  activeMediaHint,
  isVideoTaskActive,
} from "@/lib/media-status";

import styles from "./MediaResult.module.css";

const POLL_INTERVAL_MS = 5000;
const DEFAULT_VIDEO_MODEL = "wan2.7-t2v-2026-06-12";
const RETIRED_HINT = "视频生成已退役，历史任务只能查看。";
const RETIRED_SUCCESS_HINT = "视频生成已退役，历史结果仍可查看与下载。";

/**
 * 正文旁的视频结果：状态、真实资产与说明属于同一条消息。
 *
 * 视频不自动播放，保留浏览器播放控件；成功也只把已经通过账户授权
 * 读取的资产渲染为预览与下载入口。完整说明与供应商追溯在右侧面板查看。
 */
export function VideoMedia({
  conversationId,
  task: initialTask,
  onRefreshMessages,
  onOpenPreview,
}: {
  conversationId: string;
  task: VideoTaskProjection;
  onRefreshMessages?: () => void;
  onOpenPreview?: (trigger: HTMLButtonElement) => void;
}) {
  const { data: polled } = useApiQuery(
    `video-task:${conversationId}:${initialTask.task_id}`,
    () => getVideoTask(conversationId, initialTask.task_id),
    {
      pollMs: POLL_INTERVAL_MS,
      stopWhen: (latest) => !isVideoTaskActive(latest),
    }
  );
  const task = polled?.task_id === initialTask.task_id ? polled : initialTask;

  const assetEnabled =
    task.status === "succeeded" && !task.deleted && Boolean(task.asset_id);
  const {
    data: asset,
    error: assetError,
    loading: assetLoading,
    reload: reloadAsset,
  } = useApiQuery(
    `video-asset:${conversationId}:${task.asset_id ?? "none"}`,
    () => getVideoAsset(conversationId, task.asset_id as string),
    { enabled: assetEnabled }
  );

  const notifiedRef = useRef<string | null>(null);
  useEffect(() => {
    if (
      polled?.task_id === initialTask.task_id &&
      polled.status === "succeeded" &&
      initialTask.status !== "succeeded" &&
      notifiedRef.current !== polled.task_id
    ) {
      notifiedRef.current = polled.task_id;
      onRefreshMessages?.();
    }
  }, [initialTask.status, initialTask.task_id, onRefreshMessages, polled]);

  const [previewBroken, setPreviewBroken] = useState(false);
  const [previewToken, setPreviewToken] = useState(0);

  if (task.deleted) {
    return (
      <section className={styles.media} data-testid="video-media-deleted">
        <p className={styles.notice}>
          <Icon name="videoClapper" size={16} aria-hidden /> 该视频已删除。
        </p>
        <p className={styles.hint}>
          历史说明保留；播放与下载入口已移除，也不会读取旧缓存。
        </p>
      </section>
    );
  }

  if (task.status === "succeeded") {
    return (
      <section className={styles.media} data-testid="video-media" data-status="succeeded">
        <div className={styles.statusRow}>
          <span
            className={styles.statusChip}
            data-status="succeeded"
            role="status"
            aria-live="polite"
            data-testid="video-media-status"
          >
            {VIDEO_STATUS_LABELS.succeeded}
          </span>
          <span className={styles.meta}>视频生成 · 历史只读</span>
        </div>

        {!task.asset_id ? (
          <p
            className={`${styles.notice} ${styles.noticeError}`}
            role="alert"
            data-testid="video-media-unavailable"
          >
            任务显示成功，但结果资产不可用；未显示预览或下载链接。
          </p>
        ) : assetLoading ? (
          <p className={styles.statusLine} role="status" data-testid="video-media-asset-loading">
            <span className="bg-spinner" aria-hidden="true" />
            正在核对视频资产…
          </p>
        ) : assetError ? (
          <VideoAssetError error={assetError} onRetry={() => void reloadAsset()} />
        ) : asset ? (
          asset.deleted || !asset.object_id ? (
            <p
              className={`${styles.notice} ${styles.noticeError}`}
              role="alert"
              data-testid="video-media-unavailable"
            >
              视频资产已删除或不可访问；未显示预览或下载链接。
            </p>
          ) : (
            <>
              <div className={styles.previewWrap}>
                {/* 不自动播放：preload=none 且无 autoplay，播放由用户主动开始。 */}
                <video
                  key={previewToken}
                  className={styles.video}
                  src={videoUrl(conversationId, asset.asset_id)}
                  controls
                  preload="none"
                  aria-label={asset.description || "生成的视频"}
                  data-testid="video-media-preview"
                  onError={() => setPreviewBroken(true)}
                />
              </div>

              {previewBroken && (
                <div
                  className={`${styles.notice} ${styles.noticeError}`}
                  role="alert"
                  data-testid="video-media-preview-error"
                >
                  <p className={styles.notice}>
                    视频无法播放，文件可能已损坏或不可读取；上方说明仍保留。
                  </p>
                  <div className={styles.actions}>
                    <button
                      type="button"
                      className={styles.actionButton}
                      data-testid="video-media-preview-retry"
                      onClick={() => {
                        setPreviewBroken(false);
                        setPreviewToken((value) => value + 1);
                      }}
                    >
                      <Icon name="retry" size={14} aria-hidden /> 重新加载
                    </button>
                    <a
                      className={styles.actionLink}
                      href={videoUrl(conversationId, asset.asset_id, true)}
                      download
                      data-testid="video-media-download"
                    >
                      <Icon name="download" size={14} aria-hidden /> 下载视频
                    </a>
                  </div>
                </div>
              )}

              {asset.description && (
                <p className={styles.caption} data-testid="video-media-description">
                  {asset.description}
                </p>
              )}
              <p className={styles.caption} data-testid="video-media-prompt">
                生成要求：{task.prompt}
              </p>
              {!previewBroken && (
                <div className={styles.actions}>
                  {onOpenPreview && (
                    <button
                      type="button"
                      className={styles.actionButton}
                      data-testid="video-media-details"
                      onClick={(event) => onOpenPreview(event.currentTarget)}
                    >
                      <Icon name="videoClapper" size={14} aria-hidden /> 查看视频详情
                    </button>
                  )}
                  <a
                    className={styles.actionLink}
                    href={videoUrl(conversationId, asset.asset_id, true)}
                    download
                    data-testid="video-media-download"
                  >
                    <Icon name="download" size={14} aria-hidden /> 下载视频
                  </a>
                </div>
              )}
            </>
          )
        ) : null}

        <p className={styles.hint}>{RETIRED_SUCCESS_HINT}</p>
      </section>
    );
  }

  const failed = task.status === "failed";
  const cancelled = task.status === "cancelled";
  return (
    <section
      className={styles.media}
      data-testid="video-media"
      data-status={task.status}
      aria-busy={isVideoTaskActive(task)}
    >
      <div className={styles.statusRow}>
        <span
          className={styles.statusChip}
          data-status={task.status}
          role={failed ? "alert" : "status"}
          aria-live="polite"
          data-testid="video-media-status"
        >
          {VIDEO_STATUS_LABELS[task.status]}
        </span>
        <span className={styles.meta}>
          视频生成 · {task.model_id || DEFAULT_VIDEO_MODEL}
        </span>
      </div>
      <p className={styles.caption} data-testid="video-media-prompt">
        生成要求：{task.prompt}
      </p>
      {failed && (
        <p
          className={`${styles.notice} ${styles.noticeError}`}
          role="alert"
          data-testid="video-media-error"
        >
          {task.error_message || "视频生成失败。"}
        </p>
      )}
      {cancelled && (
        <p className={styles.notice} data-testid="video-media-stopped">
          任务已停止，迟到的云端结果不会保存。
        </p>
      )}
      {task.status === "cancelling" && (
        <p className={styles.notice}>已请求停止，正在等待云端收敛；迟到的结果不会保存。</p>
      )}
      {task.status === "recovery" && (
        <p className={styles.notice}>上次处理被中断，后台正在恢复任务。</p>
      )}
      {isVideoTaskActive(task) && (
        <p className={styles.hint}>{activeMediaHint("video")}</p>
      )}
      <p className={styles.hint}>{RETIRED_HINT}</p>
    </section>
  );
}

function VideoAssetError({
  error,
  onRetry,
}: {
  error: { status: number; message: string };
  onRetry: () => void;
}) {
  if (error.status === 404 || error.status === 403) {
    return (
      <p
        className={`${styles.notice} ${styles.noticeError}`}
        role="alert"
        data-testid="video-media-asset-error"
      >
        视频资产已删除或当前账户无权访问；未显示预览或下载链接。
      </p>
    );
  }
  return (
    <div data-testid="video-media-asset-error">
      <p className={`${styles.notice} ${styles.noticeError}`} role="alert">
        暂时无法读取视频资产{error.status ? `（HTTP ${error.status}）` : ""}。
      </p>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.actionButton}
          data-testid="video-media-retry"
          onClick={onRetry}
        >
          <Icon name="retry" size={14} aria-hidden /> 重试读取
        </button>
      </div>
    </div>
  );
}
