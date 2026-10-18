"use client";

import { useEffect, useRef } from "react";

import { Icon } from "@/components/design-system/Icon";
import type {
  CommuteBreakBuffer,
  CommutePlace,
  CommuteRouteProjection,
  ModuleQueryRecord,
} from "@/lib/api";
import {
  commutePlaceScope,
  formatCommuteDistance,
  formatCommuteDuration,
  formatCommuteTime,
} from "@/lib/commute-format";

import styles from "./CommuteDetailPanel.module.css";

const STATUS_LABELS: Record<string, string> = {
  success: "路线已取得",
  unverified: "路线未取得可核验路径点",
  error: "本轮未完成",
  stopped: "本轮已停止",
  clarification: "等待补充地点或方式",
};

const SOURCE_LABELS: Record<string, string> = {
  amap_place: "高德地点检索",
  amap_route: "高德路线规划",
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

function PlaceDetail({ label, place }: { label: string; place: CommutePlace }) {
  const unverified = place.unverified ?? [];
  return (
    <div className={styles.place} data-testid={`commute-detail-place-${place.role}`}>
      <p className={styles.placeHeading}>
        <span>{label}</span>
        <strong>{place.name}</strong>
        <span className={place.campus_verified ? styles.scopeVerified : styles.scopeUnverified}>
          {commutePlaceScope(place)}
        </span>
      </p>
      <dl className={styles.facts}>
        <dt>地址</dt>
        <dd>{place.address ?? "高德未返回地址"}</dd>
        <dt>区县</dt>
        <dd>{place.district ?? "高德未返回区县"}</dd>
        <dt>用户原话</dt>
        <dd>「{place.original_phrase}」</dd>
        <dt>实际检索词</dt>
        <dd>「{place.query}」</dd>
        <dt>坐标</dt>
        <dd>{place.location}</dd>
        <dt>POI</dt>
        <dd>{place.poi_id ?? "高德未返回 POI 标识"}</dd>
        <dt>核实依据</dt>
        <dd>{place.match_basis}</dd>
      </dl>
      {unverified.length > 0 && (
        <ul className={styles.notes} data-testid={`commute-detail-unverified-${place.role}`}>
          {unverified.map((item, index) => (
            <li key={`${item}-${index}`}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function BufferDetail({ buffer }: { buffer: CommuteBreakBuffer }) {
  return (
    <div data-testid="commute-detail-buffer">
      <dl className={styles.facts}>
        <dt>课间窗口</dt>
        <dd>{buffer.in_window ? "命中课间前后窗口" : "当前不在课间高峰窗口内"}</dd>
        <dt>命中时间</dt>
        <dd>{buffer.matched_break_time ?? "未命中"}</dd>
        <dt>与课间点相差</dt>
        <dd>{buffer.minutes_away === null || buffer.minutes_away === undefined ? "未知" : `${buffer.minutes_away} 分钟`}</dd>
        <dt>建议增加</dt>
        <dd>{buffer.added_minutes} 分钟</dd>
        <dt>判定时刻</dt>
        <dd>{formatCommuteTime(buffer.checked_at)}</dd>
        <dt>时区</dt>
        <dd>{buffer.timezone}</dd>
      </dl>
      <p className={styles.note}>{buffer.rule_note}</p>
    </div>
  );
}

function QueryRecordRow({ record }: { record: ModuleQueryRecord }) {
  const failed = record.status === "error" || record.status === "timeout";
  return (
    <li
      data-testid="commute-detail-query"
      className={failed ? styles.queryFailed : undefined}
    >
      <strong>{SOURCE_LABELS[record.source] ?? record.source}</strong>
      {" · 查询「"}
      {record.query}
      {"」 · "}
      {QUERY_STATUS_LABELS[record.status] ?? record.status}
      {`（${record.evidence_count} 条）`}
      {" · "}
      {formatCommuteTime(record.retrieved_at)}
      {record.error_message ? `：${record.error_message}` : ""}
    </li>
  );
}

export function CommuteDetailPanel({
  selection,
  onClose,
}: {
  selection: { messageId: string; route: CommuteRouteProjection } | null;
  onClose: () => void;
}) {
  if (!selection) return null;
  const key = `${selection.messageId}:${selection.route.resolved_at ?? ""}:${(selection.route.polyline ?? []).join("|")}`;
  return <CommuteDetailContent key={key} route={selection.route} onClose={onClose} />;
}

function CommuteDetailContent({
  route,
  onClose,
}: {
  route: CommuteRouteProjection;
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLElement>(null);
  const origin = route.origin ?? null;
  const destination = route.destination ?? null;
  const steps = route.steps ?? [];
  const queries = route.queries ?? [];
  const notes = route.evidence_notes ?? [];
  const modeLabel = route.mode_label ?? "出行方式未知";
  const title =
    origin && destination ? `${origin.name} → ${destination.name}` : "校园通勤路线详情";

  useEffect(() => {
    panelRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.isComposing) return;
      event.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <aside
      ref={panelRef}
      id="commute-detail-viewer"
      role="dialog"
      aria-modal="false"
      tabIndex={-1}
      aria-labelledby="commute-detail-title"
      className={styles.panel}
      data-testid="commute-detail-panel"
    >
      <header className={styles.header}>
        <div className={styles.titleGroup}>
          <span className={styles.kicker}>校园通勤</span>
          <h2 id="commute-detail-title" className={styles.title}>
            {title}
          </h2>
        </div>
        <button
          type="button"
          aria-label="关闭路线详情"
          className={styles.closeButton}
          data-testid="commute-detail-close"
          onClick={onClose}
        >
          <Icon name="close" size={18} aria-hidden />
        </button>
      </header>

      <p className={styles.meta} data-testid="commute-detail-meta">
        {modeLabel}
        {route.mode_phrase && route.mode_phrase !== route.mode_label
          ? `（用户说「${route.mode_phrase}」）`
          : ""}
        {" · "}
        {STATUS_LABELS[route.status] ?? "状态未核实"}
        {route.resolved_at ? ` · ${formatCommuteTime(route.resolved_at)}` : ""}
      </p>

      <div className={styles.body}>
        {route.status === "error" && (
          <section className={styles.section} data-testid="commute-detail-error">
            <h3 className={styles.heading}>失败信息</h3>
            <p className={styles.errorText}>{route.error_message ?? "本轮校园通勤未完成。"}</p>
            {route.error_code && <p className={styles.note}>错误码：{route.error_code}</p>}
          </section>
        )}

        {route.status === "clarification" && route.pending && (
          <section className={styles.section} data-testid="commute-detail-clarification">
            <h3 className={styles.heading}>等待补充</h3>
            <p className={styles.note}>{route.pending.question}</p>
          </section>
        )}

        <section className={styles.section} data-testid="commute-detail-places">
          <h3 className={styles.heading}>地点核实</h3>
          {origin ? (
            <PlaceDetail label="起点" place={origin} />
          ) : (
            <p className={styles.note}>本轮没有已确认的起点。</p>
          )}
          {destination ? (
            <PlaceDetail label="终点" place={destination} />
          ) : (
            <p className={styles.note}>本轮没有已确认的终点。</p>
          )}
        </section>

        <section className={styles.section} data-testid="commute-detail-time">
          <h3 className={styles.heading}>时间与缓冲</h3>
          <dl className={styles.facts}>
            <dt>距离</dt>
            <dd>{formatCommuteDistance(route.distance_m)}</dd>
            <dt>高德基础耗时</dt>
            <dd>{formatCommuteDuration(route.base_duration_seconds)}（高德估计，不是保证到达时间）</dd>
            <dt>建议总时间</dt>
            <dd>
              {formatCommuteDuration(route.suggested_total_seconds)}
              （基础耗时加上课间规则缓冲的估计值，不是保证到达时间）
            </dd>
          </dl>
          {route.buffer ? (
            <BufferDetail buffer={route.buffer} />
          ) : (
            <p className={styles.note}>本轮没有课间缓冲判定。</p>
          )}
        </section>

        <section className={styles.section} data-testid="commute-detail-steps">
          <h3 className={styles.heading}>完整路线文字（高德返回）</h3>
          <p className={styles.note} data-testid="commute-detail-map-text">
            {route.path_verified
              ? `地图按高德返回的 ${(route.polyline ?? []).length} 个路径点绘制；文字等价为下方 ${steps.length} 段路线。`
              : "本轮没有取得可核验的路径点，因此没有地图线；以下文字路段是高德实际返回的内容。"}
          </p>
          {steps.length > 0 ? (
            <ol className={styles.steps}>
              {steps.map((step) => (
                <li key={`${step.index}-${step.instruction}`} data-testid={`commute-detail-step-${step.index}`}>
                  {step.index}. {step.instruction}
                  {step.road_name ? `（${step.road_name}）` : ""}
                  {step.distance_m !== null && step.distance_m !== undefined
                    ? `，${formatCommuteDistance(step.distance_m)}`
                    : ""}
                </li>
              ))}
            </ol>
          ) : (
            <p className={styles.note}>高德本轮没有返回文字路段。</p>
          )}
        </section>

        <section className={styles.section} data-testid="commute-detail-evidence">
          <h3 className={styles.heading}>路线证据与外部调用记录</h3>
          {queries.length > 0 ? (
            <ul className={styles.list} data-testid="commute-detail-queries">
              {queries.map((record, index) => (
                <QueryRecordRow key={`${record.source}-${record.query}-${index}`} record={record} />
              ))}
            </ul>
          ) : (
            <p className={styles.note}>本轮没有外部调用记录。</p>
          )}
          {notes.length > 0 && (
            <ul className={styles.list} data-testid="commute-detail-notes">
              {notes.map((note, index) => (
                <li key={`${note}-${index}`}>{note}</li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </aside>
  );
}
