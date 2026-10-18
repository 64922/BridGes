"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/design-system/Icon";
import {
  getImageAsset,
  getImageTask,
  imageVersionUrl,
  type ImageAssetProjection,
  type ImageTaskProjection,
  type ImageVersionProjection,
} from "@/lib/api";
import { useApiQuery } from "@/lib/data";
import {
  IMAGE_STATUS_LABELS,
  activeMediaHint,
  isImageTaskActive,
} from "@/lib/media-status";

import styles from "./MediaResult.module.css";

const POLL_INTERVAL_MS = 5000;
const DEFAULT_IMAGE_MODEL = "qwen-image-2.0-pro-2026-06-22";
const RETIRED_HINT = "图片生成已退役，历史任务只能查看。";
const RETIRED_SUCCESS_HINT = "图片生成已退役，历史结果仍可查看与下载。";

export function currentImageVersion(
  asset: ImageAssetProjection,
  resultVersionId?: string | null
): ImageVersionProjection | undefined {
  const versions = asset.versions ?? [];
  return (
    versions.find((version) => version.version_id === resultVersionId) ??
    versions.find((version) => version.version_id === asset.current_version_id) ??
    versions[versions.length - 1]
  );
}

/**
 * 正文旁的图片结果：状态、真实资产与说明属于同一条消息。
 *
 * 任务在进行中时每 5 秒查询一次投影，终态停轮询；成功也只把已经
 * 通过账户授权读取的资产渲染为缩略图与下载入口，资产不可访问时
 * 明确说明，不显示假缩略图或假链接。完整版本与替代文本在右侧面板查看。
 */
export function ImageMedia({
  conversationId,
  task: initialTask,
  onRefreshMessages,
  onOpenPreview,
}: {
  conversationId: string;
  task: ImageTaskProjection;
  onRefreshMessages?: () => void;
  onOpenPreview?: (trigger: HTMLButtonElement) => void;
}) {
  const { data: polled } = useApiQuery(
    `image-task:${conversationId}:${initialTask.task_id}`,
    () => getImageTask(conversationId, initialTask.task_id),
    {
      pollMs: POLL_INTERVAL_MS,
      stopWhen: (latest) => !isImageTaskActive(latest),
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
    `image-asset:${conversationId}:${task.asset_id ?? "none"}`,
    () => getImageAsset(conversationId, task.asset_id as string),
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

  const [brokenVersionId, setBrokenVersionId] = useState<string | null>(null);
  const [previewToken, setPreviewToken] = useState(0);
  const version = asset ? currentImageVersion(asset, task.result_version_id) : undefined;

  if (task.deleted) {
    return (
      <section className={styles.media} data-testid="image-media-deleted">
        <p className={styles.notice}>
          <Icon name="imagePicture" size={16} aria-hidden /> 该图片已删除。
        </p>
        <p className={styles.hint}>
          历史说明保留；缩略图与下载入口已移除，也不会读取旧缓存。
        </p>
      </section>
    );
  }

  if (task.status === "succeeded") {
    return (
      <section className={styles.media} data-testid="image-media" data-status="succeeded">
        <div className={styles.statusRow}>
          <span
            className={styles.statusChip}
            data-status="succeeded"
            role="status"
            aria-live="polite"
            data-testid="image-media-status"
          >
            {IMAGE_STATUS_LABELS.succeeded}
          </span>
          <span className={styles.meta}>图片生成 · 历史只读</span>
        </div>

        {!task.asset_id ? (
          <p
            className={`${styles.notice} ${styles.noticeError}`}
            role="alert"
            data-testid="image-media-unavailable"
          >
            任务显示成功，但结果资产不可用；未显示缩略图或下载链接。
          </p>
        ) : assetLoading ? (
          <p className={styles.statusLine} role="status" data-testid="image-media-asset-loading">
            <span className="bg-spinner" aria-hidden="true" />
            正在核对图片资产…
          </p>
        ) : assetError ? (
          <ImageAssetError error={assetError} onRetry={() => void reloadAsset()} />
        ) : asset ? (
          version ? (
            <>
              {brokenVersionId === version.version_id ? (
                <div
                  className={`${styles.notice} ${styles.noticeError}`}
                  role="alert"
                  data-testid="image-media-preview-error"
                >
                  <p className={styles.notice}>
                    图片无法显示，文件可能已损坏或不可读取；上方说明仍保留。
                  </p>
                  <div className={styles.actions}>
                    <button
                      type="button"
                      className={styles.actionButton}
                      data-testid="image-media-preview-retry"
                      onClick={() => {
                        setBrokenVersionId(null);
                        setPreviewToken((value) => value + 1);
                      }}
                    >
                      <Icon name="retry" size={14} aria-hidden /> 重新显示
                    </button>
                    <a
                      className={styles.actionLink}
                      href={imageVersionUrl(conversationId, asset.asset_id, version.version_id, true)}
                      download
                      data-testid="image-media-download"
                    >
                      <Icon name="download" size={14} aria-hidden /> 下载当前版本
                    </a>
                  </div>
                </div>
              ) : (
                <div className={styles.previewWrap}>
                  {onOpenPreview ? (
                    <button
                      type="button"
                      className={styles.imageButton}
                      aria-label="查看图片详情"
                      title="查看图片详情"
                      data-testid="image-media-open"
                      onClick={(event) => onOpenPreview(event.currentTarget)}
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        key={`${version.version_id}:${previewToken}`}
                        className={styles.image}
                        src={imageVersionUrl(conversationId, asset.asset_id, version.version_id)}
                        alt={asset.alt_text || "生成的图片"}
                        loading="lazy"
                        data-testid="image-media-preview"
                        onError={() => setBrokenVersionId(version.version_id)}
                      />
                    </button>
                  ) : (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img
                      key={`${version.version_id}:${previewToken}`}
                      className={styles.image}
                      src={imageVersionUrl(conversationId, asset.asset_id, version.version_id)}
                      alt={asset.alt_text || "生成的图片"}
                      loading="lazy"
                      data-testid="image-media-preview"
                      onError={() => setBrokenVersionId(version.version_id)}
                    />
                  )}
                </div>
              )}

              {asset.alt_text && (
                <p className={styles.caption} data-testid="image-media-alt-text">
                  {asset.alt_text}
                </p>
              )}
              <p className={styles.caption} data-testid="image-media-prompt">
                生成要求：{task.prompt}
              </p>
              {brokenVersionId !== version.version_id && (
                <div className={styles.actions}>
                  {onOpenPreview && (
                    <button
                      type="button"
                      className={styles.actionButton}
                      data-testid="image-media-details"
                      onClick={(event) => onOpenPreview(event.currentTarget)}
                    >
                      <Icon name="imagePicture" size={14} aria-hidden /> 查看版本与说明
                    </button>
                  )}
                  <a
                    className={styles.actionLink}
                    href={imageVersionUrl(conversationId, asset.asset_id, version.version_id, true)}
                    download
                    data-testid="image-media-download"
                  >
                    <Icon name="download" size={14} aria-hidden /> 下载当前版本
                  </a>
                </div>
              )}
            </>
          ) : (
            <p
              className={`${styles.notice} ${styles.noticeError}`}
              role="alert"
              data-testid="image-media-unavailable"
            >
              图片资产没有可读取的版本；未显示缩略图或下载链接。
            </p>
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
      data-testid="image-media"
      data-status={task.status}
      aria-busy={isImageTaskActive(task)}
    >
      <div className={styles.statusRow}>
        <span
          className={styles.statusChip}
          data-status={task.status}
          role={failed ? "alert" : "status"}
          aria-live="polite"
          data-testid="image-media-status"
        >
          {IMAGE_STATUS_LABELS[task.status]}
        </span>
        <span className={styles.meta}>
          图片生成 · {task.model_id || DEFAULT_IMAGE_MODEL}
        </span>
      </div>
      <p className={styles.caption} data-testid="image-media-prompt">
        生成要求：{task.prompt}
      </p>
      {failed && (
        <p
          className={`${styles.notice} ${styles.noticeError}`}
          role="alert"
          data-testid="image-media-error"
        >
          {task.error_message || "图片生成失败。"}
        </p>
      )}
      {cancelled && (
        <p className={styles.notice} data-testid="image-media-stopped">
          任务已停止，迟到的云端结果不会保存。
        </p>
      )}
      {task.status === "recovery" && (
        <p className={styles.notice}>上次处理被中断，后台正在恢复任务。</p>
      )}
      {isImageTaskActive(task) && (
        <p className={styles.hint}>{activeMediaHint("image")}</p>
      )}
      <p className={styles.hint}>{RETIRED_HINT}</p>
    </section>
  );
}

function ImageAssetError({
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
        data-testid="image-media-asset-error"
      >
        图片资产已删除或当前账户无权访问；未显示缩略图或下载链接。
      </p>
    );
  }
  return (
    <div data-testid="image-media-asset-error">
      <p className={`${styles.notice} ${styles.noticeError}`} role="alert">
        暂时无法读取图片资产{error.status ? `（HTTP ${error.status}）` : ""}。
      </p>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.actionButton}
          data-testid="image-media-retry"
          onClick={onRetry}
        >
          <Icon name="retry" size={14} aria-hidden /> 重试读取
        </button>
      </div>
    </div>
  );
}
