"use client";

import { ButtonLink } from "@/components/design-system/ButtonLink";
import { Icon } from "@/components/design-system/Icon";
import type {
  CommutePlaceCandidate,
  CommuteRouteProjection,
} from "@/lib/api";
import {
  AMAP_BROWSER_MAP_ANCHOR,
  AMAP_WEB_SERVICE_ANCHOR,
  credentialSettingsHref,
} from "@/lib/settings-links";
import type { ChatMessageView } from "@/lib/chat-message-view";

import { CommuteRouteMap } from "./CommuteRouteMap";
import { commuteCandidateScope } from "@/lib/commute-format";

const AMAP_WEB_SERVICE_ERROR_CODES = new Set(["amap_not_configured", "amap_key_rejected"]);

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
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
      <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        {title}
      </p>
      <ol
        data-testid={testId}
        style={{
          margin: 0,
          paddingLeft: "var(--space-5)",
          listStyle: "decimal",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-1)",
          fontSize: "var(--text-sm)",
        }}
      >
        {candidates.map((candidate, index) => (
          <li key={`${candidate.poi_id ?? candidate.name}-${index}`}>
            <span style={{ color: "var(--color-text-primary)", fontWeight: 600 }}>
              {index + 1}. {candidate.name}
            </span>
            {candidate.address ? ` · ${candidate.address}` : ""}
            {candidate.district ? ` · ${candidate.district}` : ""}
            {` · ${commuteCandidateScope(candidate)}`}
          </li>
        ))}
      </ol>
    </div>
  );
}

function ClarificationBlock({ route }: { route: CommuteRouteProjection }) {
  const originCandidates = route.origin_candidates ?? [];
  const destinationCandidates = route.destination_candidates ?? [];
  const hasCandidates = originCandidates.length > 0 || destinationCandidates.length > 0;

  const focusComposer = () => {
    const composer = document.getElementById("composer-input");
    if (!(composer instanceof HTMLElement)) return;
    composer.scrollIntoView?.({ block: "nearest" });
    composer.focus({ preventScroll: true });
  };

  return (
    <section
      data-testid="commute-clarification"
      role="status"
      aria-live="polite"
      style={noticeStyle}
    >
      <CandidateList
        title="起点候选（高德真实返回）"
        candidates={originCandidates}
        testId="commute-route-origin-candidates"
      />
      <CandidateList
        title="终点候选（高德真实返回）"
        candidates={destinationCandidates}
        testId="commute-route-destination-candidates"
      />
      <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
        {hasCandidates
          ? "直接在下方回复序号或地点名称即可；回复会续接上一轮校园通勤请求。"
          : "直接在下方补充缺少的地点、方式或更明确的建筑名称；回复会续接上一轮校园通勤请求。"}
      </p>
      <button type="button" data-testid="commute-answer-focus-composer" onClick={focusComposer} style={secondaryButtonStyle}>
        <Icon name="chevronRight" size={14} aria-hidden /> 去输入框回复
      </button>
    </section>
  );
}

export function CommuteAnswer({
  message,
  conversationId,
  onRetry,
  onOpenDetails,
}: {
  message: ChatMessageView;
  conversationId?: string;
  onRetry?: (id: string) => void;
  onOpenDetails?: (
    messageId: string,
    route: CommuteRouteProjection,
    trigger: HTMLButtonElement
  ) => void;
}) {
  const route = message.commuteRoute ?? null;
  if (!route || !conversationId) return null;

  const streaming = message.status === "streaming";
  const hasRoute = route.status === "success" || route.status === "unverified";
  const failed = route.status === "error";
  const credentialFailure =
    failed && route.error_code != null && AMAP_WEB_SERVICE_ERROR_CODES.has(route.error_code);
  const deliveryHandlesRecovery = Boolean(message.turnResult?.recovery);
  const showRetry =
    failed && route.retryable && Boolean(onRetry) && !deliveryHandlesRecovery;
  const detailsAvailable = hasRoute || failed || route.status === "stopped";

  return (
    <section
      data-testid={`commute-answer-${route.status}`}
      aria-busy={streaming}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-3)",
        marginTop: "var(--space-3)",
        fontSize: "var(--text-sm)",
        color: "var(--color-text-secondary)",
      }}
    >
      {hasRoute && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          <p style={{ margin: 0, fontWeight: 600, color: "var(--color-text-primary)" }}>路线地图</p>
          <CommuteRouteMap
            polyline={route.polyline ?? []}
            originLabel={route.origin?.name ?? null}
            destinationLabel={route.destination?.name ?? null}
            browserMapSettingsHref={credentialSettingsHref(
              AMAP_BROWSER_MAP_ANCHOR,
              `/chat/${conversationId}`
            )}
          />
        </div>
      )}

      {route.status === "clarification" && <ClarificationBlock route={route} />}

      {failed && (
        <div
          role="alert"
          data-testid="commute-error"
          style={{
            ...noticeStyle,
            borderColor: "var(--color-status-error)",
            backgroundColor: "var(--color-status-error-bg)",
            color: "var(--color-status-error)",
          }}
        >
          {route.error_code && (
            <p style={{ margin: 0, fontSize: "var(--text-xs)" }}>
              错误码：{route.error_code}
            </p>
          )}
          {route.error_message && <p style={{ margin: 0 }}>{route.error_message}</p>}
          {credentialFailure && (
            <div
              data-testid="commute-route-credential-action"
              style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}
            >
              <p style={{ margin: 0 }}>
                路线与地点查询使用「高德 Web 服务 Key」，与页面底图的「高德浏览器地图」凭据分开配置。
              </p>
              <ButtonLink
                href={credentialSettingsHref(
                  AMAP_WEB_SERVICE_ANCHOR,
                  `/chat/${conversationId}`
                )}
                variant="secondary"
              >
                前往配置高德 Web 服务 Key
              </ButtonLink>
            </div>
          )}
          {showRetry && (
            <button
              type="button"
              data-testid="commute-answer-retry"
              onClick={() => onRetry?.(message.id)}
              style={secondaryButtonStyle}
            >
              <Icon name="retry" size={14} aria-hidden /> 重试校园通勤
            </button>
          )}
        </div>
      )}

      {detailsAvailable && onOpenDetails && (
        <div>
          <button
            type="button"
            data-testid="commute-answer-details"
            onClick={(event) => onOpenDetails(message.id, route, event.currentTarget)}
            style={secondaryButtonStyle}
          >
            <Icon name="route" size={14} aria-hidden /> 查看路线详情
          </button>
        </div>
      )}
    </section>
  );
}

const noticeStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-2)",
  padding: "var(--space-3)",
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-md)",
  backgroundColor: "var(--color-bg-secondary)",
  fontSize: "var(--text-sm)",
  color: "var(--color-text-secondary)",
};

const secondaryButtonStyle: React.CSSProperties = {
  alignSelf: "flex-start",
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-1)",
  minHeight: "var(--target-size)",
  padding: "var(--space-1) var(--space-3)",
  border: "1px solid var(--color-border-strong)",
  borderRadius: "var(--radius-md)",
  backgroundColor: "transparent",
  color: "var(--color-text-primary)",
  font: "inherit",
  fontSize: "var(--text-sm)",
  fontWeight: 600,
  cursor: "pointer",
};
