"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { Dialog } from "@/components/bridges/Dialog";
import { StateBlock } from "@/components/bridges/StateBlock";
import { Button } from "@/components/design-system/Button";
import { Icon, type IconName } from "@/components/design-system/Icon";
import { MainContent } from "@/components/layout/MainContent";
import {
  ApiError,
  classifyApiError,
  fetchKnowledgeBaseMaterialBlob,
  searchUnified,
  type SearchResponse,
  type SearchResultItem,
  type SearchResultType,
  type SearchSegment,
} from "@/lib/api";
import { useLearningProjects } from "@/lib/learning-projects";
import { restoreSearchReturnFocus } from "@/lib/search-shortcut";

const DEBOUNCE_MS = 300;
const SEARCH_LIMIT = 50;

const TYPE_ORDER: SearchResultType[] = ["chat", "image", "document", "project"];

const TYPE_META: Record<SearchResultType, { label: string; icon: IconName }> = {
  chat: { label: "聊天", icon: "chatBubble" },
  image: { label: "图片", icon: "imagePicture" },
  document: { label: "文档", icon: "documentPage" },
  project: { label: "项目", icon: "projectFolder" },
};

/** 时间范围筛选的起止日期输入框共用样式。 */
const dateInputStyle: React.CSSProperties = {
  minHeight: "var(--target-size)",
  padding: "0 var(--space-2)",
  borderRadius: "var(--radius-md)",
  border: "1px solid var(--color-border)",
  backgroundColor: "var(--color-surface)",
  color: "var(--color-text-primary)",
  fontSize: "var(--text-sm)",
};

type TypeFilter = SearchResultType | "all";

function formatRelativeTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 60_000) return "刚刚";
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)} 分钟前`;
  if (diffMs < 86_400_000) return `${Math.floor(diffMs / 3_600_000)} 小时前`;
  if (diffMs < 7 * 86_400_000) return `${Math.floor(diffMs / 86_400_000)} 天前`;
  return date.toLocaleString("zh-CN", { hour12: false });
}

/** 日期输入（YYYY-MM-DD）→ 当日起点/终点的 ISO 8601（本地时区）。 */
function dayStartIso(date: string): string | undefined {
  if (!date) return undefined;
  const parsed = new Date(`${date}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}

function dayEndIso(date: string): string | undefined {
  if (!date) return undefined;
  const parsed = new Date(`${date}T23:59:59.999`);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}

/** 命中片段：matched 段用 <mark> 高亮，其余原样拼接。 */
function Snippet({ segments }: { segments: SearchSegment[] }) {
  return (
    <>
      {segments.map((segment, index) =>
        segment.matched ? (
          <mark
            key={index}
            style={{
              backgroundColor: "var(--color-status-wait-bg)",
              color: "var(--color-text-primary)",
              borderRadius: "var(--radius-sm)",
              paddingInline: "0.125rem",
            }}
          >
            {segment.text}
          </mark>
        ) : (
          <span key={index}>{segment.text}</span>
        )
      )}
    </>
  );
}

/**
 * 图片结果的就地预览：经既有材料下载通道读取 Blob 构造 object URL，
 * 关闭时回收；加载失败给出中文错误而非空白。
 */
function ImagePreviewDialog({
  objectId,
  title,
  onClose,
}: {
  objectId: string;
  title: string;
  onClose: () => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    fetchKnowledgeBaseMaterialBlob(objectId)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "图片加载失败，请稍后重试。");
        }
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [objectId]);

  return (
    <Dialog open onClose={onClose} title={title} description="图片预览，Esc 或关闭按钮退出。">
      {error ? (
        <p role="alert" style={{ color: "var(--color-status-error)", fontSize: "var(--text-sm)" }}>
          {error}
        </p>
      ) : url ? (
        <img
          src={url}
          alt={title}
          data-testid="search-image-preview"
          style={{ maxWidth: "100%", borderRadius: "var(--radius-md)", display: "block" }}
        />
      ) : (
        <p role="status" style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
          正在加载图片…
        </p>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "var(--space-4)" }}>
        <Button variant="secondary" size="sm" onClick={onClose}>
          关闭
        </Button>
      </div>
    </Dialog>
  );
}

/**
 * 统一桌面搜索页（Issue 24，ChatGPT 搜索面板启发的整页形态）。
 *
 * 真实行为：输入防抖 300ms 边输边搜（过期响应被丢弃）；类型 tab（带计数）、
 * 学习项目与时间范围组合筛选，一键清除筛选；结果按类型分组并高亮真实命中
 * 片段；聊天跳到消息锚点、图片就地预览、文档定位知识库详情页码、项目进入
 * 详情。键盘：↑/↓ 选择、Enter 打开、Esc 返回原上下文并归还焦点。
 * 状态覆盖：空查询引导 / 加载中 / 无结果（含建议）/ 索引未就绪 /
 * 错误可重试（保留查询与筛选）/ 权限不足。
 */
export default function SearchPageClient() {
  const router = useRouter();
  const { projects } = useLearningProjects();

  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [projectFilter, setProjectFilter] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");

  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [errorState, setErrorState] = useState<
    { kind: "error" | "permission"; message: string; projectGone?: boolean } | null
  >(null);
  const [retrySeq, setRetrySeq] = useState(0);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [preview, setPreview] = useState<{ objectId: string; title: string } | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const requestSeqRef = useRef(0);
  const restoreOnLeaveRef = useRef(false);

  // 进入页面聚焦输入框；经 Esc 返回原上下文时归还焦点给 Ctrl+K 触发元素。
  useEffect(() => {
    inputRef.current?.focus();
    return () => {
      if (restoreOnLeaveRef.current) restoreSearchReturnFocus();
    };
  }, []);

  // 输入防抖：停止输入 300ms 后才提交查询词。
  useEffect(() => {
    const timer = window.setTimeout(() => setSubmittedQuery(query.trim()), DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query]);

  // 查询词或筛选变化时重新搜索；序号 + AbortController 双重防止竞态。
  useEffect(() => {
    const q = submittedQuery.trim();
    if (!q) {
      requestSeqRef.current += 1;
      setResponse(null);
      setStatus("idle");
      setErrorState(null);
      setActiveIndex(-1);
      return;
    }
    const seq = ++requestSeqRef.current;
    const controller = new AbortController();
    setStatus("loading");
    setErrorState(null);
    searchUnified({
      q,
      types: typeFilter === "all" ? undefined : [typeFilter],
      projectId: projectFilter || undefined,
      from: dayStartIso(fromDate),
      to: dayEndIso(toDate),
      limit: SEARCH_LIMIT,
      signal: controller.signal,
    })
      .then((data) => {
        if (seq !== requestSeqRef.current) return;
        setResponse(data);
        setStatus("success");
        setActiveIndex(-1);
      })
      .catch((error) => {
        if (seq !== requestSeqRef.current) return;
        if (error instanceof DOMException && error.name === "AbortError") return;
        // 项目筛选指向已删除的项目时重试无意义：识别后引导清除筛选。
        const projectGone = error instanceof ApiError && error.code === "project_not_found";
        setErrorState({
          kind: classifyApiError(error) === "other" ? "error" : "permission",
          message: error instanceof Error ? error.message : "搜索请求失败，请稍后重试。",
          projectGone,
        });
        setStatus("error");
      });
    return () => controller.abort();
  }, [submittedQuery, typeFilter, projectFilter, fromDate, toDate, retrySeq]);

  const groups = useMemo(
    () =>
      TYPE_ORDER.map((type) => ({
        type,
        items: (response?.results ?? []).filter((item) => item.result_type === type),
      })).filter((group) => group.items.length > 0),
    [response]
  );
  const flatResults = useMemo(() => groups.flatMap((group) => group.items), [groups]);
  const resultIndex = useMemo(() => {
    const map = new Map<SearchResultItem, number>();
    flatResults.forEach((item, index) => map.set(item, index));
    return map;
  }, [flatResults]);

  const counts = response?.counts ?? {};
  const totalCount = TYPE_ORDER.reduce((sum, type) => sum + (counts[type] ?? 0), 0);

  const hasActiveFilters =
    typeFilter !== "all" || projectFilter !== "" || fromDate !== "" || toDate !== "";

  const clearFilters = useCallback(() => {
    setTypeFilter("all");
    setProjectFilter("");
    setFromDate("");
    setToDate("");
  }, []);

  const openResult = useCallback(
    (item: SearchResultItem) => {
      if (item.result_type === "chat" && item.conversation_id) {
        const anchor = item.message_id ? `?message=${encodeURIComponent(item.message_id)}` : "";
        router.push(`/chat/${encodeURIComponent(item.conversation_id)}${anchor}`);
      } else if (item.result_type === "document" && item.object_id) {
        const params = new URLSearchParams({ material: item.object_id });
        if (item.page_number) params.set("page", String(item.page_number));
        if (item.section_title) params.set("section", item.section_title);
        router.push(`/knowledge-base?${params.toString()}`);
      } else if (item.result_type === "image" && item.object_id) {
        setPreview({ objectId: item.object_id, title: item.title });
      } else if (item.result_type === "project" && item.project_id) {
        router.push(`/account/projects/${encodeURIComponent(item.project_id)}`);
      }
    },
    [router]
  );

  const closeAndReturn = useCallback(() => {
    restoreOnLeaveRef.current = true;
    if (window.history.length > 1) router.back();
    else router.push("/");
  }, [router]);

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (flatResults.length === 0) return;
      event.preventDefault();
      const next =
        event.key === "ArrowDown"
          ? (activeIndex + 1) % flatResults.length
          : (activeIndex - 1 + flatResults.length) % flatResults.length;
      setActiveIndex(next);
      requestAnimationFrame(() => {
        document.getElementById(`search-result-${next}`)?.scrollIntoView({ block: "nearest" });
      });
    } else if (event.key === "Enter") {
      const item = activeIndex >= 0 ? flatResults[activeIndex] : undefined;
      if (item) {
        event.preventDefault();
        openResult(item);
      }
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeAndReturn();
    }
  };

  const renderRow = (item: SearchResultItem) => {
    const index = resultIndex.get(item) ?? 0;
    const active = index === activeIndex;
    const meta = TYPE_META[item.result_type];
    const anchorText =
      item.result_type === "document"
        ? [item.page_number ? `第 ${item.page_number} 页` : "", item.section_title ?? ""]
            .filter(Boolean)
            .join(" · ")
        : "";
    return (
      <div
        key={`${item.result_type}-${item.result_id}`}
        role="option"
        aria-selected={active}
        id={`search-result-${index}`}
        data-testid="search-result-row"
        data-result-type={item.result_type}
        onClick={() => openResult(item)}
        onMouseEnter={() => setActiveIndex(index)}
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: "var(--space-3)",
          padding: "var(--space-3) var(--space-4)",
          borderRadius: "var(--radius-md)",
          border: `1px solid ${active ? "var(--color-accent-primary)" : "var(--color-border)"}`,
          backgroundColor: active ? "var(--color-accent-primary-soft)" : "var(--color-surface)",
          cursor: "pointer",
          transition:
            "background-color var(--motion-duration-fast) var(--motion-easing), " +
            "border-color var(--motion-duration-fast) var(--motion-easing)",
        }}
      >
        <span
          style={{ color: "var(--color-text-tertiary)", flexShrink: 0, display: "inline-flex", paddingTop: "0.125rem" }}
          title={meta.label}
        >
          <Icon name={meta.icon} size={20} aria-hidden />
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "var(--space-3)" }}>
            <span
              title={item.title}
              style={{
                minWidth: 0,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                fontWeight: 600,
                color: "var(--color-text-primary)",
              }}
            >
              {item.title}
            </span>
            <span style={{ flexShrink: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
              {formatRelativeTime(item.updated_at)}
            </span>
          </div>
          {item.snippet && item.snippet.length > 0 && (
            <p
              style={{
                marginTop: "var(--space-1)",
                fontSize: "var(--text-sm)",
                color: "var(--color-text-secondary)",
                overflowWrap: "break-word",
              }}
            >
              <Snippet segments={item.snippet} />
            </p>
          )}
          {anchorText && (
            <p style={{ marginTop: "var(--space-1)", fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
              {anchorText}
            </p>
          )}
        </div>
      </div>
    );
  };

  const renderBody = () => {
    if (!submittedQuery) {
      return (
        <StateBlock
          kind="empty"
          title="输入关键词，搜索聊天、图片、文档和学习项目"
          description="输入后自动搜索；支持按类型、学习项目和时间范围筛选。上下方向键选择结果，回车打开，Esc 返回。"
        />
      );
    }
    if (status === "error" && errorState) {
      return errorState.kind === "permission" ? (
        <StateBlock
          kind="permission"
          title="当前账户无法使用搜索"
          description={errorState.message}
          actionLabel="重新登录"
          onAction={() => window.location.replace("/login")}
        />
      ) : (
        <StateBlock
          kind="error"
          title="搜索失败"
          description={
            errorState.projectGone
              ? `${errorState.message}，该学习项目可能已被删除，请清除项目筛选后重试。`
              : errorState.message
          }
          actionLabel={errorState.projectGone ? "清除项目筛选" : "重试"}
          onAction={
            errorState.projectGone
              ? clearFilters
              : () => setRetrySeq((current) => current + 1)
          }
        />
      );
    }
    if (response) {
      if (flatResults.length === 0) {
        return response.index_ready ? (
          <StateBlock
            kind="empty"
            title={`没有找到与“${response.query || submittedQuery}”相关的结果`}
            description="换个关键词试试，或清除类型、学习项目和时间筛选后重试。"
            actionLabel={hasActiveFilters ? "清除筛选" : undefined}
            onAction={hasActiveFilters ? clearFilters : undefined}
          />
        ) : (
          <StateBlock
            kind="recovery"
            title="索引尚未就绪"
            description="正在为你的内容建立索引，完成后即可搜索文档与图片，请稍后重试。"
            actionLabel="重试"
            onAction={() => setRetrySeq((current) => current + 1)}
          />
        );
      }
      return (
        <div>
          {!response.index_ready && (
            <div
              role="status"
              data-testid="search-index-banner"
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "var(--space-2)",
                marginBottom: "var(--space-4)",
                padding: "var(--space-3) var(--space-4)",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-status-wait)",
                backgroundColor: "var(--color-status-wait-bg)",
                color: "var(--color-status-wait)",
                fontSize: "var(--text-sm)",
              }}
            >
              <Icon name="alert" size={18} aria-hidden />
              <span>文档与图片索引尚未就绪，当前结果可能不完整，完成后将自动包含更多内容。</span>
            </div>
          )}
          {status === "loading" && (
            <p role="status" style={{ marginBottom: "var(--space-2)", fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
              正在搜索…
            </p>
          )}
          {/* listbox 只包含 option（经 role="group" 的合法容器），保证读屏将
              每个结果识别为可选择的选项。 */}
          <div role="listbox" id="search-results" aria-label="搜索结果">
            {groups.map((group) => (
              <div
                key={group.type}
                role="group"
                data-testid={`search-group-${group.type}`}
                aria-label={TYPE_META[group.type].label}
                style={{ marginBottom: "var(--space-4)" }}
              >
                <h2
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-2)",
                    marginBottom: "var(--space-2)",
                    fontSize: "var(--text-sm)",
                    fontWeight: 600,
                    color: "var(--color-text-secondary)",
                  }}
                >
                  <Icon name={TYPE_META[group.type].icon} size={16} aria-hidden />
                  {TYPE_META[group.type].label}
                </h2>
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                  {group.items.map(renderRow)}
                </div>
              </div>
            ))}
          </div>
        </div>
      );
    }
    return (
      <StateBlock kind="loading" title="正在搜索" description="在你的聊天、图片、文档和学习项目中查找。" />
    );
  };

  const tabs: { key: TypeFilter; label: string }[] = [
    { key: "all", label: "全部" },
    ...TYPE_ORDER.map((type) => ({ key: type as TypeFilter, label: TYPE_META[type].label })),
  ];

  return (
    <MainContent>
      <section
        aria-labelledby="search-title"
        style={{ maxWidth: "52rem", marginInline: "auto", padding: "0 var(--space-4)" }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--space-4)" }}>
          <h1 id="search-title" className="sc-section-title">
            搜索
          </h1>
          <button
            type="button"
            aria-label="关闭搜索并返回"
            data-testid="search-close"
            onClick={closeAndReturn}
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              minWidth: "var(--target-size)",
              minHeight: "var(--target-size)",
              border: "none",
              borderRadius: "var(--radius-md)",
              backgroundColor: "transparent",
              color: "var(--color-text-tertiary)",
              cursor: "pointer",
            }}
          >
            <Icon name="cross" size={20} aria-hidden />
          </button>
        </div>

        <div style={{ position: "relative", marginTop: "var(--space-4)" }}>
          <span
            aria-hidden="true"
            style={{
              position: "absolute",
              left: "var(--space-4)",
              top: "50%",
              transform: "translateY(-50%)",
              display: "inline-flex",
              color: "var(--color-text-tertiary)",
              pointerEvents: "none",
            }}
          >
            <Icon name="search" size={20} />
          </span>
          <input
            ref={inputRef}
            type="search"
            role="combobox"
            aria-expanded={flatResults.length > 0}
            aria-controls="search-results"
            aria-activedescendant={activeIndex >= 0 ? `search-result-${activeIndex}` : undefined}
            aria-label="搜索聊天、图片、文档和学习项目"
            data-testid="search-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onInputKeyDown}
            placeholder="搜索聊天、图片、文档和学习项目"
            autoComplete="off"
            style={{
              width: "100%",
              minHeight: "3.25rem",
              paddingLeft: "2.75rem",
              paddingRight: "3.25rem",
              borderRadius: "var(--radius-xl)",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-surface)",
              color: "var(--color-text-primary)",
              fontSize: "var(--text-lg)",
            }}
          />
          {query && (
            <button
              type="button"
              aria-label="清空关键词"
              data-testid="search-clear-query"
              onClick={() => {
                setQuery("");
                inputRef.current?.focus();
              }}
              style={{
                position: "absolute",
                right: "var(--space-2)",
                top: "50%",
                transform: "translateY(-50%)",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                minWidth: "var(--target-size)",
                minHeight: "var(--target-size)",
                border: "none",
                borderRadius: "var(--radius-md)",
                backgroundColor: "transparent",
                color: "var(--color-text-tertiary)",
                cursor: "pointer",
              }}
            >
              <Icon name="cross" size={16} aria-hidden />
            </button>
          )}
        </div>

        {submittedQuery && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              flexWrap: "wrap",
              marginTop: "var(--space-4)",
            }}
          >
            <div role="group" aria-label="按类型筛选" style={{ display: "flex", gap: "var(--space-1)", flexWrap: "wrap" }}>
              {tabs.map((tab) => {
                const activeTab = typeFilter === tab.key;
                const count = tab.key === "all" ? totalCount : (counts[tab.key] ?? 0);
                return (
                  <button
                    key={tab.key}
                    type="button"
                    aria-pressed={activeTab}
                    data-testid={`search-tab-${tab.key}`}
                    onClick={() => setTypeFilter(tab.key)}
                    style={{
                      minHeight: "var(--target-size)",
                      padding: "0 var(--space-3)",
                      borderRadius: "var(--radius-md)",
                      border: `1px solid ${activeTab ? "var(--color-accent-primary)" : "var(--color-border)"}`,
                      backgroundColor: activeTab ? "var(--color-accent-primary-soft)" : "var(--color-surface)",
                      color: activeTab ? "var(--color-accent-primary)" : "var(--color-text-secondary)",
                      fontSize: "var(--text-sm)",
                      fontWeight: activeTab ? 600 : 400,
                      cursor: "pointer",
                      transition:
                        "background-color var(--motion-duration-fast) var(--motion-easing), " +
                        "border-color var(--motion-duration-fast) var(--motion-easing)",
                    }}
                  >
                    {tab.label}
                    {response ? ` ${count}` : ""}
                  </button>
                );
              })}
            </div>
            <select
              aria-label="按学习项目筛选"
              data-testid="search-project-filter"
              value={projectFilter}
              onChange={(event) => setProjectFilter(event.target.value)}
              style={{
                minHeight: "var(--target-size)",
                padding: "0 var(--space-2)",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-surface)",
                color: "var(--color-text-primary)",
                fontSize: "var(--text-sm)",
              }}
            >
              <option value="">全部项目</option>
              {projects.map((project) => (
                <option key={project.project_id} value={project.project_id}>
                  {project.name}
                </option>
              ))}
            </select>
            <input
              type="date"
              aria-label="开始日期"
              data-testid="search-from"
              value={fromDate}
              onChange={(event) => setFromDate(event.target.value)}
              style={dateInputStyle}
            />
            <input
              type="date"
              aria-label="结束日期"
              data-testid="search-to"
              value={toDate}
              onChange={(event) => setToDate(event.target.value)}
              style={dateInputStyle}
            />
            {hasActiveFilters && (
              <Button variant="ghost" size="sm" data-testid="search-clear-filters" onClick={clearFilters}>
                清除筛选
              </Button>
            )}
          </div>
        )}

        <div style={{ marginTop: "var(--space-6)" }}>{renderBody()}</div>
      </section>

      {preview && (
        <ImagePreviewDialog
          objectId={preview.objectId}
          title={preview.title}
          onClose={() => setPreview(null)}
        />
      )}
    </MainContent>
  );
}
