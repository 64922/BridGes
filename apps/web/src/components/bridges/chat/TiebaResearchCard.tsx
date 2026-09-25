"use client";

import { Icon } from "@/components/design-system/Icon";
import type {
  ModuleQueryRecord,
  TiebaCandidateLink,
  TiebaOfficialCheck,
  TiebaPostProjection,
  TiebaRejectedCandidate,
  TiebaResearchProjection,
} from "@/lib/api";

/** V2 Issue 14：贴吧模块状态的中文标题（每条助手消息内如实显示）。 */
const STATUS_TITLES: Record<string, string> = {
  clarification: "需要先确认一个问题",
  searching: "正在检索贴吧帖子",
  success: "贴吧信息搜集",
  links_only: "只取得帖链（未取得回复内容）",
  empty: "没有找到属于目标贴吧的帖子",
  error: "贴吧信息搜集未完成",
  stopped: "已停止贴吧检索",
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

const READ_STATUS_LABELS: Record<string, string> = {
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

function formatTime(value: string | null | undefined): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN");
}

/** 一次外部调用的真实记录：来源、实际查询词、结果分类、取得时间与错误。 */
function QueryRecordRow({ record }: { record: ModuleQueryRecord }) {
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
function PostRow({ post }: { post: TiebaPostProjection }) {
  const floors =
    post.floor_min != null && post.floor_max != null
      ? `第 ${post.floor_min}–${post.floor_max} 楼`
      : null;
  const replies = post.replies ?? [];
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
        {post.total_pages ? `（页面声明共 ${post.total_pages} 页）` : ""}
        {floors ? ` · ${floors}` : ""} · 读取于 {formatTime(post.retrieved_at)}
      </span>
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
                {reply.floor != null ? `第 ${reply.floor} 楼` : "楼层未知"}
                {reply.is_original_poster ? "（楼主）" : ""}
                {reply.posted_at ? ` · ${reply.posted_at}` : " · 时间未给出"}：
              </span>
              {reply.content}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/** 仅有搜索摘要的帖链：明确标注归属未确认。 */
function CandidateLinkRow({ link }: { link: TiebaCandidateLink }) {
  return (
    <li style={{ color: "var(--color-text-secondary)" }}>
      <a href={link.url} target="_blank" rel="noreferrer">
        {link.title || link.url}
      </a>
      <span style={{ color: "var(--color-status-wait)" }}> · 归属未确认（仅有搜索摘要）</span>
    </li>
  );
}

/** 被剔除的候选：他吧同名帖与剔除依据（不静默丢弃）。 */
function RejectedRow({ candidate }: { candidate: TiebaRejectedCandidate }) {
  return (
    <li style={{ color: "var(--color-text-tertiary)" }}>
      {candidate.title || candidate.url}：{candidate.evidence}
    </li>
  );
}

/** 学校官方页面核验结果（与吧友个人经历分开展示）。 */
function OfficialCheckRow({ check }: { check: TiebaOfficialCheck }) {
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
      {check.excerpt ? (
        <blockquote
          data-testid={`tieba-official-${check.host}-excerpt`}
          style={{
            margin: "var(--space-1) 0 0",
            paddingLeft: "var(--space-3)",
            borderLeft: "2px solid var(--color-border)",
            color: "var(--color-text-tertiary)",
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

/**
 * 贴吧信息搜集结果卡（V2 Issue 14）。
 *
 * 只渲染投影里的真实内容：实际查询词、每个候选的归属确认依据与被剔除
 * 理由、真实读到的页数与楼层时间、帖链降级时的「归属未确认／未取得回复
 * 内容」标注，以及单独分区的学校官方页面核验。绝不把搜索摘要当作已读
 * 帖子，也绝不以模型记忆补足回复内容。
 */
export function TiebaResearchCard({
  research,
  streaming,
  onRetry,
}: {
  research: TiebaResearchProjection | null;
  streaming: boolean;
  onRetry?: () => void;
}) {
  if (!research) return null;
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
  const failed = research.status === "error";
  const waiting = research.status === "clarification";
  const degraded = research.status === "links_only";
  const posts = research.confirmed_posts ?? [];
  const links = research.candidate_links ?? [];
  const rejected = research.rejected_candidates ?? [];
  const official = research.official_checks ?? [];
  const queries = research.queries ?? [];
  const sections = research.sections ?? [];
  const notes = research.evidence_boundary ?? [];
  const terms = research.topic_terms ?? [];
  const timeFilter = research.time_filter;

  return (
    <section
      data-testid={`tieba-research-card-${research.status}`}
      role={failed ? "alert" : "status"}
      aria-live="polite"
      aria-busy={streaming || research.status === "searching"}
      style={{
        ...shellStyle,
        borderColor: failed ? "var(--color-status-error)" : "var(--color-border)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <Icon
          name={failed ? "alert" : waiting ? "info" : "tiebaThread"}
          size={16}
          aria-hidden
        />
        <strong style={{ color: failed ? "var(--color-status-error)" : "var(--color-text-primary)" }}>
          {STATUS_TITLES[research.status] ?? "贴吧信息搜集"}
        </strong>
        {research.status === "success" && (
          <span
            data-testid="tieba-research-count"
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
            已确认 {posts.length} 帖
          </span>
        )}
        {degraded && (
          <span
            data-testid="tieba-research-degraded"
            style={{
              marginLeft: "auto",
              padding: "2px var(--space-2)",
              borderRadius: "var(--radius-full)",
              backgroundColor: "var(--color-status-wait-bg)",
              color: "var(--color-status-wait)",
              fontSize: "var(--text-xs)",
              fontWeight: 600,
            }}
          >
            仅帖链
          </span>
        )}
      </div>

      {/* AC1：原始问题逐字保留，只按解析出的原词检索。 */}
      <p style={{ margin: 0 }}>
        所在贴吧：<strong>{research.forum}</strong>
        {terms.length > 0 ? (
          <>
            {" · 原始名词："}
            <strong>{terms.join("、")}</strong>
          </>
        ) : null}
      </p>
      <p style={{ margin: 0 }}>原始问题：{research.original_question}</p>
      <p style={{ margin: 0 }} data-testid="tieba-time-filter">
        时间条件：{timeFilter.requirement ? `「${timeFilter.requirement}」` : "未提出"} ·{" "}
        {timeFilter.note}
      </p>

      {waiting && research.pending && (
        <div
          data-testid="tieba-research-clarification"
          style={{
            padding: "var(--space-2) var(--space-3)",
            border: "1px solid var(--color-border-strong)",
            borderRadius: "var(--radius-md)",
            backgroundColor: "var(--color-surface)",
          }}
        >
          <p style={{ margin: 0, fontWeight: 600, color: "var(--color-text-primary)" }}>
            {research.pending.question}
          </p>
          <p style={{ margin: "var(--space-1) 0 0", fontSize: "var(--text-xs)" }}>
            直接在下方回复即可；回复会带着「贴吧信息搜集」模块发出（输入框上方的模块标签可随时移除）。
          </p>
        </div>
      )}

      {failed && (
        <p style={{ margin: 0, color: "var(--color-status-error)" }}>
          {research.error_message ?? "本轮贴吧检索未完成。"}
          {research.error_code ? `（错误码：${research.error_code}）` : ""}
        </p>
      )}

      {research.empty_reason && <p style={{ margin: 0 }}>{research.empty_reason}</p>}

      {queries.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            本次外部调用记录
          </p>
          <ul
            data-testid="tieba-research-queries"
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

      {posts.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            已确认属于{research.forum}的帖子
          </p>
          <ul
            data-testid="tieba-research-posts"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "disc",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-2)",
            }}
          >
            {posts.map((post) => (
              <PostRow key={post.thread_id ?? post.url} post={post} />
            ))}
          </ul>
        </div>
      )}

      {links.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            帖链（未取得回复内容，归属以页面为准）
          </p>
          <ul
            data-testid="tieba-candidate-links"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "disc",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
            }}
          >
            {links.map((link) => (
              <CandidateLinkRow key={link.url} link={link} />
            ))}
          </ul>
        </div>
      )}

      {rejected.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            已剔除的候选（其他贴吧的同名帖）
          </p>
          <ul
            data-testid="tieba-rejected-candidates"
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
            {rejected.map((candidate, index) => (
              <RejectedRow key={`${candidate.url}-${index}`} candidate={candidate} />
            ))}
          </ul>
        </div>
      )}

      {sections.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>吧友说法</p>
          <ul
            data-testid="tieba-research-sections"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "disc",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
            }}
          >
            {sections.map((section, index) => (
              <li key={`section-${index}`} style={{ whiteSpace: "pre-wrap" }}>
                {section}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* AC3：官方规定只在真的取到官方页面时呈现，且与吧友经历分区。 */}
      {(official.length > 0 || research.official_check_requested) && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            学校官方页面核验（与上面的吧友个人经历分列）
          </p>
          {official.length > 0 ? (
            <ul
              data-testid="tieba-official-checks"
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
              {official.map((check, index) => (
                <OfficialCheckRow key={`${check.url}-${index}`} check={check} />
              ))}
            </ul>
          ) : (
            <p style={{ margin: 0, color: "var(--color-status-wait)" }}>
              本轮未取得学校官方页面，暂不区分官方规定与个人经历。
            </p>
          )}
        </div>
      )}

      {notes.length > 0 && (
        <div>
          <p style={{ margin: 0, fontWeight: 600 }}>证据边界</p>
          <ul
            data-testid="tieba-research-notes"
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
            {notes.map((note, index) => (
              <li key={`note-${index}`}>{note}</li>
            ))}
          </ul>
        </div>
      )}

      <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        本轮完成于 {formatTime(research.completed_at)}
      </p>

      {research.retryable && onRetry && (
        <button
          type="button"
          data-testid="tieba-research-retry"
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
          <Icon name="retry" size={14} aria-hidden /> 重试贴吧信息搜集
        </button>
      )}
    </section>
  );
}
