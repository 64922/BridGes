"use client";

/**
 * 文章结果投影的前端遥测（Issue 08 Observability）。
 *
 * 只记录投影版本、交付状态、风险类型、展开/复制事件与 legacy 读取数；
 * 绝不携带正文、引语或逐项 diff 内容。事件经 keepalive 上报到
 * POST /api/chat/humanizer/events，服务端只落审计计数。
 */

/** 可上报的事件类型（稳定 code，服务端按此计数）。 */
export type ArticleProjectionEventKind =
  | "expand"
  | "copy"
  | "legacy_read";

export interface ArticleProjectionEvent {
  event: ArticleProjectionEventKind;
  projection_version: string | null;
  delivery_status: string | null;
  risk_types: string[];
  legacy: boolean;
}

function collectRiskTypes(input: {
  fidelityBlocking?: number;
  needsConfirmation?: number;
  styleWarnings?: number;
  evidenceKinds?: string[];
  revisionTriggered?: boolean;
}): string[] {
  const types: string[] = [];
  if ((input.fidelityBlocking ?? 0) > 0) types.push("fidelity_blocking");
  if ((input.needsConfirmation ?? 0) > 0) types.push("needs_confirmation");
  if ((input.styleWarnings ?? 0) > 0) types.push("style_warning");
  for (const kind of input.evidenceKinds ?? []) types.push(`evidence_${kind}`);
  if (input.revisionTriggered) types.push("revision_triggered");
  return types;
}

/** 上报一次投影事件；失败静默（遥测不阻断界面）。 */
export function trackArticleProjectionEvent(
  payload: Omit<ArticleProjectionEvent, "event">,
  kind: ArticleProjectionEventKind
): void {
  const body: ArticleProjectionEvent = { ...payload, event: kind };
  try {
    void fetch("/api/chat/humanizer/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => {
      /* 遥测失败不打扰用户 */
    });
  } catch {
    /* keepalive 不可用（如非浏览器环境）时静默 */
  }
}

export { collectRiskTypes };
