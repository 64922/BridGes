"use client";

import type {
  ModuleQueryRecord,
  TiebaCandidateLink,
  TiebaOfficialCheck,
  TiebaPostProjection,
  TiebaRejectedCandidate,
  TiebaRunMetadata,
  TiebaSummaryCitation,
} from "@/lib/api";

const QUERY_STATUS_LABELS: Record<string, string> = {
  success: "成功",
  empty: "无结果",
  skipped: "未执行",
  timeout: "超时",
  cancelled: "已取消",
  rate_limited: "被上游限流",
  error: "失败",
};

export const READ_STATUS_LABELS: Record<string, string> = {
  read: "已读到正文",
  partial: "只读到部分楼层",
  access_restricted: "访问受限（登录或验证要求），未绕过",
  unrecognized: "页面结构无法解析",
  not_found: "帖子不存在或已删除",
  timeout: "读取超时",
  error: "读取失败",
  cancelled: "已取消",
};

const OFFICIAL_STATUS_LABELS: Record<string, string> = {
  verified: "已定位相关段落",
  excerpt_not_found: "已取得页面，但未定位到相关段落",
  fetch_failed: "未能取得该页面",
  domain_rejected: "链接不属于学校官方域名，未作为官方依据",
};

export function formatTime(value: string | null | undefined): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN");
}

/** 引用在帖子里的定位：发言者、楼层与发言时间；缺失处如实标注。 */
export function citationLocation(citation: TiebaSummaryCitation): string {
  const parts = [
    citation.author ?? "发言者未知",
    citation.floor != null ? `第 ${citation.floor} 楼` : "楼层未知",
  ];
  if (citation.posted_at) parts.push(citation.posted_at);
  return parts.join(" · ");
}

/** 运行记录：真实请求/模型调用计数、版本与未读范围。 */
export function RunMetadataRow({ metadata }: { metadata: TiebaRunMetadata }) {
  const elapsed =
    metadata.elapsed_ms == null
      ? "耗时未知"
      : metadata.elapsed_ms < 1000
        ? `${metadata.elapsed_ms} 毫秒`
        : `${(metadata.elapsed_ms / 1000).toFixed(1)} 秒`;
  const versions = Object.entries(metadata.component_versions ?? {});
  return (
    <details data-testid="tieba-run-metadata">
      <summary style={{ cursor: "pointer", minHeight: "var(--target-size)" }}>
        运行记录（请求、模型调用与未读范围）
      </summary>
      <ul
        style={{
          margin: 0,
          paddingLeft: "var(--space-5)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-1)",
          fontSize: "var(--text-xs)",
        }}
      >
        <li>
          运行 {metadata.run_id} · 配方 {metadata.recipe_version}
        </li>
        <li>
          开始 {formatTime(metadata.started_at)} · 完成 {formatTime(metadata.completed_at)} ·{" "}
          {elapsed}
        </li>
        <li>
          外部请求 {metadata.request_count} 次：检索 {metadata.search_request_count}、读取{" "}
          {metadata.read_request_count}、官方 {metadata.official_request_count}
        </li>
        <li>
          模型调用 {metadata.model_call_count} 次 · 确认帖子 {metadata.confirmed_post_count} 个 ·
          记录楼层 {metadata.read_reply_count} 条
        </li>
        {versions.length > 0 && (
          <li>组件版本：{versions.map(([name, version]) => `${name} ${version}`).join(" · ")}</li>
        )}
        {(metadata.unread_scope ?? []).length > 0 && (
          <li>
            未读范围：
            <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
              {metadata.unread_scope?.map((note, index) => (
                <li key={`unread-${index}`}>{note}</li>
              ))}
            </ul>
          </li>
        )}
      </ul>
    </details>
  );
}

/** 一个帖子本轮真实读到的范围：页数、楼层、时间与读取状态。 */
export function postReadScope(post: TiebaPostProjection): string {
  const parts = [
    `已读 ${post.pages_read}/${post.pages_limit} 页`,
  ];
  const pages = (post.page_evidence ?? []).map((item) => item.page);
  if (pages.length > 0) parts.push(`已成功读取第 ${pages.join("、")} 页`);
  parts.push(post.pages_complete ? "普通楼层页已全部读取" : "普通楼层页未读完");
  if (post.scroll_rounds_limit) {
    parts.push(`向下加载 ${post.scroll_rounds}/${post.scroll_rounds_limit} 轮`);
  }
  if (post.total_pages) parts.push(`页面声明共 ${post.total_pages} 页`);
  if (post.floor_min != null && post.floor_max != null) {
    parts.push(`第 ${post.floor_min}–${post.floor_max} 楼`);
  }
  const times = (post.replies ?? [])
    .map((reply) => reply.posted_at)
    .filter((value): value is string => Boolean(value));
  if (times.length > 0) {
    const sorted = [...times].sort();
    parts.push(`时间 ${sorted[0]}–${sorted[sorted.length - 1]}`);
  }
  parts.push(READ_STATUS_LABELS[post.read_status] ?? post.read_status);
  return parts.join(" · ");
}

/** 一次外部调用的真实记录：来源、实际查询词、结果分类、取得时间与错误。 */
export function QueryRecordRow({ record }: { record: ModuleQueryRecord }) {
  const failed = record.status === "error" || record.status === "timeout";
  return (
    <li style={{ color: failed ? "var(--color-status-error)" : "var(--color-text-secondary)" }}>
      <strong>{record.source}</strong>
      {" · 查询「"}
      {record.query}
      {"」 · "}
      {QUERY_STATUS_LABELS[record.status] ?? record.status}
      {`（${record.evidence_count} 条）`}
      {" · "}
      {formatTime(record.retrieved_at)}
      {record.detail ? `：${record.detail}` : ""}
      {record.error_message ? `：${record.error_message}` : ""}
    </li>
  );
}

/** 一个真实读到的帖子：读到的页数/楼层/时间，以及未取得回复时的原因。 */
export function PostRow({ post }: { post: TiebaPostProjection }) {
  const floors =
    post.floor_min != null && post.floor_max != null
      ? `第 ${post.floor_min}–${post.floor_max} 楼`
      : null;
  const replies = post.replies ?? [];
  const excerpts = replies.filter(reply => reply.content.trim().length > 1 && !/^\d+$/.test(reply.content.trim())).slice(0, 3);
  return (
    <li
      data-testid={`tieba-post-${post.thread_id ?? post.url}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-1)",
        paddingBottom: "var(--space-2)",
        borderBottom: "1px solid var(--color-border)",
      }}
    >
      <a href={post.url} target="_blank" rel="noreferrer" style={{ fontWeight: 600 }}>
        {post.title ?? post.url}
      </a>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        归属确认：{post.affiliation_evidence} ·{" "}
        {READ_STATUS_LABELS[post.read_status] ?? post.read_status} · 已读{" "}
        {post.pages_read}/{post.pages_limit} 页
        {post.page_evidence?.length
          ? ` · 已成功读取第 ${post.page_evidence.map((item) => item.page).join("、")} 页`
          : ""}
        {post.pages_complete ? " · 普通楼层页已全部读取" : " · 普通楼层页未读完"}
        {post.total_pages ? `（页面声明共 ${post.total_pages} 页）` : ""}
        {floors ? ` · ${floors}` : ""} · 读取于 {formatTime(post.retrieved_at)}
      </span>
      {post.pagination_error_message &&
        !(post.read_error_message ?? "").includes(post.pagination_error_message) && (
          <span style={{ color: "var(--color-status-wait)", fontSize: "var(--text-xs)" }}>
            {post.pagination_error_message}
          </span>
        )}
      {post.read_status === "partial" && post.read_error_message && (
        <span style={{ color: "var(--color-status-wait)", fontSize: "var(--text-xs)" }}>{post.read_error_message}</span>
      )}
      {!post.replies_obtained && (
        <span
          data-testid={`tieba-post-${post.thread_id ?? post.url}-no-replies`}
          style={{ color: "var(--color-status-wait)" }}
        >
          未取得回复内容
          {post.read_error_message ? `：${post.read_error_message}` : "。"}
        </span>
      )}
      {replies.length > 0 && (
        <>
        <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
          {excerpts.map((reply, index) => <li key={`excerpt-${index}`}>
            {reply.author ?? (reply.is_original_poster ? "楼主" : "回复")}{reply.posted_at ? `（${reply.posted_at}）` : ""}：
            {reply.content.slice(0, 160)}{reply.content.length > 160 ? "…" : ""}
          </li>)}
        </ul>
        <details>
          <summary style={{ cursor: "pointer", minHeight: "var(--target-size)" }}>查看本轮取得的全部原文（{replies.length} 条）</summary>
        <ul
          data-testid={`tieba-post-${post.thread_id ?? post.url}-replies`}
          style={{
            margin: 0,
            paddingLeft: "var(--space-5)",
            listStyle: "disc",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-1)",
          }}
        >
          {replies.map((reply, index) => (
            <li key={`${reply.floor ?? index}-${index}`}>
              <span style={{ color: "var(--color-text-tertiary)" }}>
                {reply.author ? `${reply.author} · ` : ""}
                {reply.floor != null ? `第 ${reply.floor} 楼` : "楼层未知"}
                {reply.is_original_poster ? "（楼主）" : ""}
                {reply.posted_at ? ` · ${reply.posted_at}` : " · 时间未给出"}：
              </span>
              {reply.content}
            </li>
          ))}
        </ul>
        </details>
        </>
      )}
    </li>
  );
}

/** 仅有搜索摘要的帖链：逐条展示未确认归属的具体原因（页面不可读／未读取）。 */
export function CandidateLinkRow({ link }: { link: TiebaCandidateLink }) {
  const reason = link.unconfirmed_reason ? `（${link.unconfirmed_reason}）` : "";
  return (
    <li style={{ color: "var(--color-text-secondary)" }}>
      <a href={link.url} target="_blank" rel="noreferrer">
        {link.title || link.url}
      </a>
      <span style={{ color: "var(--color-status-wait)" }}>
        {" "}
        · {link.source}
        {reason} · 归属未确认
      </span>
    </li>
  );
}

/** 被剔除的候选：非帖子链接与他吧证据的剔除依据（不静默丢弃）。 */
export function RejectedRow({ candidate }: { candidate: TiebaRejectedCandidate }) {
  return (
    <li style={{ color: "var(--color-text-tertiary)" }}>
      {candidate.title || candidate.url}：{candidate.evidence}
    </li>
  );
}

/** 学校官方页面核验结果（与吧友个人经历分开展示）。 */
export function OfficialCheckRow({ check }: { check: TiebaOfficialCheck }) {
  const matched = check.matched_terms ?? [];
  return (
    <li data-testid={`tieba-official-${check.host}`} style={{ color: "var(--color-text-secondary)" }}>
      <a href={check.url} target="_blank" rel="noreferrer">
        {check.title || check.url}
      </a>
      {" · "}
      {check.host}
      {" · "}
      {OFFICIAL_STATUS_LABELS[check.status] ?? check.status}
      {" · "}
      {formatTime(check.fetched_at)}
      {matched.length > 0 ? ` · 命中原词：${matched.join("、")}` : ""}
      {check.applicability && (
        <p style={{ margin: "var(--space-1) 0 0" }}>
          {check.applicability.applicable ? "适用性核对：" : "适用性未确认："}
          {check.applicability.note}
        </p>
      )}
      {check.excerpt ? (
        <blockquote
          data-testid={`tieba-official-${check.host}-excerpt`}
          style={{
            margin: "var(--space-1) 0 0",
            paddingLeft: "var(--space-3)",
            borderLeft: "2px solid var(--color-border)",
            color: "var(--color-text-tertiary)",
            whiteSpace: "pre-line",
          }}
        >
          {check.excerpt}
        </blockquote>
      ) : null}
      {check.error_message ? (
        <span style={{ color: "var(--color-status-error)" }}> · {check.error_message}</span>
      ) : null}
    </li>
  );
}

