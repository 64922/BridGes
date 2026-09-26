"use client";

import { Icon } from "@/components/design-system/Icon";
import type {
  CommuteBreakBuffer,
  CommutePlace,
  CommutePlaceCandidate,
  CommuteRouteProjection,
  CommuteRouteStep,
  ModuleQueryRecord,
} from "@/lib/api";
import { CommuteRouteMap } from "./CommuteRouteMap";

/** V2 Issue 12：通勤模块状态的中文标题（每条助手消息内如实显示）。 */
const STATUS_TITLES: Record<string, string> = {
  clarification: "需要先确认一个问题",
  success: "校园通勤路线",
  unverified: "路线已查到，但没有可核验的路径点",
  error: "校园通勤未完成",
  stopped: "已停止校园通勤",
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
  amap_place: "高德地点检索",
  amap_route: "高德路线规划",
};

function formatTime(value: string | null | undefined): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN");
}

/** 与服务端 presenting.py 同一口径：米或公里、秒或分钟，避免两处数字口径不一致。 */
function formatDistance(meters: number | null | undefined): string {
  if (meters === null || meters === undefined) return "未知";
  if (meters < 1000) return `${meters} 米`;
  return `${(meters / 1000).toFixed(1)} 公里`;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "未知";
  if (seconds < 60) return `${seconds} 秒`;
  const totalMinutes = Math.max(1, Math.round(seconds / 60));
  if (totalMinutes < 60) return `${totalMinutes} 分钟`;
  const hours = Math.floor(totalMinutes / 60);
  const rest = totalMinutes % 60;
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}

function placeValue(place: CommutePlace): string {
  const address = place.address ? `，${place.address}` : "";
  const scope = place.campus_verified ? "校内或校门" : "未确认校内";
  return `${place.name}${address}（${scope}·高德 POI）`;
}

/** 一个地点字段：标签 + 真实取值（原话与坐标只在卡内以文本披露）。 */
function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
      <dt style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>{label}</dt>
      <dd style={{ margin: 0, color: "var(--color-text-primary)" }}>{children}</dd>
    </div>
  );
}

function CandidateList({
  title,
  candidates,
  testId,
}: {
  title: string;
  candidates: readonly CommutePlaceCandidate[];
  testId: string;
}) {
  if (candidates.length === 0) return null;
  return (
    <div>
      <p style={{ margin: 0, fontWeight: 600 }}>{title}</p>
      <ol
        data-testid={testId}
        style={{
          margin: 0,
          paddingLeft: "var(--space-5)",
          listStyle: "decimal",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-1)",
        }}
      >
        {candidates.map((candidate, index) => (
          <li key={`${candidate.poi_id ?? candidate.name}-${index}`}>
            {candidate.name}
            {candidate.address ? ` · ${candidate.address}` : ""}
            {candidate.district ? ` · ${candidate.district}` : ""}
            {candidate.campus ? " · 校内" : " · 未确认校内"}
          </li>
        ))}
      </ol>
    </div>
  );
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

function StepRow({ step }: { step: CommuteRouteStep }) {
  return (
    <li data-testid={`commute-route-step-${step.index}`}>
      {step.instruction}
      {step.road_name ? `（${step.road_name}）` : ""}
      {step.distance_m ? `，${formatDistance(step.distance_m)}` : ""}
    </li>
  );
}

/** 课间规则缓冲：命中时写明「可能人多」与增加的分钟数，永远附规则声明。 */
function bufferLine(buffer: CommuteBreakBuffer): string {
  if (buffer.in_window && buffer.matched_break_time) {
    return `可能人多：命中课间点 ${buffer.matched_break_time} 前后 ${buffer.minutes_away} 分钟，建议加 ${buffer.added_minutes} 分钟。`;
  }
  return "当前不在课间高峰窗口内，不加缓冲。";
}

/**
 * 校园通勤路线卡（V2 Issue 12）。
 *
 * 只渲染投影里的真实内容：按交互规格 §4.1 的顺序给起点、终点、方式、距离、
 * 高德基础耗时、课间缓冲与建议总时间，再给可缩放地图与路线文字，最后是本次
 * 外部调用记录与证据边界。澄清候选、失败与停止都在同一条消息内如实呈现；
 * 未取得路径点时只显示已证实的地点并说明没有画线，绝不绘制猜测路线。
 */
export function CommuteRouteCard({
  route,
  streaming,
  onRetry,
}: {
  route: CommuteRouteProjection | null;
  streaming: boolean;
  onRetry?: () => void;
}) {
  if (!route) return null;
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
  const failed = route.status === "error";
  const waiting = route.status === "clarification";
  const hasRoute = route.status === "success" || route.status === "unverified";
  const steps = route.steps ?? [];
  const queries = route.queries ?? [];
  const notes = route.evidence_notes ?? [];
  const polyline = route.polyline ?? [];
  const origin = route.origin ?? null;
  const destination = route.destination ?? null;

  return (
    <section
      data-testid={`commute-route-card-${route.status}`}
      role={failed ? "alert" : "status"}
      aria-live="polite"
      aria-busy={streaming}
      style={{ ...shellStyle, borderColor: failed ? "var(--color-status-error)" : "var(--color-border)" }}
    >
      <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "var(--space-2)" }}>
        <Icon name={failed ? "alert" : waiting ? "info" : "route"} size={16} aria-hidden />
        <strong style={{ color: failed ? "var(--color-status-error)" : "var(--color-text-primary)" }}>
          {STATUS_TITLES[route.status] ?? "校园通勤模块"}
        </strong>
        {route.mode_label && (
          <span
            data-testid="commute-route-mode"
            style={{
              marginLeft: "auto",
              padding: "2px var(--space-2)",
              borderRadius: "var(--radius-full)",
              backgroundColor: "var(--color-status-info-bg)",
              color: "var(--color-status-info)",
              fontSize: "var(--text-xs)",
              fontWeight: 600,
            }}
          >
            {route.mode_label}
            {route.mode_phrase && route.mode_phrase !== route.mode_label
              ? `（你说的是「${route.mode_phrase}」）`
              : ""}
          </span>
        )}
      </div>

      {hasRoute && (
        <dl
          data-testid="commute-route-facts"
          style={{
            margin: 0,
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(8.5rem, 1fr))",
            gap: "var(--space-2)",
            fontSize: "var(--text-xs)",
          }}
        >
          <Fact label="起点">{origin ? placeValue(origin) : "未知"}</Fact>
          <Fact label="终点">{destination ? placeValue(destination) : "未知"}</Fact>
          <Fact label="距离">{formatDistance(route.distance_m)}</Fact>
          <Fact label="高德基础耗时">{formatDuration(route.base_duration_seconds)}</Fact>
          <Fact label="课间缓冲">
            {route.buffer ? bufferLine(route.buffer) : "未判定"}
          </Fact>
          <Fact label="建议总时间">
            {formatDuration(route.suggested_total_seconds)}
          </Fact>
        </dl>
      )}

      {waiting && route.pending && (
        <div
          data-testid="commute-route-clarification"
          style={{
            padding: "var(--space-2) var(--space-3)",
            border: "1px solid var(--color-border-strong)",
            borderRadius: "var(--radius-md)",
            backgroundColor: "var(--color-surface)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-1)",
          }}
        >
          <p style={{ margin: 0, fontWeight: 600, color: "var(--color-text-primary)" }}>
            {route.pending.question}
          </p>
          <CandidateList
            title="起点候选（高德真实返回）"
            candidates={route.origin_candidates ?? []}
            testId="commute-route-origin-candidates"
          />
          <CandidateList
            title="终点候选（高德真实返回）"
            candidates={route.destination_candidates ?? []}
            testId="commute-route-destination-candidates"
          />
          <p style={{ margin: 0, fontSize: "var(--text-xs)" }}>
            直接在下方回复序号或地点名称即可；回复会带着「校园通勤」模块发出（输入框上方的模块标签可随时移除）。
          </p>
        </div>
      )}

      {failed && (
        <p style={{ margin: 0, color: "var(--color-status-error)" }}>
          {route.error_message ?? "本轮校园通勤未完成。"}
          {route.error_code ? `（错误码：${route.error_code}）` : ""}
        </p>
      )}

      {hasRoute && (
        <div>
          <p style={{ margin: 0, fontWeight: 600 }}>路线地图</p>
          <CommuteRouteMap
            polyline={polyline}
            originLabel={origin?.name ?? null}
            destinationLabel={destination?.name ?? null}
          />
        </div>
      )}

      {steps.length > 0 && (
        <div>
          <p style={{ margin: 0, fontWeight: 600 }}>路线文字（高德返回的路段）</p>
          <ol
            data-testid="commute-route-steps"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "decimal",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
            }}
          >
            {steps.map((step) => (
              <StepRow key={`${step.index}-${step.instruction}`} step={step} />
            ))}
          </ol>
        </div>
      )}

      {route.buffer && (
        <p
          data-testid="commute-route-buffer-note"
          style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-status-wait)" }}
        >
          {route.buffer.rule_note}
        </p>
      )}

      {queries.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            本次外部调用记录
          </p>
          <ul
            data-testid="commute-route-queries"
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

      {notes.length > 0 && (
        <div>
          <p style={{ margin: 0, fontWeight: 600 }}>证据边界</p>
          <ul
            data-testid="commute-route-notes"
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

      {route.retryable && onRetry && (
        <button
          type="button"
          data-testid="commute-route-retry"
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
          <Icon name="retry" size={14} aria-hidden /> 重试校园通勤
        </button>
      )}
    </section>
  );
}
