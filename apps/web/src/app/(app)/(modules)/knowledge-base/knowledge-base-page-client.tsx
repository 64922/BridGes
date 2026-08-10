"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { IngestionStatusChip } from "@/components/bridges/AttachmentIngestion";
import { Dialog } from "@/components/bridges/Dialog";
import { Menu, type MenuItem } from "@/components/bridges/Menu";
import { StateBlock } from "@/components/bridges/StateBlock";
import { Button } from "@/components/design-system/Button";
import { Icon, type IconName } from "@/components/design-system/Icon";
import { MainContent } from "@/components/layout/MainContent";
import {
  deleteKnowledgeBaseMaterial,
  downloadKnowledgeBaseMaterial,
  listKnowledgeBaseMaterials,
  rebuildKnowledgeBaseMaterial,
  retryKnowledgeBaseMaterial,
  uploadKnowledgeBaseMaterial,
  type KnowledgeBaseMaterialProjection,
} from "@/lib/api";
import { useApiQuery } from "@/lib/data";

const MAX_MATERIAL_BYTES = 10 * 1024 * 1024;
const ACCEPT_ATTRIBUTE = ".pdf,.docx,.txt,.md,.markdown,.png,.jpg,.jpeg,.gif,.webp";
const SUPPORTED_EXTENSION = /\.(pdf|docx|txt|md|markdown|png|jpe?g|gif|webp)$/i;
const POLL_INTERVAL_MS = 2500;

type StatusFilter = "all" | "processing" | "ready" | "failed";

const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "processing", label: "处理中" },
  { key: "ready", label: "已就绪" },
  { key: "failed", label: "失败" },
];

/** 状态筛选分桶：error → 失败；ready/empty → 已就绪；其余（含恢复中、重建中）→ 处理中。 */
function statusBucket(material: KnowledgeBaseMaterialProjection): Exclude<StatusFilter, "all"> {
  if (material.status === "error") return "failed";
  if (material.status === "ready" || material.status === "empty") return "ready";
  return "processing";
}

function newId(prefix: string): string {
  return `${prefix}-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatAbsoluteTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("zh-CN", { hour12: false });
}

/** 相对时间（7 天内），绝对时间通过 title 属性完整呈现。 */
function formatRelativeTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 60_000) return "刚刚";
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)} 分钟前`;
  if (diffMs < 86_400_000) return `${Math.floor(diffMs / 3_600_000)} 小时前`;
  if (diffMs < 7 * 86_400_000) return `${Math.floor(diffMs / 86_400_000)} 天前`;
  return formatAbsoluteTime(iso);
}

function materialIcon(material: KnowledgeBaseMaterialProjection): IconName {
  return material.media_type.startsWith("image/") ? "uploadImage" : "uploadFile";
}

/** 媒体类型的中文短名（列表行内联展示，未知类型回退为原始值）。 */
function mediaTypeLabel(material: KnowledgeBaseMaterialProjection): string {
  const mediaType = material.media_type;
  if (mediaType === "application/pdf") return "PDF 文档";
  if (
    mediaType === "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  ) {
    return "Word 文档";
  }
  if (mediaType === "text/markdown" || /\.(md|markdown)$/i.test(material.filename)) {
    return "Markdown";
  }
  if (mediaType === "text/plain") return "文本";
  if (mediaType.startsWith("image/")) return "图片";
  return mediaType;
}

/** 向量降级：材料本身已就绪，但本文档向量未建成（GQ-05 后按文档级信号判断）。 */
function isVectorDegraded(material: KnowledgeBaseMaterialProjection): boolean {
  return material.status === "ready" && !material.vector_indexed;
}

// ---------------------------------------------------------------------------
// 逐阶段状态（详情对话框）
// ---------------------------------------------------------------------------

type StageState = "done" | "failed" | "pending" | "unavailable";

interface StageStates {
  upload: StageState;
  parse: StageState;
  fulltext: StageState;
  vector: StageState;
}

/** 由摄取状态与失败阶段推导四个处理阶段的呈现状态。 */
function deriveStageStates(material: KnowledgeBaseMaterialProjection): StageStates {
  if (material.status === "ready" || material.status === "empty") {
    return {
      upload: "done",
      parse: "done",
      fulltext: "done",
      // GQ-05：就绪但向量缺失 = 向量化降级/不可用（不再以全局可用性
      // 当作「处理中 pending」）。
      vector: material.vector_indexed ? "done" : "unavailable",
    };
  }
  if (material.status === "error") {
    const stage = material.failure_stage ?? null;
    const parseFailed = stage === null || stage === "read" || stage === "parse";
    const fulltextFailed = stage === "chunk" || stage === "index";
    const vectorFailed = stage === "embed";
    return {
      upload: "done",
      parse: parseFailed ? "failed" : "done",
      fulltext: parseFailed ? "pending" : fulltextFailed ? "failed" : "done",
      vector: parseFailed || fulltextFailed ? "pending" : vectorFailed ? "failed" : "done",
    };
  }
  return { upload: "done", parse: "pending", fulltext: "pending", vector: "pending" };
}

const STAGE_STATE_CONFIG: Record<StageState, { label: string; icon: IconName; color: string }> = {
  done: { label: "成功", icon: "check", color: "var(--color-status-success)" },
  failed: { label: "失败", icon: "alert", color: "var(--color-status-error)" },
  pending: { label: "等待中", icon: "info", color: "var(--color-text-tertiary)" },
  unavailable: { label: "不可用", icon: "alert", color: "var(--color-status-wait)" },
};

const FAILURE_STAGE_LABELS: Record<string, string> = {
  read: "读取",
  parse: "解析",
  chunk: "分块",
  embed: "向量嵌入",
  index: "索引写入",
};

function StageChip({ state }: { state: StageState }) {
  const config = STAGE_STATE_CONFIG[state];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "var(--space-1)",
        fontSize: "var(--text-xs)",
        fontWeight: 600,
        color: config.color,
      }}
    >
      <Icon name={config.icon} size={13} aria-hidden />
      {config.label}
    </span>
  );
}

function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "baseline",
        gap: "var(--space-4)",
        fontSize: "var(--text-sm)",
      }}
    >
      <span style={{ color: "var(--color-text-tertiary)", flexShrink: 0 }}>{label}</span>
      <span
        style={{
          color: "var(--color-text-primary)",
          textAlign: "right",
          minWidth: 0,
          overflowWrap: "anywhere",
        }}
      >
        {children}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 上传中的临时条目
// ---------------------------------------------------------------------------

interface PendingUpload {
  id: string;
  filename: string;
  progress: number;
  status: "uploading" | "error";
  error?: string;
}

type DialogState =
  | { kind: "detail" | "delete" | "rebuild"; material: KnowledgeBaseMaterialProjection }
  | null;

/**
 * 删除/重建共用的确认对话框：说明文案、可恢复错误（409 等，role="alert"）、
 * 取消/确认按钮。两种操作仅文案、确认按钮文案与色调不同。
 */
function ConfirmActionDialog({
  title,
  explanation,
  error,
  confirmLabel,
  confirmVariant,
  busy,
  onConfirm,
  onClose,
}: {
  title: string;
  explanation: string;
  error: string;
  confirmLabel: string;
  confirmVariant: "primary" | "danger";
  busy: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Dialog open onClose={onClose} title={title}>
      <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
        {explanation}
      </p>
      {error && (
        <p
          role="alert"
          data-testid="kb-dialog-error"
          style={{
            marginTop: "var(--space-3)",
            padding: "var(--space-3)",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--color-status-error)",
            backgroundColor: "var(--color-status-error-bg)",
            color: "var(--color-status-error)",
            fontSize: "var(--text-sm)",
          }}
        >
          {error}
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
        <Button variant="secondary" size="sm" onClick={onClose}>
          取消
        </Button>
        <Button variant={confirmVariant} size="sm" isLoading={busy} onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </div>
    </Dialog>
  );
}

/**
 * 全局知识库桌面页（Issue 18）。
 *
 * 真实闭环：上传（XHR 进度）→ 轮询处理状态 → 列表/筛选/详情/下载/重试/
 * 重建索引/删除。页面级状态走 StateBlock，行级状态走 IngestionStatusChip；
 * 向量降级同时有页面级横幅与行级徽标，绝不把降级的知识库整体呈现为可用。
 */
export default function KnowledgeBasePageClient() {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [searchText, setSearchText] = useState("");
  const [pendingUploads, setPendingUploads] = useState<PendingUpload[]>([]);
  const [actionError, setActionError] = useState("");
  const [dialog, setDialog] = useState<DialogState>(null);
  // Issue 24：经 URL（?material=&page=&section=）打开详情时展示的页码/章节锚点说明
  const [detailAnchor, setDetailAnchor] = useState<{
    page: number | null;
    section: string | null;
  } | null>(null);
  const [dialogError, setDialogError] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadControllersRef = useRef(new Map<string, AbortController>());
  // Issue 24：?material= URL 定位只处理一次（避免轮询刷新重复打开对话框）
  const urlAnchorHandledRef = useRef(false);

  // 列表加载 + 轮询：处理中（等待/解析/恢复）或索引重建中时每 2.5 秒刷新，
  // 全部稳定后自动停止；轮询失败静默保留现有列表（useApiQuery 统一生命周期）。
  const {
    data: materials,
    error: queryError,
    loading,
    reload,
  } = useApiQuery("knowledge-base", () => listKnowledgeBaseMaterials(), {
    pollMs: POLL_INTERVAL_MS,
    stopWhen: (list) =>
      !list.some(
        (material) =>
          ["queued", "processing", "recovery"].includes(material.status) ||
          material.index_rebuilding
      ),
  });

  useEffect(() => {
    const controllers = uploadControllersRef.current;
    return () => {
      controllers.forEach((controller) => controller.abort());
      controllers.clear();
    };
  }, []);

  // Issue 24：统一搜索的文档跳转定位。?material=<object_id> 自动打开该
  // 材料的详情对话框；带 &page=N 时在详情内展示页码锚点说明（详情不含
  // 正文分块，锚点信息保证定位可见）。材料缺失时给出中文提示而非静默。
  useEffect(() => {
    if (urlAnchorHandledRef.current || materials === null) return;
    urlAnchorHandledRef.current = true;
    const params = new URLSearchParams(window.location.search);
    const objectId = params.get("material");
    if (!objectId) return;
    const target = materials.find((item) => item.object_id === objectId);
    if (!target) {
      setActionError("没有找到对应的材料，可能已被删除或属于其他账户。");
      return;
    }
    const pageParam = Number.parseInt(params.get("page") ?? "", 10);
    setDetailAnchor({
      page: Number.isNaN(pageParam) ? null : pageParam,
      section: params.get("section"),
    });
    setDialog({ kind: "detail", material: target });
  }, [materials]);

  // -------------------------------------------------------------------------
  // 上传
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
      await uploadKnowledgeBaseMaterial(
        file,
        entry.id,
        (loaded, total) =>
          updatePendingUpload(entry.id, {
            progress: total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0,
          }),
        controller.signal
      );
      // 上传成功后移除临时条目并刷新列表；材料以服务端投影为准（通常进入 queued）。
      uploadControllersRef.current.delete(entry.id);
      setPendingUploads((current) => current.filter((item) => item.id !== entry.id));
      reload();
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

  const onFilesSelected = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setActionError("");
    const entries: { entry: PendingUpload; file: File | null }[] = Array.from(files).map((file) => {
      const id = newId("kb-upload");
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
      if (file.size === 0 || file.size > MAX_MATERIAL_BYTES) {
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
      return {
        entry: { id, filename: file.name, progress: 0, status: "uploading" as const },
        file,
      };
    });
    setPendingUploads((current) => [...current, ...entries.map((item) => item.entry)]);
    entries.forEach(({ entry, file }) => {
      if (file) void startUpload(entry, file);
    });
  };

  const openFilePicker = () => fileInputRef.current?.click();

  // -------------------------------------------------------------------------
  // 行操作
  // -------------------------------------------------------------------------

  const runAction = async (key: string, action: () => Promise<void>) => {
    setBusyAction(key);
    setActionError("");
    setDialogError("");
    try {
      await action();
    } catch (error) {
      setActionError(errorMessage(error, "操作失败，请稍后重试。"));
    } finally {
      setBusyAction(null);
    }
  };

  const doRetry = (material: KnowledgeBaseMaterialProjection) =>
    runAction(`retry:${material.object_id}`, async () => {
      const updated = await retryKnowledgeBaseMaterial(material.object_id);
      setDialog((current) =>
        current && current.material.object_id === updated.object_id
          ? { ...current, material: updated }
          : current
      );
      await reload();
    });

  const doDownload = (material: KnowledgeBaseMaterialProjection) =>
    runAction(`download:${material.object_id}`, async () => {
      await downloadKnowledgeBaseMaterial(material.object_id, material.filename);
    });

  const doDelete = async (material: KnowledgeBaseMaterialProjection) => {
    setBusyAction(`delete:${material.object_id}`);
    setDialogError("");
    try {
      await deleteKnowledgeBaseMaterial(material.object_id);
      setDialog(null);
      await reload();
    } catch (error) {
      // 409 material_processing 等可恢复错误：对话框内展示中文原因，材料保留在列表中。
      setDialogError(errorMessage(error, "删除失败，请稍后重试。"));
    } finally {
      setBusyAction(null);
    }
  };

  const doRebuild = async (material: KnowledgeBaseMaterialProjection) => {
    setBusyAction(`rebuild:${material.object_id}`);
    setDialogError("");
    try {
      await rebuildKnowledgeBaseMaterial(material.object_id);
      setDialog(null);
      await reload();
    } catch (error) {
      setDialogError(errorMessage(error, "重建索引失败，请稍后重试。"));
    } finally {
      setBusyAction(null);
    }
  };

  const openDialog = (kind: "detail" | "delete" | "rebuild", material: KnowledgeBaseMaterialProjection) => {
    setDialogError("");
    setDetailAnchor(null);
    setDialog({ kind, material });
  };

  // 对话框中的材料跟随轮询刷新（保留打开期间的最新投影）。
  const liveDialogMaterial = dialog
    ? (materials ?? []).find((item) => item.object_id === dialog.material.object_id) ??
      dialog.material
    : null;

  // -------------------------------------------------------------------------
  // 筛选（上传/轮询刷新不会重置筛选状态）
  // -------------------------------------------------------------------------

  const filteredMaterials = useMemo(() => {
    const keyword = searchText.trim().toLowerCase();
    return (materials ?? []).filter((material) => {
      if (statusFilter !== "all" && statusBucket(material) !== statusFilter) return false;
      if (keyword && !material.filename.toLowerCase().includes(keyword)) return false;
      return true;
    });
  }, [materials, statusFilter, searchText]);

  const filtering = statusFilter !== "all" || searchText.trim().length > 0;

  const degradedMaterial = (materials ?? []).find(isVectorDegraded);

  const clearFilters = () => {
    setStatusFilter("all");
    setSearchText("");
  };

  // -------------------------------------------------------------------------
  // 渲染
  // -------------------------------------------------------------------------

  const renderRowMenu = (material: KnowledgeBaseMaterialProjection) => {
    const items: MenuItem[] = [
      { label: "查看详情", icon: "paperSearch", onSelect: () => openDialog("detail", material) },
      { label: "下载", icon: "download", onSelect: () => void doDownload(material) },
    ];
    if (material.status === "error" || material.status === "recovery") {
      items.push({ label: "重试", icon: "retry", onSelect: () => void doRetry(material) });
    }
    if (material.status === "ready" || material.status === "empty") {
      items.push({ label: "重建索引", icon: "settings", onSelect: () => openDialog("rebuild", material) });
    }
    items.push({
      label: "删除",
      icon: "trash",
      danger: true,
      onSelect: () => openDialog("delete", material),
    });
    return items;
  };

  const renderMaterialRow = (material: KnowledgeBaseMaterialProjection) => {
    const degraded = isVectorDegraded(material);
    return (
      <li
        key={material.object_id}
        data-testid="kb-material-row"
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
          <Icon name={materialIcon(material)} size={22} aria-hidden />
        </span>
        <div style={{ minWidth: 0, flex: 1, display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", minWidth: 0 }}>
            <span
              title={material.filename}
              style={{
                minWidth: 0,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                fontWeight: 500,
                color: "var(--color-text-primary)",
              }}
            >
              {material.filename}
            </span>
            <IngestionStatusChip status={material.status} />
            {degraded && (
              <span
                title={material.vector_unavailable_reason ?? "向量检索暂不可用"}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "var(--space-1)",
                  padding: "2px var(--space-2)",
                  borderRadius: "999px",
                  fontSize: "var(--text-xs)",
                  fontWeight: 600,
                  color: "var(--color-status-wait)",
                  backgroundColor: "var(--color-status-wait-bg)",
                  flexShrink: 0,
                }}
              >
                <Icon name="alert" size={13} aria-hidden />
                向量不可用
              </span>
            )}
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
            <span>{mediaTypeLabel(material)}</span>
            <span>{formatSize(material.content_length)}</span>
            <span>{material.source}</span>
            <code
              title={`完整哈希：${material.content_hash}`}
              style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)" }}
            >
              {material.content_hash_summary}
            </code>
            <span title={formatAbsoluteTime(material.updated_at)}>
              更新于 {formatRelativeTime(material.updated_at)}
            </span>
            {material.index_version_id && (
              <span title={`索引版本 ${material.index_version_id}`}>
                索引 {material.index_version_id.slice(0, 8)}
              </span>
            )}
            {material.usable_for_chat && <span>可用于对话</span>}
            {material.status === "error" && material.failure_reason && (
              <span role="alert" style={{ color: "var(--color-status-error)" }}>
                {FAILURE_STAGE_LABELS[material.failure_stage ?? ""] ?? "处理"}阶段失败：
                {material.failure_reason}
              </span>
            )}
            {material.index_rebuilding && <span>索引重建中，旧版本继续可用</span>}
          </div>
        </div>
        <div style={{ flexShrink: 0 }}>
          <Menu
            trigger={<Icon name="more" size={20} aria-hidden />}
            ariaLabel={`材料操作：${material.filename}`}
            items={renderRowMenu(material)}
          />
        </div>
      </li>
    );
  };

  const renderDetailDialog = (material: KnowledgeBaseMaterialProjection) => {
    const stages = deriveStageStates(material);
    const degraded = isVectorDegraded(material);
    const retrying = busyAction === `retry:${material.object_id}`;
    // 搜索定位锚点（页码与章节，与搜索结果行同一「第 N 页 · 章节」格式）
    const anchorText = detailAnchor
      ? [
          detailAnchor.page != null ? `第 ${detailAnchor.page} 页` : "",
          detailAnchor.section ?? "",
        ]
          .filter(Boolean)
          .join(" · ")
      : "";
    return (
      <Dialog
        open
        onClose={() => setDialog(null)}
        title={material.filename}
        description="材料的完整元数据与逐阶段处理状态。"
      >
        {anchorText && (
          <p
            data-testid="kb-detail-anchor"
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: "var(--space-2)",
              marginBottom: "var(--space-3)",
              padding: "var(--space-3)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-status-info)",
              backgroundColor: "var(--color-status-info-bg)",
              color: "var(--color-status-info)",
              fontSize: "var(--text-sm)",
            }}
          >
            <Icon name="info" size={18} aria-hidden />
            <span>
              来自搜索的定位锚点：{anchorText}（对应原文档位置）。
              详情不含正文分块，锚点用于在原文档中定位命中位置。
            </span>
          </p>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
          {material.title && <MetaRow label="标题">{material.title}</MetaRow>}
          <MetaRow label="类型">{material.media_type}</MetaRow>
          <MetaRow label="大小">{formatSize(material.content_length)}</MetaRow>
          <MetaRow label="上传时间">{formatAbsoluteTime(material.created_at)}</MetaRow>
          <MetaRow label="更新时间">{formatAbsoluteTime(material.updated_at)}</MetaRow>
          <MetaRow label="来源">{material.source}</MetaRow>
          <MetaRow label="内容哈希摘要">
            <code title={`完整哈希：${material.content_hash}`} style={{ fontFamily: "var(--font-mono)" }}>
              {material.content_hash_summary}
            </code>
          </MetaRow>
          <MetaRow label="索引版本">
            {material.index_version_id ? (
              <code title={material.index_version_id} style={{ fontFamily: "var(--font-mono)" }}>
                {material.index_version_id.slice(0, 8)}
              </code>
            ) : (
              "尚未建立索引"
            )}
          </MetaRow>
          <MetaRow label="分块数">{material.chunk_count}</MetaRow>
        </div>

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-2)",
            marginTop: "var(--space-4)",
            padding: "var(--space-3)",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--color-border)",
          }}
        >
          <MetaRow label="上传">
            <StageChip state={stages.upload} />
          </MetaRow>
          <MetaRow label="解析">
            <StageChip state={stages.parse} />
          </MetaRow>
          <MetaRow label="全文索引">
            <StageChip state={stages.fulltext} />
          </MetaRow>
          <MetaRow label="向量索引">
            <StageChip state={stages.vector} />
          </MetaRow>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", marginTop: "var(--space-4)" }}>
          <MetaRow label="可用于对话">
            {material.usable_for_chat ? (
              <StageChip state="done" />
            ) : (
              <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                暂不可用
              </span>
            )}
          </MetaRow>
          {material.status === "error" && (
            <>
              <MetaRow label="失败阶段">
                {FAILURE_STAGE_LABELS[material.failure_stage ?? ""] ?? "处理"}
              </MetaRow>
              {material.failure_reason && (
                <p role="alert" style={{ fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
                  {material.failure_reason}
                </p>
              )}
            </>
          )}
          {material.retry_count > 0 && <MetaRow label="重试次数">{material.retry_count}</MetaRow>}
          {degraded && (
            <p style={{ fontSize: "var(--text-sm)", color: "var(--color-status-wait)" }}>
              向量检索不可用
              {material.vector_unavailable_reason ? `：${material.vector_unavailable_reason}` : ""}
              。全文检索仍可使用，材料可在对话中被检索。
            </p>
          )}
          {material.index_rebuilding && (
            <p role="status" style={{ fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
              索引重建中，重建期间检索使用旧版本。
            </p>
          )}
        </div>

        {actionError && (
          <p role="alert" style={{ marginTop: "var(--space-3)", fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
            {actionError}
          </p>
        )}

        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "flex-end",
            gap: "var(--space-2)",
            marginTop: "var(--space-4)",
          }}
        >
          <Button variant="secondary" size="sm" onClick={() => void doDownload(material)}>
            下载
          </Button>
          {(material.status === "error" || material.status === "recovery") && (
            <Button
              variant="secondary"
              size="sm"
              isLoading={retrying}
              onClick={() => void doRetry(material)}
            >
              重试
            </Button>
          )}
          {(material.status === "ready" || material.status === "empty") && (
            <Button variant="secondary" size="sm" onClick={() => openDialog("rebuild", material)}>
              重建索引
            </Button>
          )}
          <Button variant="danger" size="sm" onClick={() => openDialog("delete", material)}>
            删除
          </Button>
        </div>
      </Dialog>
    );
  };

  return (
    <MainContent>
      <section
        aria-labelledby="knowledge-base-title"
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
            <h1 id="knowledge-base-title" className="sc-section-title">
              知识库
            </h1>
            <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
              全局材料仅当前账户可见，可在任何对话中检索。
            </p>
          </div>
          <Button data-testid="kb-upload-button" onClick={openFilePicker}>
            <Icon name="uploadFile" size={18} aria-hidden />
            上传材料
          </Button>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ACCEPT_ATTRIBUTE}
          data-testid="kb-file-input"
          aria-label="选择要上传的材料文件"
          style={{ display: "none" }}
          onChange={(event) => {
            onFilesSelected(event.target.files);
            event.target.value = "";
          }}
        />

        {degradedMaterial && (
          <div
            role="status"
            data-testid="kb-vector-banner"
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: "var(--space-2)",
              marginTop: "var(--space-4)",
              padding: "var(--space-3) var(--space-4)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-status-wait)",
              backgroundColor: "var(--color-status-wait-bg)",
              color: "var(--color-status-wait)",
              fontSize: "var(--text-sm)",
            }}
          >
            <Icon name="alert" size={18} aria-hidden />
            <span>
              向量检索当前不可用
              {degradedMaterial.vector_unavailable_reason
                ? `：${degradedMaterial.vector_unavailable_reason}`
                : ""}
              。全文检索不受影响，已就绪的材料仍可在对话中检索。
            </span>
          </div>
        )}

        {actionError && !dialog && (
          <p
            role="alert"
            data-testid="kb-action-error"
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

        {pendingUploads.length > 0 && (
          <ul
            role="list"
            aria-label="正在上传的材料"
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-2)",
              marginTop: "var(--space-4)",
            }}
          >
            {pendingUploads.map((entry) => (
              <li
                key={entry.id}
                data-testid="kb-upload-entry"
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

        <div style={{ marginTop: "var(--space-6)" }}>
          {loading ? (
            <StateBlock kind="loading" title="正在加载知识库材料" description="读取当前账户的全局材料列表。" />
          ) : queryError ? (
            <StateBlock
              kind={queryError.status === 403 ? "permission" : "error"}
              title={queryError.status === 403 ? "没有访问知识库的权限" : "知识库加载失败"}
              description={queryError.message}
              actionLabel="重试"
              onAction={reload}
            />
          ) : (materials ?? []).length === 0 ? (
            <div>
              <StateBlock
                kind="empty"
                title="当前账户还没有知识库材料"
                description="上传 PDF、DOCX、TXT、Markdown 或图片材料，解析索引完成后即可在任何对话中检索；材料仅当前账户可见。"
                actionLabel="返回新聊天"
                actionHref="/"
              />
              <div style={{ display: "flex", justifyContent: "center" }}>
                <Button onClick={openFilePicker}>
                  <Icon name="uploadFile" size={18} aria-hidden />
                  上传材料
                </Button>
              </div>
            </div>
          ) : (
            <>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-3)",
                  flexWrap: "wrap",
                  marginBottom: "var(--space-4)",
                }}
              >
                <div role="group" aria-label="按状态筛选" style={{ display: "flex", gap: "var(--space-1)" }}>
                  {STATUS_FILTERS.map((filter) => {
                    const active = statusFilter === filter.key;
                    return (
                      <button
                        key={filter.key}
                        type="button"
                        aria-pressed={active}
                        data-testid={`kb-filter-${filter.key}`}
                        onClick={() => setStatusFilter(filter.key)}
                        style={{
                          minHeight: "var(--target-size)",
                          padding: "0 var(--space-3)",
                          borderRadius: "var(--radius-md)",
                          border: `1px solid ${active ? "var(--color-accent-primary)" : "var(--color-border)"}`,
                          backgroundColor: active ? "var(--color-accent-primary-soft)" : "var(--color-surface)",
                          color: active ? "var(--color-accent-primary)" : "var(--color-text-secondary)",
                          fontSize: "var(--text-sm)",
                          fontWeight: active ? 600 : 400,
                          cursor: "pointer",
                          transition:
                            "background-color var(--motion-duration-fast) var(--motion-easing), " +
                            "border-color var(--motion-duration-fast) var(--motion-easing)",
                        }}
                      >
                        {filter.label}
                      </button>
                    );
                  })}
                </div>
                <input
                  type="search"
                  value={searchText}
                  onChange={(event) => setSearchText(event.target.value)}
                  placeholder="按文件名搜索"
                  aria-label="按文件名搜索"
                  data-testid="kb-search-input"
                  style={{
                    flex: 1,
                    minWidth: "12rem",
                    minHeight: "var(--target-size)",
                    padding: "var(--space-2) var(--space-3)",
                    borderRadius: "var(--radius-md)",
                    border: "1px solid var(--color-border)",
                    backgroundColor: "var(--color-surface)",
                    color: "var(--color-text-primary)",
                    fontSize: "var(--text-base)",
                  }}
                />
              </div>

              {filteredMaterials.length === 0 ? (
                <StateBlock
                  kind="empty"
                  title="没有匹配的材料"
                  description="当前筛选条件下没有材料，调整筛选或清除后再试。"
                  actionLabel="清除筛选"
                  onAction={clearFilters}
                />
              ) : (
                <ul role="list" style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                  {filteredMaterials.map(renderMaterialRow)}
                </ul>
              )}
            </>
          )}
        </div>
      </section>

      {dialog?.kind === "detail" && liveDialogMaterial && renderDetailDialog(liveDialogMaterial)}

      {dialog?.kind === "delete" && liveDialogMaterial && (
        <ConfirmActionDialog
          title="删除材料"
          explanation={`将删除「${liveDialogMaterial.filename}」的文件本体、解析分块与派生索引，操作不可撤销；已引用该材料的对话历史不会被修改。`}
          error={dialogError}
          confirmLabel="确认删除"
          confirmVariant="danger"
          busy={busyAction === `delete:${liveDialogMaterial.object_id}`}
          onConfirm={() => void doDelete(liveDialogMaterial)}
          onClose={() => setDialog(null)}
        />
      )}

      {dialog?.kind === "rebuild" && liveDialogMaterial && (
        <ConfirmActionDialog
          title="重建索引"
          explanation={`将以当前索引配置重建「${liveDialogMaterial.filename}」的索引，重建期间检索短暂使用旧版本。`}
          error={dialogError}
          confirmLabel="确认重建"
          confirmVariant="primary"
          busy={busyAction === `rebuild:${liveDialogMaterial.object_id}`}
          onConfirm={() => void doRebuild(liveDialogMaterial)}
          onClose={() => setDialog(null)}
        />
      )}
    </MainContent>
  );
}
