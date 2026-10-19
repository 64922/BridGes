"use client";

import type {
  TiebaOfficialCheck,
  TiebaPostProjection,
  TiebaResearchProjection,
} from "@/lib/api";

import {
  CandidateLinkRow,
  OfficialCheckRow,
  PostRow,
  QueryRecordRow,
  RejectedRow,
  RunMetadataRow,
  formatTime,
} from "./TiebaEvidenceRows";

function targetForumUrl(forum: string): string {
  return `https://tieba.baidu.com/f?kw=${encodeURIComponent(forum.replace(/吧$/, ""))}`;
}

function officialIsApplicable(check: TiebaOfficialCheck): boolean {
  return (
    check.status === "verified" &&
    Boolean(check.excerpt) &&
    check.applicability?.applicable === true
  );
}

function unreadReason(post: TiebaPostProjection): string | null {
  if (!post.replies_obtained) {
    return post.read_error_message || "本轮未取得回复文本。";
  }
  if (post.read_status === "partial" && post.read_error_message) {
    return post.read_error_message;
  }
  return null;
}

/**
 * 贴吧结果的可核验依据：读取范围、局部失败边界、帖链降级、官方核验与
 * 原始帖子。总结正文由 TiebaAnswer 单独呈现；这里不重复总结，只回答
 * “依据是什么、读到哪、还缺什么”。
 */
export function TiebaEvidenceDetails({
  research,
  showSections = true,
}: {
  research: TiebaResearchProjection;
  showSections?: boolean;
}) {
  const posts = research.confirmed_posts ?? [];
  const links = research.candidate_links ?? [];
  const rejected = research.rejected_candidates ?? [];
  const official = research.official_checks ?? [];
  const queries = research.queries ?? [];
  const sections = research.sections ?? [];
  const notes = research.evidence_boundary ?? [];
  const terms = research.topic_terms ?? [];
  const rounds = research.query_rounds ?? [];
  const timeFilter = research.time_filter;
  const partialPosts = posts.filter((post) => unreadReason(post) !== null);
  const applicableOfficial = official.filter(officialIsApplicable);
  const hasOfficialPageWithoutApplicability =
    official.length > 0 && applicableOfficial.length === 0;
  const linksOnly = research.status === "links_only";

  return (
    <div
      data-testid="tieba-evidence"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
        margin: "var(--space-3) 0 0",
        fontSize: "var(--text-sm)",
        color: "var(--color-text-secondary)",
        overflowWrap: "break-word",
      }}
    >
      <p data-testid="tieba-research-scope" style={{ margin: 0 }}>
        检索范围：
        <a
          href={targetForumUrl(research.forum)}
          target="_blank"
          rel="noreferrer"
          style={{ color: "var(--color-accent-secondary)" }}
        >
          <strong>{research.forum}</strong>
        </a>
        {terms.length > 0 ? ` · 原始名词：${terms.join("、")}` : ""}
        {research.official_check_requested ? " · 学校官方域名核验" : ""}
      </p>
      <p data-testid="tieba-time-filter" style={{ margin: 0 }}>
        时间条件：{timeFilter.requirement ? `「${timeFilter.requirement}」` : "未提出"} ·{" "}
        {timeFilter.note}
      </p>

      {partialPosts.length > 0 && (
        <ul
          data-testid="tieba-read-boundary"
          style={{
            margin: 0,
            paddingLeft: "var(--space-5)",
            color: "var(--color-status-wait)",
          }}
        >
          {partialPosts.map((post) => (
            <li key={post.thread_id ?? post.url}>
              {post.title ?? "帖子"}：{unreadReason(post)}
              {post.replies_obtained
                ? "（总结只覆盖本轮读到的楼层，未读部分不进入结论）"
                : "（未取得回复内容，未把缺失部分补写成完整共识）"}
            </li>
          ))}
        </ul>
      )}

      {linksOnly && (
        <p
          data-testid="tieba-links-only"
          style={{ margin: 0, color: "var(--color-status-wait)" }}
        >
          本轮只取得帖链，未取得回复内容；搜索摘要不是已读帖子，也不能当作吧友结论。
        </p>
      )}
      {research.status === "empty" && (
        <p style={{ margin: 0 }}>
          {research.empty_reason ?? "没有找到属于目标贴吧的帖子。"}
          {" 未召回帖子不代表吧内没有相关讨论。"}
        </p>
      )}
      {research.empty_reason && research.status !== "empty" && (
        <p style={{ margin: 0 }}>{research.empty_reason}</p>
      )}

      {links.length > 0 && (
        <details open={posts.length === 0} data-testid="tieba-links-details">
          <summary style={{ cursor: "pointer", minHeight: "var(--target-size)" }}>
            候选帖链（搜索摘要发现，归属未确认，未取得回复内容）
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

      {(official.length > 0 || research.official_check_requested) && (
        <section
          data-testid="tieba-official-section"
          style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}
        >
          <strong style={{ color: "var(--color-text-primary)" }}>
            学校官方页面核验（与上面的吧友经历分列）
          </strong>
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
            <p
              data-testid="tieba-official-unverified"
              style={{ margin: 0, color: "var(--color-status-wait)" }}
            >
              本轮未取得学校官方页面，官方规定保持未核实；以上贴吧内容不构成官方结论。
            </p>
          )}
          {hasOfficialPageWithoutApplicability && (
            <p
              data-testid="tieba-official-not-applicable"
              style={{ margin: 0, color: "var(--color-status-wait)" }}
            >
              已取得官方页面，但没有确认适用于本次问题的原文，不显示为官方确认。
            </p>
          )}
          {research.official_unverified_note && (
            <p
              data-testid="tieba-official-note"
              style={{ margin: 0, color: "var(--color-status-wait)" }}
            >
              {research.official_unverified_note}
            </p>
          )}
          {research.official_blocked_reason && (
            <p style={{ margin: 0 }}>来源限制：{research.official_blocked_reason}</p>
          )}
        </section>
      )}

      {(research.conflicts ?? []).length > 0 && (
        <section
          data-testid="tieba-research-conflicts"
          style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}
        >
          <strong style={{ color: "var(--color-text-primary)" }}>
            官方与帖子经历存在冲突
          </strong>
          <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
            {research.conflicts?.map((conflict, index) => (
              <li key={`${conflict.official_url}-${index}`}>
                <a href={conflict.official_url} target="_blank" rel="noreferrer">
                  {conflict.official_title}
                </a>
                ：{conflict.official_statement}
                {conflict.post_refs?.map((ref, offset) => (
                  <p key={`${ref.url}-${offset}`} style={{ margin: 0 }}>
                    <a href={ref.url} target="_blank" rel="noreferrer">
                      帖子
                    </a>
                    {ref.floor != null ? `第 ${ref.floor} 楼` : "（楼层未知）"}
                    {ref.posted_at ? ` · ${ref.posted_at}` : ""}：{ref.quote}
                  </p>
                ))}
                <p style={{ margin: 0 }}>
                  {conflict.time_basis} {conflict.scope_basis}
                </p>
                <p style={{ margin: 0 }}>{conflict.note}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {posts.length > 0 && (
        <details data-testid="tieba-posts-details">
          <summary style={{ cursor: "pointer", minHeight: "var(--target-size)" }}>
            已确认属于{research.forum}的帖子与实际采集范围（{posts.length} 帖）
          </summary>
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
        </details>
      )}

      {rounds.length > 0 && (
        <details data-testid="tieba-query-plan">
          <summary style={{ cursor: "pointer", minHeight: "var(--target-size)" }}>
            检索计划（每轮目标与实际检索词）
          </summary>
          <ul
            data-testid="tieba-query-plan-rounds"
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
            {rounds.map((round, index) => (
              <li key={`${round.goal}-${index}`}>
                {round.goal}：{round.execution_terms}
              </li>
            ))}
          </ul>
        </details>
      )}

      {queries.length > 0 && (
        <details>
          <summary style={{ cursor: "pointer", minHeight: "var(--target-size)" }}>
            本次外部调用记录
          </summary>
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
              <QueryRecordRow
                key={`${record.source}-${record.query}-${index}`}
                record={record}
              />
            ))}
          </ul>
        </details>
      )}

      {rejected.length > 0 && (
        <details>
          <summary style={{ cursor: "pointer", minHeight: "var(--target-size)" }}>
            已剔除的候选（每条附剔除依据）
          </summary>
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

      {showSections && sections.length > 0 && (
        <details>
          <summary style={{ cursor: "pointer", minHeight: "var(--target-size)" }}>
            按规则摘录的已读原文
          </summary>
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
        </details>
      )}

      {notes.length > 0 && (
        <ul
          data-testid="tieba-evidence-boundary"
          style={{
            margin: 0,
            paddingLeft: "var(--space-5)",
            color: "var(--color-text-tertiary)",
            fontSize: "var(--text-xs)",
          }}
        >
          {notes.map((note, index) => (
            <li key={`note-${index}`}>{note}</li>
          ))}
        </ul>
      )}

      {research.run_metadata && <RunMetadataRow metadata={research.run_metadata} />}

      <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        本轮完成于 {formatTime(research.completed_at)}
      </p>
    </div>
  );
}
