"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/design-system/Icon";
import {
  ApiError,
  fetchImageVersionContent,
  fetchVideoContent,
  getImageAsset,
  getVideoAsset,
  imageVersionUrl,
  videoUrl,
  type ImageVersionProjection,
} from "@/lib/api";
import { mediaSelectionKey, type ChatMediaSelection } from "@/lib/chat-media";
import { useApiQuery } from "@/lib/data";

import styles from "./MediaViewerPanel.module.css";

type ContentState =
  | { kind: "loading" }
  | { kind: "ready"; objectUrl: string }
  | { kind: "error"; status: number; message: string };

function isAccessError(status: number): boolean {
  return status === 404 || status === 403;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function errorStatus(error: unknown): number {
  return error instanceof ApiError ? error.status : 0;
}

function MediaPanelError({
  status,
  message,
  onRetry,
}: {
  status: number;
  message: string;
  onRetry: () => void;
}) {
  if (isAccessError(status)) {
    return (
      <div className={styles.error} role="alert" data-testid="media-viewer-error">
        <span>该媒体已删除或当前账户无权访问；不会读取旧缓存或显示下载入口。</span>
      </div>
    );
  }
  return (
    <div className={styles.error} role="alert" data-testid="media-viewer-error">
      <span>{message}</span>
      <button
        type="button"
        className={styles.retryButton}
        data-testid="media-viewer-retry"
        onClick={onRetry}
      >
        <Icon name="retry" size={14} aria-hidden /> 重试
      </button>
    </div>
  );
}

function ImagePanelBody({
  conversationId,
  selection,
}: {
  conversationId: string;
  selection: ChatMediaSelection;
}) {
  const {
    data: asset,
    error: assetError,
    loading: assetLoading,
    reload: reloadAsset,
  } = useApiQuery(
    `media-image-asset:${conversationId}:${selection.assetId}`,
    () => getImageAsset(conversationId, selection.assetId)
  );

  const versions = asset?.versions ?? [];
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(
    selection.resultVersionId
  );
  const selectedVersion: ImageVersionProjection | undefined =
    versions.find((version) => version.version_id === selectedVersionId) ??
    versions.find((version) => version.version_id === asset?.current_version_id) ??
    versions[versions.length - 1];

  const [content, setContent] = useState<ContentState>({ kind: "loading" });
  const [retryToken, setRetryToken] = useState(0);
  const activeVersionId = selectedVersion?.version_id;
  useEffect(() => {
    if (!activeVersionId) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setContent({ kind: "loading" });
    const load = async () => {
      try {
        const response = await fetchImageVersionContent(
          conversationId,
          selection.assetId,
          activeVersionId,
          controller.signal
        );
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setContent({ kind: "ready", objectUrl });
      } catch (error) {
        if (controller.signal.aborted) return;
        setContent({
          kind: "error",
          status: errorStatus(error),
          message: errorMessage(error, "图片读取失败，请稍后重试。"),
        });
      }
    };
    void load();
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [
    activeVersionId,
    conversationId,
    retryToken,
    selection.assetId,
  ]);

  if (assetLoading) {
    return (
      <p className={styles.statusLine} role="status" data-testid="media-viewer-loading">
        <span className="bg-spinner" aria-hidden="true" />
        正在读取图片资产…
      </p>
    );
  }
  if (assetError) {
    return (
      <MediaPanelError
        status={assetError.status}
        message={assetError.message}
        onRetry={() => void reloadAsset()}
      />
    );
  }
  if (!asset) {
    return <p className={styles.note}>图片资产暂时不可用。</p>;
  }
  if (!selectedVersion) {
    return (
      <p className={styles.note} data-testid="media-viewer-unavailable">
        该图片资产没有可读取的版本。
      </p>
    );
  }

  const accessDenied = content.kind === "error" && isAccessError(content.status);
  return (
    <div className={styles.preview}>
      <p className={styles.meta}>
        图片生成 · {versions.length} 个版本 · 当前显示 {selectedVersion.kind === "edit" ? "编辑" : "生成"}版本
      </p>

      {versions.length > 1 && (
        <div data-testid="media-viewer-versions">
          <p className={styles.sectionTitle}>版本记录</p>
          <div className={styles.versionRow} role="group" aria-label="图片版本">
            {versions.map((version, index) => (
              <button
                key={version.version_id}
                type="button"
                className={styles.versionButton}
                aria-pressed={version.version_id === selectedVersion.version_id}
                data-testid="media-viewer-version-button"
                onClick={() => setSelectedVersionId(version.version_id)}
              >
                v{index + 1} · {version.kind === "edit" ? "编辑" : "生成"}
              </button>
            ))}
          </div>
        </div>
      )}

      {content.kind === "loading" ? (
        <p className={styles.statusLine} role="status" data-testid="media-viewer-loading">
          <span className="bg-spinner" aria-hidden="true" />
          正在读取图片…
        </p>
      ) : content.kind === "error" ? (
        <div>
          <MediaPanelError
            status={content.status}
            message={content.message}
            onRetry={() => setRetryToken((value) => value + 1)}
          />
        </div>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          className={styles.image}
          src={content.objectUrl}
          alt={asset.alt_text || "生成的图片"}
          data-testid="media-viewer-image"
          onError={() =>
            setContent({
              kind: "error",
              status: 0,
              message: "图片无法显示，请下载当前版本后查看。",
            })
          }
        />
      )}

      <div>
        <p className={styles.sectionTitle}>替代文本</p>
        <p className={styles.note} data-testid="media-viewer-alt-text">
          {asset.alt_text || "（空）"}
        </p>
      </div>

      <div>
        <p className={styles.sectionTitle}>生成要求</p>
        <p className={styles.caption}>{selectedVersion.prompt}</p>
      </div>

      <p className={styles.meta}>
        {selectedVersion.model_id ? `${selectedVersion.model_id} · ` : ""}
        创建于 {selectedVersion.created_at.slice(0, 16).replace("T", " ")}
      </p>

      {!accessDenied && (
        <div className={styles.downloadRow}>
          <a
            className={styles.downloadLink}
            href={imageVersionUrl(
              conversationId,
              selection.assetId,
              selectedVersion.version_id,
              true
            )}
            download
            data-testid="media-viewer-download"
          >
            <Icon name="download" size={15} aria-hidden /> 下载当前版本
          </a>
        </div>
      )}
    </div>
  );
}

function VideoPanelBody({
  conversationId,
  selection,
}: {
  conversationId: string;
  selection: ChatMediaSelection;
}) {
  const {
    data: asset,
    error: assetError,
    loading: assetLoading,
    reload: reloadAsset,
  } = useApiQuery(
    `media-video-asset:${conversationId}:${selection.assetId}`,
    () => getVideoAsset(conversationId, selection.assetId)
  );
  const [content, setContent] = useState<ContentState>({ kind: "loading" });
  const [retryToken, setRetryToken] = useState(0);
  const videoRef = useRef<HTMLVideoElement>(null);
  const assetReady = Boolean(asset && !asset.deleted && asset.object_id);

  // 播放器在内容就绪时被捕获；关闭、切换或卸载时显式暂停，再释放 object URL。
  useEffect(() => {
    if (content.kind !== "ready") return;
    const video = videoRef.current;
    return () => {
      video?.pause();
    };
  }, [content]);

  useEffect(() => {
    if (!assetReady) {
      setContent({ kind: "loading" });
      return;
    }
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setContent({ kind: "loading" });
    const load = async () => {
      try {
        const response = await fetchVideoContent(
          conversationId,
          selection.assetId,
          controller.signal
        );
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setContent({ kind: "ready", objectUrl });
      } catch (error) {
        if (controller.signal.aborted) return;
        setContent({
          kind: "error",
          status: errorStatus(error),
          message: errorMessage(error, "视频读取失败，请稍后重试。"),
        });
      }
    };
    void load();
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [assetReady, conversationId, retryToken, selection.assetId]);

  if (assetLoading) {
    return (
      <p className={styles.statusLine} role="status" data-testid="media-viewer-loading">
        <span className="bg-spinner" aria-hidden="true" />
        正在读取视频资产…
      </p>
    );
  }
  if (assetError) {
    return (
      <MediaPanelError
        status={assetError.status}
        message={assetError.message}
        onRetry={() => void reloadAsset()}
      />
    );
  }
  if (!asset) {
    return <p className={styles.note}>视频资产暂时不可用。</p>;
  }
  if (asset.deleted || !asset.object_id) {
    return (
      <p className={styles.note} data-testid="media-viewer-unavailable">
        该视频资产已删除或不可访问。
      </p>
    );
  }

  const accessDenied = content.kind === "error" && isAccessError(content.status);
  return (
    <div className={styles.preview}>
      <p className={styles.meta}>视频生成 · 无版本链</p>

      {content.kind === "loading" ? (
        <p className={styles.statusLine} role="status" data-testid="media-viewer-loading">
          <span className="bg-spinner" aria-hidden="true" />
          正在读取视频…
        </p>
      ) : content.kind === "error" ? (
        <div>
          <MediaPanelError
            status={content.status}
            message={content.message}
            onRetry={() => setRetryToken((value) => value + 1)}
          />
        </div>
      ) : (
        <video
          ref={videoRef}
          className={styles.video}
          src={content.objectUrl}
          controls
          autoPlay={false}
          preload="metadata"
          aria-label={asset.description || "生成的视频"}
          data-testid="media-viewer-video"
          onError={() =>
            setContent({
              kind: "error",
              status: 0,
              message: "视频无法播放，请下载后查看。",
            })
          }
        />
      )}

      <div>
        <p className={styles.sectionTitle}>可访问文字说明</p>
        <p className={styles.note} data-testid="media-viewer-description">
          {asset.description || "（空）"}
        </p>
      </div>

      <div>
        <p className={styles.sectionTitle}>生成要求</p>
        <p className={styles.caption}>{asset.prompt}</p>
      </div>

      <div>
        <p className={styles.sectionTitle}>追溯信息</p>
        <p className={styles.note}>
          {asset.model_id ?? "模型快照未记录"} · 创建于{" "}
          {asset.created_at.slice(0, 16).replace("T", " ")}
        </p>
        {asset.cloud_task_id && (
          <p className={`${styles.note} ${styles.mono}`}>
            供应商任务 {asset.cloud_task_id}
          </p>
        )}
      </div>

      {!accessDenied && (
        <div className={styles.downloadRow}>
          <a
            className={styles.downloadLink}
            href={videoUrl(conversationId, selection.assetId, true)}
            download
            data-testid="media-viewer-download"
          >
            <Icon name="download" size={15} aria-hidden /> 下载视频
          </a>
        </div>
      )}
    </div>
  );
}

function MediaViewerContent({
  conversationId,
  selection,
  onClose,
}: {
  conversationId: string;
  selection: ChatMediaSelection;
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    panelRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.isComposing) return;
      event.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <aside
      ref={panelRef}
      id="media-viewer"
      role="dialog"
      aria-modal="false"
      tabIndex={-1}
      aria-labelledby="media-viewer-title"
      className={styles.panel}
      data-testid="media-viewer"
    >
      <header className={styles.header}>
        <div className={styles.titleGroup}>
          <span className={styles.kicker}>媒体</span>
          <h2 id="media-viewer-title" className={styles.title}>
            {selection.kind === "image" ? "图片详情" : "视频详情"}
          </h2>
        </div>
        <button
          type="button"
          aria-label="关闭媒体预览"
          className={styles.closeButton}
          data-testid="media-viewer-close"
          onClick={onClose}
        >
          <Icon name="close" size={18} aria-hidden />
        </button>
      </header>
      <div className={styles.body}>
        {selection.kind === "image" ? (
          <ImagePanelBody conversationId={conversationId} selection={selection} />
        ) : (
          <VideoPanelBody conversationId={conversationId} selection={selection} />
        )}
      </div>
    </aside>
  );
}

/**
 * 右侧媒体详情面板：默认不渲染，只在用户主动打开时读取资产与字节。
 * 选择身份按 conversation / message / task / asset 固定；切换选择、关闭、
 * 切换会话或账户卸载都会中止读取并释放 object URL 与播放器。
 */
export function MediaViewerPanel({
  conversationId,
  selection,
  onClose,
}: {
  conversationId: string;
  selection: ChatMediaSelection | null;
  onClose: () => void;
}) {
  if (!selection || selection.conversationId !== conversationId) return null;
  return (
    <MediaViewerContent
      key={mediaSelectionKey(selection)}
      conversationId={conversationId}
      selection={selection}
      onClose={onClose}
    />
  );
}
