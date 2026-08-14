import { Icon } from "@/components/design-system/Icon";
import type { WebSearchProjection, WebSearchResult } from "@/lib/api";

const unverifiedSearchSuffix = "；学习模式将标注本轮未联网核实";
const failureStatusLabel: Record<string, string> = {
  permission: `公网搜索权限未通过${unverifiedSearchSuffix}`,
  empty: `没有找到公开网页结果${unverifiedSearchSuffix}`,
  evidence_insufficient: `来源证据不足${unverifiedSearchSuffix}`,
  fetch_error: `来源页面抓取失败${unverifiedSearchSuffix}`,
  source_conflict: "来源存在冲突",
  error: `联网搜索未完成${unverifiedSearchSuffix}`,
};

const providerChallengeLabel =
  `公网搜索提供方暂时受阻，请稍后显式重试${unverifiedSearchSuffix}`;

const providerLabel: Record<string, string> = {
  duckduckgo: "DuckDuckGo",
  brave_search: "Brave Search",
};

function displayProvider(provider: string | null | undefined): string {
  if (!provider) return "未选定提供方";
  return providerLabel[provider] ?? provider;
}

function accessedAt(value: string | null): string {
  if (!value) return "访问时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "访问时间未知"
    : `访问于 ${date.toLocaleString("zh-CN")}`;
}

function ResultItem({ result }: { result: WebSearchResult }) {
  return (
    <li
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-1)",
        padding: "var(--space-2) 0",
        borderTop: "1px solid var(--color-border)",
      }}
    >
      <a
        href={result.url}
        target="_blank"
        rel="noreferrer"
        data-testid={`web-search-result-${result.result_id}`}
        style={{
          color: "var(--color-accent-primary)",
          fontWeight: 600,
          textDecoration: "underline",
          textUnderlineOffset: "3px",
          minHeight: "var(--target-size)",
          display: "inline-flex",
          alignItems: "center",
          gap: "var(--space-1)",
          outlineOffset: "3px",
        }}
      >
        <Icon name="chevronRight" size={14} aria-hidden />
        {result.title}
      </a>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        {result.site} · {result.url}
      </span>
      {result.snippet && (
        <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
          {result.snippet}
        </p>
      )}
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        {result.verification === "verified" || result.verification === "cross_verified"
          ? "已抓取核验 · "
          : result.verification === "structured"
            ? "结构化结果 · "
          : result.verification === "summary_only"
            ? "仅搜索摘要，未完成页面核验 · "
            : "来源页面抓取失败 · "}
        {accessedAt(result.fetched_at ?? result.accessed_at)}
      </span>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        提供方：{displayProvider(result.provider)}（{result.provider_version}）
      </span>
    </li>
  );
}

function RetryButton({ onRetry }: { onRetry: () => void }) {
  return (
    <button
      type="button"
      onClick={onRetry}
      data-testid="web-search-retry"
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: "var(--space-1)",
        minHeight: "var(--target-size)",
        padding: "var(--space-1) var(--space-2)",
        border: "1px solid var(--color-status-error)",
        borderRadius: "var(--radius-md)",
        backgroundColor: "transparent",
        color: "var(--color-status-error)",
        cursor: "pointer",
        font: "inherit",
        fontSize: "var(--text-xs)",
        fontWeight: 600,
      }}
    >
      <Icon name="retry" size={14} aria-hidden />
      重试联网搜索
    </button>
  );
}

function QueryTrail({ search }: { search: WebSearchProjection }) {
  const history = search.query_history ?? [];
  if (history.length <= 1) return null;
  return (
    <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
      本轮执行的脱敏查询：{history.join(" · ")}
    </span>
  );
}

/** Issue 21：公网搜索卡，明确显示触发原因、脱敏查询、真实来源与恢复操作。 */
export function WebSearchCard({
  search,
  streaming,
  onRetry,
  onCancel,
}: {
  search: WebSearchProjection | null;
  streaming: boolean;
  onRetry: () => void;
  onCancel?: () => void;
}) {
  if (!search) return null;
  const results = search.results ?? [];
  const selectedProvider = search.selected_provider ?? search.provider;
  const selectedProviderVersion =
    search.selected_provider_version ?? search.provider_version;

  const shellStyle: React.CSSProperties = {
    margin: "var(--space-3) 0 0",
    padding: "var(--space-3)",
    borderRadius: "var(--radius-md)",
    border: "1px solid var(--color-border)",
    backgroundColor: "var(--color-bg-secondary)",
    display: "flex",
    flexDirection: "column",
    gap: "var(--space-2)",
  };

  if (search.status === "loading" || search.status === "recovery") {
    return (
      <section data-testid="web-search-card-loading" role="status" aria-live="polite" style={shellStyle}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
          <span className="bg-spinner" aria-hidden="true" />
          <strong>正在联网搜索</strong>
          {search.status === "recovery" && <span>（正在恢复）</span>}
          {search.can_cancel && onCancel && (
            <button
              type="button"
              onClick={onCancel}
              data-testid="web-search-cancel"
              style={{
                marginLeft: "auto",
                minHeight: "var(--target-size)",
                padding: "var(--space-1) var(--space-2)",
                border: "1px solid var(--color-border-strong)",
                borderRadius: "var(--radius-md)",
                backgroundColor: "transparent",
                color: "var(--color-text-secondary)",
                cursor: "pointer",
                font: "inherit",
                fontSize: "var(--text-xs)",
              }}
            >
              取消本轮
            </button>
          )}
        </div>
        <span style={{ fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
          {search.trigger_reason} · 查询概述：{search.query_summary} · 主用提供方：{displayProvider(search.provider)}
        </span>
        {search.error_message && (
          <span
            data-testid="web-search-card-degraded-hint"
            style={{ fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}
          >
            {search.error_message}
          </span>
        )}
      </section>
    );
  }

  if (search.status === "cancelled") {
    return (
      <section data-testid="web-search-card-cancelled" role="status" aria-live="polite" style={shellStyle}>
        <span style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
          <Icon name="info" size={16} aria-hidden /> 已取消本轮联网搜索，未发送新的用户消息。
        </span>
      </section>
    );
  }

  const failed = [
    "error",
    "permission",
    "fetch_error",
    "evidence_insufficient",
    "source_conflict",
  ].includes(search.status);
  if (failed || search.status === "empty") {
    return (
      <section
        data-testid={`web-search-card-${search.status}`}
        role={failed ? "alert" : "status"}
        aria-live="polite"
        style={{
          ...shellStyle,
          borderColor: failed ? "var(--color-status-error)" : "var(--color-border)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
          <Icon name={failed ? "alert" : "info"} size={16} aria-hidden />
          <strong>
            {search.error_code === "web_search_provider_challenge"
              ? providerChallengeLabel
              : failureStatusLabel[search.status] ?? "联网搜索未完成"}
          </strong>
          {search.can_retry && <span style={{ marginLeft: "auto" }}><RetryButton onRetry={onRetry} /></span>}
        </div>
        <span style={{ fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
          {search.error_message ?? "请检查网络后重试。"}
        </span>
        <QueryTrail search={search} />
      </section>
    );
  }

  return (
    <section data-testid="web-search-card" aria-live="polite" aria-busy={streaming} style={shellStyle}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <Icon name="paperSearch" size={16} aria-hidden />
        <strong>{search.status === "partial" ? "联网搜索（部分成功）" : "联网搜索"}</strong>
        <span
          role="status"
          style={{
            marginLeft: "auto",
            padding: "2px var(--space-2)",
            borderRadius: "var(--radius-full)",
            color: "var(--color-status-success)",
            backgroundColor: "var(--color-status-success-bg)",
            fontSize: "var(--text-xs)",
            fontWeight: 600,
          }}
        >
          已返回 {results.length} 条来源
        </span>
      </div>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        {search.trigger_reason} · 查询概述：{search.query_summary} · 实际提供方：{displayProvider(selectedProvider)}（{selectedProviderVersion}）
      </span>
      <QueryTrail search={search} />
      <ul
        role="list"
        aria-label="联网搜索来源"
        data-testid="web-search-results"
        style={{ listStyle: "none", margin: 0, padding: 0 }}
      >
        {results.map((result) => <ResultItem key={result.result_id} result={result} />)}
      </ul>
    </section>
  );
}
