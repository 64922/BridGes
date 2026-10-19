"use client";

import type { TiebaRunMetadata } from "@/lib/api";

type RequestBreakdown = NonNullable<
  TiebaRunMetadata["request_breakdown"]
>[number];
type BrowserCleanup = NonNullable<TiebaRunMetadata["browser_cleanup"]>;

const SCOPE_EXCLUSION_LABELS: Record<string, string> = {
  campus_conflict: "明确其他校区",
  campus_unconfirmed: "校区未确认",
  other_topic: "其他话题",
  topic_mismatch: "话题不相关",
  out_of_time_range: "时间范围外",
  unknown_time: "发言时间未知",
};

const REQUEST_TYPE_LABELS: Record<string, string> = {
  search: "检索",
  read: "读取",
  official: "官方页面",
  browser: "浏览器",
  other: "其他",
};

const STAGE_LABELS: Record<string, string> = {
  "tieba.parse": "解析",
  "tieba.plan": "计划",
  "tieba.verify_official": "官方核验",
  "tieba.search": "检索",
  "tieba.read": "读取",
  "tieba.synthesize": "综合",
  "tieba.verify": "核验",
};

const CLOSEOUT_STAGE_LABELS: Record<string, string> = {
  search: "检索",
  read: "读取",
  official: "官方",
};

const CLOSEOUT_LABELS: Record<string, string> = {
  completed: "完成",
  evidence_sufficient: "证据足够",
  deadline: "到达截止",
  stopped: "已停止",
  auth_required: "需要认证",
  budget_exhausted: "预算耗尽",
  rate_limited: "被上游限流",
  deepen_incomplete: "深入未完成",
  transient_error: "临时错误",
  permanent_error: "永久错误",
  cancelled: "已取消",
  skipped: "未执行",
  blocked: "已阻断",
  no_planned_queries: "无需查询",
};

export function formatTime(value: string | null | undefined): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN");
}

/** 排除原因的固定中文说明；未知原因保留原键以便核对。 */
export function scopeExclusionLabel(reason: string): string {
  return SCOPE_EXCLUSION_LABELS[reason] ?? reason;
}

function durationLabel(value: number | null | undefined): string {
  if (value == null) return "未知";
  return value < 1000 ? `${value} 毫秒` : `${(value / 1000).toFixed(1)} 秒`;
}

function requestBreakdownLabel(item: RequestBreakdown): string {
  const type = REQUEST_TYPE_LABELS[item.request_type] ?? item.request_type;
  return `${type}（${item.purpose}） ${item.count ?? 0} 次 · 网络 ${durationLabel(
    item.network_ms
  )} · 排队 ${durationLabel(item.queued_ms)}`;
}

function browserCleanupLabel(cleanup: BrowserCleanup): string {
  const timedOut = (cleanup.timed_out ?? 0) > 0 ? ` · 超时 ${cleanup.timed_out} 次` : "";
  return `浏览器清理 ${cleanup.count ?? 0} 次 · 最大 ${durationLabel(
    cleanup.max_ms
  )} · 容差 ${durationLabel(cleanup.tolerance_ms)}${timedOut}`;
}

export function RunMetadataRow({ metadata }: { metadata: TiebaRunMetadata }) {
  const elapsed =
    metadata.elapsed_ms == null
      ? "耗时未知"
      : metadata.elapsed_ms < 1000
        ? `${metadata.elapsed_ms} 毫秒`
        : `${(metadata.elapsed_ms / 1000).toFixed(1)} 秒`;
  const versions = Object.entries(metadata.component_versions ?? {});
  const breakdown = metadata.request_breakdown ?? [];
  const stages = Object.entries(metadata.stage_timings_ms ?? {});
  const closeouts = Object.entries(metadata.closeout_reasons ?? {});
  return (
    <details data-testid="tieba-run-metadata">
      <summary style={{ cursor: "pointer", minHeight: "var(--target-size)" }}>
        运行记录（请求节奏、阶段耗时与未读范围）
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
          运行 {metadata.run_id} · 配方 {metadata.recipe_version} · 契约{" "}
          {metadata.contract_version}
        </li>
        <li>
          开始 {formatTime(metadata.started_at)} · 完成 {formatTime(metadata.completed_at)} ·{" "}
          {elapsed}
        </li>
        <li>
          外部请求 {metadata.request_count} 次：检索 {metadata.search_request_count}、搜索页{" "}
          {metadata.search_page_count ?? 0}、读取 {metadata.read_request_count}、官方{" "}
          {metadata.official_request_count}
        </li>
        <li data-testid="tieba-run-rhythm">
          首轮覆盖 {metadata.first_pass_post_count ?? 0} 帖 · 深入{" "}
          {metadata.deepened_post_count ?? 0} 帖
          {metadata.physical_request_count
            ? ` · 物理请求 ${metadata.physical_request_count} 次`
            : ""}
          {metadata.remaining_work_ms != null
            ? ` · 剩余工作预算 ${durationLabel(metadata.remaining_work_ms)}`
            : ""}
        </li>
        {breakdown.length > 0 && (
          <li data-testid="tieba-request-breakdown">
            请求节奏：
            <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
              {breakdown.map((item) => (
                <li key={`${item.request_type}-${item.purpose}`}>
                  {requestBreakdownLabel(item)}
                </li>
              ))}
            </ul>
          </li>
        )}
        {stages.length > 0 && (
          <li data-testid="tieba-stage-timings">
            阶段耗时：
            {stages
              .map(([node, duration]) => {
                const label = STAGE_LABELS[node] ?? node;
                return `${label} ${durationLabel(duration)}`;
              })
              .join(" · ")}
          </li>
        )}
        {closeouts.length > 0 && (
          <li data-testid="tieba-closeout-reasons">
            收尾：
            {closeouts
              .map(([stage, reason]) => {
                const stageLabel = CLOSEOUT_STAGE_LABELS[stage] ?? stage;
                return `${stageLabel} ${CLOSEOUT_LABELS[reason] ?? reason}`;
              })
              .join(" · ")}
          </li>
        )}
        {metadata.browser_cleanup && (
          <li data-testid="tieba-browser-cleanup">
            {browserCleanupLabel(metadata.browser_cleanup)}
          </li>
        )}
        <li>
          模型调用 {metadata.model_call_count} 次 · 确认帖子 {metadata.confirmed_post_count} 个 ·
          记录楼层 {metadata.read_reply_count} 条
        </li>
        <li data-testid="tieba-evidence-counts">
          候选 {metadata.candidate_count ?? 0} 条 · 可读取 {metadata.usable_candidate_count ?? 0}{" "}
          条 · 已读 {metadata.read_post_count ?? 0} 条 · 合格证据{" "}
          {metadata.qualified_evidence_count ?? 0} 条 · 仅上下文{" "}
          {metadata.context_only_evidence_count ?? 0} 条 · 排除{" "}
          {metadata.excluded_evidence_count ?? 0} 条
          {Object.keys(metadata.exclusion_reasons ?? {}).length > 0
            ? `（${Object.entries(metadata.exclusion_reasons ?? {})
                .map(([reason, count]) => `${scopeExclusionLabel(reason)} ${count} 条`)
                .join("、")}）`
            : ""}
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
