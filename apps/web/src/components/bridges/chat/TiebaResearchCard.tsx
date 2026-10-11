"use client";

import { Icon } from "@/components/design-system/Icon";
import type { TiebaResearchProjection } from "@/lib/api";
import { TiebaBrowserSetup } from "./TiebaBrowserSetup";
import {
  CandidateLinkRow,
  OfficialCheckRow,
  PostRow,
  QueryRecordRow,
  RejectedRow,
  formatTime,
} from "./TiebaEvidenceRows";

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
  const hasOfficialEvidence = official.some((check) =>
    check.status === "verified" && check.excerpt && check.applicability?.applicable,
  );
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
          {research.status === "empty" && hasOfficialEvidence
            ? "已取得官方信息，贴吧讨论尚未取得"
            : STATUS_TITLES[research.status] ?? "贴吧信息搜集"}
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
        检索范围：<a href={`https://tieba.baidu.com/f?kw=${encodeURIComponent(research.forum.replace(/吧$/, ""))}`} target="_blank" rel="noreferrer"><strong>{research.forum}</strong></a>{research.official_check_requested && " · 学校官方域名 ecjtu.edu.cn"}
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
      {(research.status === "empty" || degraded) && (
        <p style={{ margin: 0 }}>
          未召回帖子不代表吧内没有相关讨论。
        </p>
      )}
      {!streaming && research.status !== "stopped" && queries.some(query =>
        ["tieba_browser_setup_required", "tieba_browser_unavailable", "tieba_browser_busy"].includes(query.error_code ?? "") ||
        (degraded && query.source === "tieba_browser")
      ) && (
        <TiebaBrowserSetup onRetry={onRetry} />
      )}

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
      {research.official_blocked_reason && (
        <p style={{ margin: 0 }}>来源限制：{research.official_blocked_reason}</p>
      )}
      {research.official_unverified_note && (
        <p style={{ margin: 0, color: "var(--color-status-wait)" }}>
          {research.official_unverified_note}
        </p>
      )}

      {queries.length > 0 && (
        <details>
          <summary style={{ cursor: "pointer", minHeight: "var(--target-size)", fontWeight: 600 }}>本次外部调用记录</summary>
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
        </details>
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
        <details open={posts.length === 0}>
          <summary style={{ cursor: "pointer", minHeight: "var(--target-size)", fontWeight: 600 }}>
            帖链（未取得回复内容，归属以页面为准）
          </summary>
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
        </details>
      )}

      {rejected.length > 0 && (
        <details>
          <summary style={{ cursor: "pointer", minHeight: "var(--target-size)", fontWeight: 600 }}>已剔除的候选（每条附剔除依据）</summary>
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
        </details>
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

      {(research.conflicts ?? []).length > 0 && (
        <div data-testid="tieba-research-conflicts">
          <strong>官方与帖子经历存在冲突</strong>
          <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
            {research.conflicts?.map((conflict, index) => (
              <li key={`${conflict.official_url}-${index}`}>
                <a href={conflict.official_url} target="_blank" rel="noreferrer">
                  {conflict.official_title}
                </a>：{conflict.official_statement}
                {conflict.post_refs?.map((ref, offset) => (
                  <p key={`${ref.url}-${offset}`} style={{ margin: 0 }}>
                    <a href={ref.url} target="_blank" rel="noreferrer">帖子</a>
                    {ref.floor != null ? `第 ${ref.floor} 楼` : "（楼层未知）"}
                    {ref.posted_at ? ` · ${ref.posted_at}` : ""}：{ref.quote}
                  </p>
                ))}
                <p style={{ margin: 0 }}>{conflict.time_basis} {conflict.scope_basis}</p>
                <p style={{ margin: 0 }}>{conflict.note}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {notes.length > 0 && (
        <details>
          <summary style={{ cursor: "pointer", minHeight: "var(--target-size)", fontWeight: 600 }}>证据边界</summary>
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
        </details>
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
