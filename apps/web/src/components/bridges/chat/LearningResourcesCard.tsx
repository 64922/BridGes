"use client";

import { Icon } from "@/components/design-system/Icon";
import type {
  LearningResourcesProjection,
  ModuleQueryRecord,
  ResourceItem,
} from "@/lib/api";

/** V2 Issue 13：资料模块状态的中文标题（每条助手消息内如实显示）。 */
const STATUS_TITLES: Record<string, string> = {
  clarification: "需要先确认一个问题",
  searching: "正在检索图书与视频",
  success: "学习资料推荐",
  empty: "没有找到匹配的资料",
  error: "资料检索未完成",
  stopped: "已停止资料检索",
};

const KIND_LABELS: Record<string, string> = {
  book: "图书",
  video: "视频",
};

const QUERY_STATUS_LABELS: Record<string, string> = {
  success: "成功",
  empty: "无结果",
  skipped: "未执行",
  timeout: "超时",
  cancelled: "已取消",
  rate_limited: "被上游限流",
  error: "失败",
};

const SOURCE_LABELS: Record<string, string> = {
  openlibrary: "Open Library 书目",
  openalex: "OpenAlex 图书记录",
  tavily: "公网搜索（发现哔哩哔哩直达页）",
  bilibili: "哔哩哔哩公开接口（核对元数据）",
  book_catalog: "图书书目来源",
};

function formatTime(value: string | null | undefined): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN");
}

function formatDuration(seconds: number | null | undefined): string | null {
  if (!seconds || seconds <= 0) return null;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes === 0) return `${rest} 秒`;
  return rest === 0 ? `${minutes} 分钟` : `${minutes} 分 ${rest} 秒`;
}

/** 一次外部调用的真实记录：来源、实际查询词、结果分类、取得时间与错误。 */
function QueryRecordRow({ record }: { record: ModuleQueryRecord }) {
  const failed = record.status === "error" || record.status === "timeout";
  return (
    <li style={{ color: failed ? "var(--color-status-error)" : "var(--color-text-secondary)" }}>
      <strong>{SOURCE_LABELS[record.source] ?? record.source}</strong>
      {" · 查询「"}
      {record.query}
      {"」 · "}
      {QUERY_STATUS_LABELS[record.status] ?? record.status}
      {`（${record.evidence_count} 条）`}
      {" · "}
      {formatTime(record.retrieved_at)}
      {record.error_message ? `：${record.error_message}` : ""}
    </li>
  );
}

function formatCount(value: number | null | undefined): string | null {
  if (value === null || value === undefined || value < 0) return null;
  if (value >= 10000) return `约 ${(value / 10000).toFixed(1)} 万`;
  return String(value);
}

/** 一条资料：由浅入深的位置、真实链接、书目/视频元数据与选择理由。 */
function ResourceRow({ item }: { item: ResourceItem }) {
  const unverified = item.unverified ?? [];
  const duration = formatDuration(item.duration_seconds);
  const views = formatCount(item.view_count);
  const likes = formatCount(item.like_count);
  const counters = [
    views ? `播放 ${views}` : null,
    likes ? `点赞 ${likes}` : null,
  ].filter((value): value is string => Boolean(value));
  const details = [
    item.creator,
    item.publisher,
    item.year ? `${item.year} 年` : null,
    item.isbn ? `ISBN ${item.isbn}` : null,
    duration ? `时长 ${duration}` : null,
    counters.length > 0 ? `${counters.join("、")}（平台计数）` : null,
  ].filter((value): value is string => Boolean(value));
  return (
    <li
      data-testid={`resources-item-${item.order}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-1)",
        paddingBottom: "var(--space-2)",
        borderBottom: "1px solid var(--color-border)",
      }}
    >
      <p style={{ margin: 0, fontWeight: 600, color: "var(--color-text-primary)" }}>
        {item.order}. [{KIND_LABELS[item.kind] ?? item.kind}] {item.title}
      </p>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        适用阶段：{item.stage}
        {details.length > 0 ? ` · ${details.join(" · ")}` : ""}
      </span>
      <p style={{ margin: 0 }}>{item.reason_zh}</p>
      <p style={{ margin: 0, color: "var(--color-text-tertiary)" }}>
        核对依据：{item.match_basis}
      </p>
      <a
        href={item.url}
        target="_blank"
        rel="noreferrer"
        data-testid={`resources-${item.order}-link`}
        style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-1)" }}
      >
        <Icon name="download" size={14} aria-hidden />
        {item.kind === "video" ? "打开哔哩哔哩视频页" : "打开书目页"}
      </a>
      {unverified.length > 0 && (
        <span style={{ fontSize: "var(--text-xs)", color: "var(--color-status-wait)" }}>
          未核实项：{unverified.join("；")}
        </span>
      )}
    </li>
  );
}

/**
 * 学习资料推荐结果卡（V2 Issue 13）。
 *
 * 只渲染投影里的真实内容：保留的原词与实际查询词、每次外部调用的来源与
 * 结果分类、按由浅入深排列的图书/视频与直达链接、适用阶段与选择理由、
 * 以及实际数量与证据边界。澄清、无结果、失败与停止都在同一条消息内如实
 * 呈现，绝不假装检索成功、也绝不用模型记忆补造条目。
 */
export function LearningResourcesCard({
  resources,
  streaming,
  onRetry,
}: {
  resources: LearningResourcesProjection | null;
  streaming: boolean;
  onRetry?: () => void;
}) {
  if (!resources) return null;
  const shellStyle: React.CSSProperties = {
    margin: "var(--space-3) 0 0",
    padding: "var(--space-3)",
    border: "1px solid var(--color-border)",
    borderRadius: "var(--radius-md)",
    backgroundColor: "var(--color-bg-secondary)",
    display: "flex",
    flexDirection: "column",
    gap: "var(--space-2)",
    fontSize: "var(--text-sm)",
    color: "var(--color-text-secondary)",
  };
  const failed = resources.status === "error";
  const waiting = resources.status === "clarification";
  const items = resources.items ?? [];
  const queries = resources.queries ?? [];
  const notes = resources.evidence_notes ?? [];
  const expansions = resources.expansions ?? [];
  const books = items.filter((item) => item.kind === "book");
  const videos = items.filter((item) => item.kind === "video");

  return (
    <section
      data-testid={`resources-card-${resources.status}`}
      role={failed ? "alert" : "status"}
      aria-live="polite"
      aria-busy={streaming || resources.status === "searching"}
      style={{ ...shellStyle, borderColor: failed ? "var(--color-status-error)" : "var(--color-border)" }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <Icon
          name={failed ? "alert" : waiting ? "info" : "learningProject"}
          size={16}
          aria-hidden
        />
        <strong style={{ color: failed ? "var(--color-status-error)" : "var(--color-text-primary)" }}>
          {STATUS_TITLES[resources.status] ?? "学习资料推荐"}
        </strong>
        {resources.status === "success" && (
          <span
            data-testid="resources-count"
            style={{
              marginLeft: "auto",
              padding: "2px var(--space-2)",
              borderRadius: "var(--radius-full)",
              backgroundColor: "var(--color-status-success-bg)",
              color: "var(--color-status-success)",
              fontSize: "var(--text-xs)",
              fontWeight: 600,
            }}
          >
            实际 {items.length} 条（图书 {books.length} / 视频 {videos.length}）
            {resources.requested_books + resources.requested_videos !== items.length
              ? ` / 目标 ${resources.requested_books} 本 + ${resources.requested_videos} 条`
              : ""}
          </span>
        )}
      </div>

      {/* AC1：保留本轮原始专业名词与实际查询词（两者不同时同时呈现）。 */}
      {(resources.original_phrase || resources.final_query) && (
        <p style={{ margin: 0 }}>
          {resources.original_phrase && (
            <>
              原始说法：<strong>{resources.original_phrase}</strong>
            </>
          )}
          {resources.original_phrase && resources.final_query ? " · " : ""}
          {resources.final_query && (
            <>
              实际查询词：<strong>{resources.final_query}</strong>
            </>
          )}
        </p>
      )}
      {resources.level_label && <p style={{ margin: 0 }}>学习层次：{resources.level_label}</p>}
      {resources.goal && <p style={{ margin: 0 }}>学习目的：{resources.goal}</p>}
      {expansions.length > 0 && <p style={{ margin: 0 }}>扩展词：{expansions.join("、")}</p>}

      {waiting && resources.pending && (
        <div
          data-testid="resources-clarification"
          style={{
            padding: "var(--space-2) var(--space-3)",
            border: "1px solid var(--color-border-strong)",
            borderRadius: "var(--radius-md)",
            backgroundColor: "var(--color-surface)",
          }}
        >
          <p style={{ margin: 0, fontWeight: 600, color: "var(--color-text-primary)" }}>
            {resources.pending.question}
          </p>
          <p style={{ margin: "var(--space-1) 0 0", fontSize: "var(--text-xs)" }}>
            直接在下方回复即可；回复会带着「学习资料推荐」模块发出（输入框上方的模块标签可随时移除）。
          </p>
        </div>
      )}

      {failed && (
        <p style={{ margin: 0, color: "var(--color-status-error)" }}>
          {resources.error_message ?? "本轮资料检索未完成。"}
          {resources.error_code ? `（错误码：${resources.error_code}）` : ""}
        </p>
      )}

      {queries.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            本次外部调用记录
          </p>
          <ul
            data-testid="resources-queries"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "disc",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
              fontSize: "var(--text-xs)",
            }}
          >
            {queries.map((record, index) => (
              <QueryRecordRow key={`${record.source}-${record.query}-${index}`} record={record} />
            ))}
          </ul>
        </div>
      )}

      {items.length > 0 && (
        <ol
          data-testid="resources-items"
          style={{
            margin: 0,
            paddingLeft: "var(--space-5)",
            listStyle: "decimal",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-2)",
          }}
        >
          {items.map((item) => (
            <ResourceRow key={`${item.order}-${item.url}`} item={item} />
          ))}
        </ol>
      )}

      {notes.length > 0 && (
        <div>
          <p style={{ margin: 0, fontWeight: 600 }}>证据边界</p>
          <ul
            data-testid="resources-notes"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "disc",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
            }}
          >
            {notes.map((note, index) => (
              <li key={`${note}-${index}`}>{note}</li>
            ))}
          </ul>
        </div>
      )}

      {resources.retryable && onRetry && (
        <button
          type="button"
          data-testid="resources-retry"
          onClick={onRetry}
          style={{
            alignSelf: "flex-start",
            minHeight: "var(--target-size)",
            padding: "var(--space-1) var(--space-3)",
            border: "1px solid var(--color-status-error)",
            borderRadius: "var(--radius-md)",
            backgroundColor: "transparent",
            color: "var(--color-status-error)",
            font: "inherit",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          <Icon name="retry" size={14} aria-hidden /> 重试资料检索
        </button>
      )}
    </section>
  );
}
