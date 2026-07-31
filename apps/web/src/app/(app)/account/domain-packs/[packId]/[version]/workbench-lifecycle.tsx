"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { StatusBadge } from "@/components/design-system/StatusBadge";
import {
  advanceInvalidation,
  confirmRollback,
  emergencyRevoke,
  executeRollback,
  fetchSecurityAdminStatus,
  getImpactSet,
  getInvalidation,
  getRevalidationReport,
  listInvalidations,
  listRevocations,
  listRollbacks,
  proposeRollback,
  recordInvalidation,
  registerSecurityAdmin,
  reportRevalidated,
  resolveImpact,
  type PackImpactCategory,
  type PackImpactSet,
  type PackInvalidationEvent,
  type PackInvalidationStage,
  type PackInvalidationTrigger,
  type PackRollbackRecord,
  type RevalidationReport,
  type RevocationEvent,
} from "@/lib/api";

const STAGES: { id: PackInvalidationStage; label: string }[] = [
  { id: "detected", label: "检测" },
  { id: "triaged", label: "分诊" },
  { id: "contained", label: "控制" },
  { id: "impacted_objects_found", label: "定位影响" },
  { id: "remediating", label: "修复" },
  { id: "revalidating", label: "重验证" },
  { id: "closed", label: "关闭" },
];

const STAGE_INDEX = Object.fromEntries(
  STAGES.map((stage, index) => [stage.id, index])
) as Record<PackInvalidationStage, number>;

const TRIGGER_LABELS: Record<string, string> = {
  source_retracted: "来源撤回",
  source_status_unknown: "来源状态未知",
  dependency_revoked: "依赖撤销",
  signature_invalid: "签名无效",
  security_event: "安全事件",
  evaluation_regression: "评测回归",
  rule_defect: "规则缺陷",
  review_expired: "复核过期",
};

const CATEGORY_LABELS: Record<string, string> = {
  pack: "包",
  run: "运行",
  claim: "Claim",
  evidence: "Evidence",
  wording: "Wording",
  artifact: "产物",
  project: "项目",
  user_action: "用户动作",
};

const CATEGORY_ORDER = [
  "pack",
  "run",
  "claim",
  "evidence",
  "wording",
  "artifact",
  "project",
  "user_action",
];

const NEXT_STAGE: Record<PackInvalidationStage, PackInvalidationStage | null> = {
  detected: "triaged",
  triaged: "contained",
  contained: "impacted_objects_found",
  impacted_objects_found: "remediating",
  remediating: "revalidating",
  revalidating: "closed",
  closed: null,
};

function shortId(value: string | undefined, length = 12): string {
  if (!value) return "—";
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

function formatTime(value: string | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function stageStatus(index: number, currentIndex: number, stage: string): "pass" | "running" | "waiting" | "blocked" {
  if (index < currentIndex) return "pass";
  if (index === currentIndex) return stage === "closed" ? "pass" : "running";
  return "waiting";
}

function impactActionText(action: string | undefined): string {
  switch (action) {
    case "block_new_use":
      return "阻断新使用";
    case "revalidate":
      return "需要重验证";
    case "preserve_and_mark":
      return "保留并标记";
    default:
      return action ?? "";
  }
}

export function WorkbenchLifecycle({ packId, version }: { packId: string; version: string }) {
  const [invalidations, setInvalidations] = useState<PackInvalidationEvent[]>([]);
  const [rollbacks, setRollbacks] = useState<PackRollbackRecord[]>([]);
  const [revocations, setRevocations] = useState<RevocationEvent[]>([]);
  const [isAdmin, setIsAdmin] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState<PackInvalidationEvent | null>(null);
  const [impactSet, setImpactSet] = useState<PackImpactSet | null>(null);
  const [revalidation, setRevalidation] = useState<RevalidationReport | null>(null);
  const [expandedEvent, setExpandedEvent] = useState<string | null>(null);

  // 新建失效事件表单
  const [showNewForm, setShowNewForm] = useState(false);
  const [newTrigger, setNewTrigger] = useState<PackInvalidationTrigger>("source_retracted");
  const [newReason, setNewReason] = useState("");

  // 紧急撤销表单：决策 6.10 要求二次认证、输入包 ID/版本并确认影响范围。
  const [showRevokeForm, setShowRevokeForm] = useState(false);
  const [revokePackId, setRevokePackId] = useState(packId);
  const [revokeVersion, setRevokeVersion] = useState(version);
  const [revokeTrigger, setRevokeTrigger] = useState<PackInvalidationTrigger>("security_event");
  const [revokeReason, setRevokeReason] = useState("");
  const [revokeFactor, setRevokeFactor] = useState("");
  const [revokeImpactConfirmed, setRevokeImpactConfirmed] = useState(false);

  // 回滚表单
  const [showRollbackForm, setShowRollbackForm] = useState(false);
  const [rollbackReason, setRollbackReason] = useState("");
  const [rollbackOpinion, setRollbackOpinion] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setError(null);
      const [events, rolls, revs, admin] = await Promise.all([
        listInvalidations(packId),
        listRollbacks(),
        listRevocations(),
        fetchSecurityAdminStatus(),
      ]);
      setInvalidations(events);
      setRollbacks(rolls);
      setRevocations(revs);
      setIsAdmin(admin.is_security_admin);
      if (expandedEvent) {
        const event = events.find((item) => item.event_id === expandedEvent) ?? null;
        setSelectedEvent(event);
        if (event?.impact_set_id) {
          setImpactSet(await getImpactSet(event.event_id));
        } else {
          setImpactSet(null);
        }
        setRevalidation(await getRevalidationReport(expandedEvent));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [packId, expandedEvent]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const run = async (key: string, action: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await action();
      await refresh();
      setNotice("操作成功");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const openEvent = async (event: PackInvalidationEvent) => {
    setExpandedEvent(event.event_id);
    setSelectedEvent(event);
    try {
      setImpactSet(
        event.impact_set_id ? await getImpactSet(event.event_id) : null
      );
      setRevalidation(await getRevalidationReport(event.event_id));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleResolveImpact = async (eventId: string) => {
    const impact = await resolveImpact(eventId);
    setImpactSet(impact);
    await refresh();
    setNotice("影响集已生成");
  };

  const handleAdvance = async (event: PackInvalidationEvent) => {
    const next = NEXT_STAGE[event.stage];
    if (!next) return;
    await run(`advance:${next}`, () => advanceInvalidation(event.event_id, next));
  };

  const handleRevalidateArea = async (area: PackImpactCategory) => {
    if (!impactSet || !expandedEvent) return;
    const refs = (impactSet.items ?? [])
      .filter((item) => item.category === area)
      .map((item) => item.ref_id);
    if (!refs.length) return;
    await run(`revalidate:${area}`, () =>
      reportRevalidated(expandedEvent, area, refs).then((report) => setRevalidation(report))
    );
  };

  const impactItems = impactSet?.items ?? [];
  const groupedItems = CATEGORY_ORDER.map((category) => ({
    category,
    items: impactItems.filter((item) => item.category === category),
  })).filter((group) => group.items.length > 0);

  const areaProgress = (area: {
    total: number;
    revalidated?: string[];
    failed?: string[];
  }) => {
    const revalidatedCount = (area.revalidated?.length ?? 0) + (area.failed?.length ?? 0);
    const failedCount = area.failed?.length ?? 0;
    return { revalidatedCount, failedCount, done: revalidatedCount >= (area.total ?? 0) };
  };

  const revalidationAreas = revalidation?.areas ?? [];
  const currentRollbacks = rollbacks.filter(
    (rollback) => rollback.pack_id === packId
  );
  const currentRevocations = revocations.filter(
    (item) => item.pack_id === packId
  );

  return (
    <section className="sc-card" aria-labelledby="lifecycle-title" style={{ display: "flex", flexDirection: "column", gap: "var(--space-5)" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--space-4)", flexWrap: "wrap" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          <h2 id="lifecycle-title" className="sc-section-title" style={{ margin: 0 }}>
            失效、撤销与回滚
          </h2>
          <p style={{ margin: 0, color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
            来源撤回、依赖失效、签名或安全事件按检测→分诊→控制→定位影响→修复→重验证→关闭处置。
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
          {isAdmin ? (
            <StatusBadge status="pass" label="安全管理员" />
          ) : (
            <Button
              variant="secondary"
              size="sm"
              isLoading={busy === "admin"}
              onClick={() => run("admin", registerSecurityAdmin)}
            >
              登记为安全管理员
            </Button>
          )}
          <Button size="sm" onClick={() => setShowNewForm((value) => !value)}>
            新建失效事件
          </Button>
        </div>
      </header>

      {error && (
        <p role="alert" style={{ margin: 0, color: "var(--color-status-error)" }}>
          {error}
        </p>
      )}
      {notice && (
        <p role="status" aria-live="polite" style={{ margin: 0, color: "var(--color-status-success)" }}>
          {notice}
        </p>
      )}

      {/* 新建失效事件 */}
      {showNewForm && (
        <div
          style={{
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-md)",
            padding: "var(--space-4)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-3)",
          }}
        >
          <h3 className="sc-landmark-label" style={{ textTransform: "none", margin: 0 }}>
            登记失效事件（触发后进入检测阶段）
          </h3>
          <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
            <span className="sc-landmark-label" style={{ textTransform: "none" }}>触发类型</span>
            <select
              value={newTrigger}
              onChange={(event) => setNewTrigger(event.target.value as PackInvalidationTrigger)}
              style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)", minHeight: "var(--target-size)" }}
            >
              {Object.entries(TRIGGER_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
            <span className="sc-landmark-label" style={{ textTransform: "none" }}>原因</span>
            <textarea
              value={newReason}
              onChange={(event) => setNewReason(event.target.value)}
              rows={2}
              placeholder="说明检测到的失效证据，例如来源勘误、依赖撤销或评测回归"
              style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)" }}
            />
          </label>
          <Button
            size="sm"
            disabled={!newReason.trim()}
            isLoading={busy === "new-event"}
            onClick={() =>
              run("new-event", () =>
                recordInvalidation({
                  pack_id: packId,
                  version,
                  trigger: newTrigger,
                  reason: newReason.trim(),
                })
              ).then(() => {
                setShowNewForm(false);
                setNewReason("");
              })
            }
          >
            登记失效事件
          </Button>
        </div>
      )}

      {/* 失效事件列表 */}
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
        {invalidations.length === 0 && (
          <p style={{ margin: 0, color: "var(--color-text-tertiary)" }}>
            该包版本暂无失效事件。
          </p>
        )}
        {invalidations.map((event) => {
          const currentIndex = STAGE_INDEX[event.stage];
          const expanded = expandedEvent === event.event_id;
          const next = NEXT_STAGE[event.stage];
          return (
            <article
              key={event.event_id}
              style={{
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-md)",
                padding: "var(--space-4)",
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-4)",
                backgroundColor: event.emergency ? "var(--color-status-error-bg)" : undefined,
              }}
            >
              <header style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--space-3)", flexWrap: "wrap" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
                  <span
                    style={{
                      fontSize: "var(--text-xs)",
                      fontWeight: 600,
                      padding: "0.125rem 0.5rem",
                      borderRadius: "var(--radius-full)",
                      backgroundColor: "var(--color-bg-secondary)",
                      color: "var(--color-text-secondary)",
                      border: "1px solid var(--color-border-strong)",
                    }}
                  >
                    {TRIGGER_LABELS[event.trigger] ?? event.trigger}
                  </span>
                  {event.emergency && <StatusBadge status="blocked" label="紧急" />}
                  <StatusBadge status={event.stage === "closed" ? "pass" : "running"} label={`阶段 ${STAGES[currentIndex].label}`} />
                </div>
                <span style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
                  {formatTime(event.initiated_at)} · {shortId(event.initiated_by, 8)}
                </span>
              </header>

              <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
                {event.reason}
              </p>

              {/* 七阶段时间线 */}
              <nav aria-label="失效事件阶段">
                <ol
                  role="list"
                  style={{ display: "flex", listStyle: "none", margin: 0, padding: 0, overflowX: "auto", gap: "var(--space-1)" }}
                >
                  {STAGES.map((stage, index) => {
                    const status = stageStatus(index, currentIndex, event.stage);
                    return (
                      <li key={stage.id} style={{ flex: "0 0 auto", minWidth: "6rem" }}>
                        <div
                          aria-current={index === currentIndex ? "step" : undefined}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "var(--space-2)",
                            padding: "var(--space-2)",
                            borderRadius: "var(--radius-md)",
                            border: "1px solid var(--color-border)",
                            backgroundColor:
                              index === currentIndex ? "var(--color-bg-secondary)" : "var(--color-surface)",
                          }}
                        >
                          <span
                            aria-hidden="true"
                            style={{
                              width: "1.5rem",
                              height: "1.5rem",
                              borderRadius: "var(--radius-full)",
                              display: "inline-flex",
                              alignItems: "center",
                              justifyContent: "center",
                              fontSize: "var(--text-xs)",
                              fontWeight: 600,
                              color:
                                status === "pass"
                                  ? "var(--color-text-on-accent)"
                                  : "var(--color-text-secondary)",
                              backgroundColor:
                                status === "pass"
                                  ? "var(--color-status-success)"
                                  : "var(--color-bg-secondary)",
                              border: status === "running" ? "1px solid var(--color-accent-primary)" : "none",
                            }}
                          >
                            {status === "pass" ? <Icon name="check" size={14} aria-hidden /> : index + 1}
                          </span>
                          <span style={{ fontSize: "var(--text-xs)", whiteSpace: "nowrap" }}>{stage.label}</span>
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </nav>

              {/* 处置操作 */}
              <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", alignItems: "center" }}>
                {next && (
                  <Button
                    variant="secondary"
                    size="sm"
                    isLoading={busy === `advance:${next}`}
                    disabled={
                      (next === "impacted_objects_found" && !event.impact_set_id) ||
                      // 重验证报告只对展开事件加载；关闭门按各事件自身的报告裁决。
                      (next === "closed" &&
                        expanded &&
                        revalidation?.status !== "completed")
                    }
                    onClick={() => handleAdvance(event)}
                  >
                    推进到{STAGES[STAGE_INDEX[next]].label}
                  </Button>
                )}
                {!event.impact_set_id && STAGE_INDEX[event.stage] <= STAGE_INDEX.contained && (
                  <Button
                    size="sm"
                    isLoading={busy === `impact:${event.event_id}`}
                    onClick={() =>
                      run(`impact:${event.event_id}`, () => handleResolveImpact(event.event_id))
                    }
                  >
                    生成影响集
                  </Button>
                )}
                <Button variant="ghost" size="sm" onClick={() => (expanded ? setExpandedEvent(null) : openEvent(event))}>
                  {expanded ? "收起详情" : "查看详情"}
                </Button>
              </div>

              {next === "closed" && expanded && revalidation?.status !== "completed" && (
                <p role="status" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-wait)" }}>
                  重验证未完成前不能关闭失效事件。
                </p>
              )}

              {/* 影响带与重验证 */}
              {expanded && (
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
                  <div>
                    <h3 className="sc-landmark-label" style={{ textTransform: "none", margin: "0 0 var(--space-2)" }}>
                      影响带：触发源 → 包/规则 → 运行 → Claim/Evidence → Wording/产物 → 项目/用户动作
                    </h3>
                    {groupedItems.length === 0 ? (
                      <p style={{ margin: 0, color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
                        尚未生成影响集；使用“生成影响集”按钮定位下游影响。
                      </p>
                    ) : (
                      <ol
                        role="list"
                        style={{
                          display: "flex",
                          listStyle: "none",
                          margin: 0,
                          padding: 0,
                          overflowX: "auto",
                          gap: "var(--space-3)",
                        }}
                      >
                        {groupedItems.map((group) => (
                          <li key={group.category} style={{ flex: "0 0 auto", width: "15rem", maxWidth: "80vw" }}>
                            <div
                              style={{
                                border: "1px solid var(--color-border)",
                                borderRadius: "var(--radius-md)",
                                padding: "var(--space-3)",
                                display: "flex",
                                flexDirection: "column",
                                gap: "var(--space-2)",
                                height: "100%",
                              }}
                            >
                              <strong style={{ fontSize: "var(--text-sm)" }}>
                                {CATEGORY_LABELS[group.category] ?? group.category}
                                <span style={{ color: "var(--color-text-tertiary)", fontWeight: 400 }}>
                                  {" "}{group.items.length}
                                </span>
                              </strong>
                              <ul role="list" style={{ margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                                {group.items.map((item) => (
                                  <li
                                    key={item.item_id}
                                    style={{
                                      fontSize: "var(--text-xs)",
                                      color: "var(--color-text-secondary)",
                                      display: "flex",
                                      flexDirection: "column",
                                      gap: "0.125rem",
                                      borderTop: "1px solid var(--color-border)",
                                      paddingTop: "var(--space-1)",
                                    }}
                                  >
                                    <span style={{ wordBreak: "break-all" }}>{item.label}</span>
                                    <span style={{ color: "var(--color-text-tertiary)" }}>
                                      {impactActionText(item.action)}
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            </div>
                          </li>
                        ))}
                      </ol>
                    )}
                  </div>

                  <div>
                    <h3 className="sc-landmark-label" style={{ textTransform: "none", margin: "0 0 var(--space-2)" }}>
                      重验证推进
                    </h3>
                    {revalidationAreas.length === 0 ? (
                      <p style={{ margin: 0, color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
                        没有需要重验证的对象，或尚未进入重验证阶段。
                      </p>
                    ) : (
                      <ul role="list" style={{ margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                        {revalidationAreas.map((area) => (
                          <li
                            key={area.area}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "space-between",
                              gap: "var(--space-3)",
                              flexWrap: "wrap",
                              border: "1px solid var(--color-border)",
                              borderRadius: "var(--radius-md)",
                              padding: "var(--space-2) var(--space-3)",
                            }}
                          >
                            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                              <StatusBadge
                                status={areaProgress(area).done ? "pass" : areaProgress(area).failedCount > 0 ? "blocked" : "waiting"}
                                label={areaProgress(area).done ? "已推进" : areaProgress(area).failedCount > 0 ? "有失败" : "待推进"}
                              />
                              <strong style={{ fontSize: "var(--text-sm)" }}>
                                {CATEGORY_LABELS[area.area] ?? area.area}
                              </strong>
                              <span style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
                                {areaProgress(area).revalidatedCount}/{area.total}
                                {areaProgress(area).failedCount > 0 && `（失败 ${areaProgress(area).failedCount}）`}
                              </span>
                            </div>
                            <Button
                              variant="secondary"
                              size="sm"
                              disabled={areaProgress(area).done}
                              isLoading={busy === `revalidate:${area.area}`}
                              onClick={() => handleRevalidateArea(area.area)}
                            >
                              登记全部重验证
                            </Button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              )}
            </article>
          );
        })}
      </div>

      {/* 紧急撤销 */}
      {isAdmin && (
        <div
          style={{
            border: "1px solid var(--color-status-error)",
            borderRadius: "var(--radius-md)",
            padding: "var(--space-4)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-3)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
              <h3 className="sc-section-title" style={{ margin: 0 }}>
                紧急撤销
              </h3>
              <p style={{ margin: 0, color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
                安全管理员可紧急撤销并阻断新运行；不能编辑规则或直接发布替代版本，撤销后仍需双人复核与影响报告。
              </p>
            </div>
            <Button
              variant="danger"
              size="sm"
              onClick={() => setShowRevokeForm((value) => !value)}
            >
              发起紧急撤销
            </Button>
          </div>
          {showRevokeForm && (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
              <p role="status" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
                破坏性操作：将阻止该版本开始任何新运行。请输入目标包 ID 与版本（须与当前版本一致）、二次认证令牌，并确认影响范围。
              </p>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(10rem, 1fr))", gap: "var(--space-3)" }}>
                <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                  <span className="sc-landmark-label" style={{ textTransform: "none" }}>包 ID</span>
                  <input
                    value={revokePackId}
                    onChange={(event) => setRevokePackId(event.target.value)}
                    placeholder={packId}
                    style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)", minHeight: "var(--target-size)" }}
                  />
                </label>
                <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                  <span className="sc-landmark-label" style={{ textTransform: "none" }}>版本</span>
                  <input
                    value={revokeVersion}
                    onChange={(event) => setRevokeVersion(event.target.value)}
                    placeholder={version}
                    style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)", minHeight: "var(--target-size)" }}
                  />
                </label>
                <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                  <span className="sc-landmark-label" style={{ textTransform: "none" }}>二次认证令牌</span>
                  <input
                    type="password"
                    value={revokeFactor}
                    onChange={(event) => setRevokeFactor(event.target.value)}
                    autoComplete="one-time-code"
                    placeholder="例如 TOTP 验证码"
                    style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)", minHeight: "var(--target-size)" }}
                  />
                </label>
              </div>
              <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                <span className="sc-landmark-label" style={{ textTransform: "none" }}>触发类型</span>
                <select
                  value={revokeTrigger}
                  onChange={(event) => setRevokeTrigger(event.target.value as PackInvalidationTrigger)}
                  style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)", minHeight: "var(--target-size)" }}
                >
                  {Object.entries(TRIGGER_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </label>
              <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                <span className="sc-landmark-label" style={{ textTransform: "none" }}>撤销原因</span>
                <textarea
                  value={revokeReason}
                  onChange={(event) => setRevokeReason(event.target.value)}
                  rows={2}
                  placeholder="说明签名、供应链、安全或严重科学缺陷"
                  style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)" }}
                />
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", fontSize: "var(--text-sm)" }}>
                <input
                  type="checkbox"
                  checked={revokeImpactConfirmed}
                  onChange={(event) => setRevokeImpactConfirmed(event.target.checked)}
                />
                我确认该撤销将阻断本版本新运行，并影响其全部下游包、运行、Claim、Evidence、Wording、产物、项目与用户动作
              </label>
              <Button
                variant="danger"
                disabled={
                  !revokeReason.trim() ||
                  !revokeFactor.trim() ||
                  !revokeImpactConfirmed ||
                  revokePackId.trim() !== packId ||
                  revokeVersion.trim() !== version
                }
                isLoading={busy === "revoke"}
                onClick={() =>
                  run("revoke", () =>
                    emergencyRevoke({
                      pack_id: packId,
                      version,
                      trigger: revokeTrigger,
                      reason: revokeReason.trim(),
                      second_factor: revokeFactor.trim(),
                    })
                  ).then(() => {
                    setShowRevokeForm(false);
                    setRevokeReason("");
                    setRevokeFactor("");
                    setRevokeImpactConfirmed(false);
                  })
                }
              >
                确认紧急撤销并阻断新运行
              </Button>
            </div>
          )}
          {currentRevocations.length > 0 && (
            <ul role="list" style={{ margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
              {currentRevocations.map((item) => (
                <li
                  key={item.revocation_id}
                  style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--space-2)", flexWrap: "wrap", fontSize: "var(--text-sm)" }}
                >
                  <span style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                    <StatusBadge status="blocked" label="已撤销" />
                    <span>{item.pack_id}@{item.pack_version}</span>
                  </span>
                  <span style={{ color: "var(--color-text-tertiary)" }}>
                    {formatTime(item.occurred_at)} · {shortId(item.revoked_by, 8)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* 受信回滚 */}
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
            <h3 className="sc-section-title" style={{ margin: 0 }}>
              受信回滚
            </h3>
            <p style={{ margin: 0, color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
              只回滚到仍受信、依赖兼容且通过当前平台下限的旧版；已撤销版本不能通过回滚复活。
            </p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            disabled={currentRevocations.length === 0}
            onClick={() => setShowRollbackForm((value) => !value)}
          >
            提议回滚
          </Button>
        </div>
        {showRollbackForm && (
          <div
            style={{
              border: "1px solid var(--color-border)",
              borderRadius: "var(--radius-md)",
              padding: "var(--space-4)",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-3)",
            }}
          >
            <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
              <span className="sc-landmark-label" style={{ textTransform: "none" }}>回滚原因</span>
              <textarea
                value={rollbackReason}
                onChange={(event) => setRollbackReason(event.target.value)}
                rows={2}
                placeholder="说明为什么受信旧版可以恢复使用"
                style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)" }}
              />
            </label>
            <Button
              size="sm"
              disabled={!rollbackReason.trim()}
              isLoading={busy === "rollback-propose"}
              onClick={() =>
                run("rollback-propose", () =>
                  proposeRollback({
                    pack_id: packId,
                    from_version: version,
                    reason: rollbackReason.trim(),
                  })
                ).then(() => {
                  setShowRollbackForm(false);
                  setRollbackReason("");
                })
              }
            >
              提议回滚到受信旧版
            </Button>
          </div>
        )}
        {currentRollbacks.length === 0 ? (
          <p style={{ margin: 0, color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
            该包尚无回滚记录。
          </p>
        ) : (
          <ul role="list" style={{ margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
            {currentRollbacks.map((rollback) => {
              const reviewerConfirmed = rollback.confirmations?.find(
                (item) => item.role === "independent"
              );
              const releaserConfirmed = rollback.confirmations?.find(
                (item) => item.role === "platform"
              );
              const approved = rollback.status === "approved";
              const executed = rollback.status === "executed";
              return (
                <li
                  key={rollback.rollback_id}
                  style={{
                    border: "1px solid var(--color-border)",
                    borderRadius: "var(--radius-md)",
                    padding: "var(--space-4)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "var(--space-3)",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--space-3)", flexWrap: "wrap" }}>
                    <strong style={{ fontSize: "var(--text-sm)" }}>
                      回滚 {rollback.from_version} → {rollback.to_version}
                    </strong>
                    <StatusBadge
                      status={executed ? "pass" : rollback.status === "rejected" ? "blocked" : rollback.status === "approved" ? "qualified" : "waiting"}
                      label={rollback.status}
                    />
                  </div>
                  <dl style={{ margin: 0, fontSize: "var(--text-sm)", display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-3)" }}>
                      <dt className="sc-landmark-label" style={{ textTransform: "none" }}>目标摘要</dt>
                      <dd style={{ margin: 0, fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)" }}>
                        {shortId(rollback.to_digest, 20)}
                      </dd>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-3)" }}>
                      <dt className="sc-landmark-label" style={{ textTransform: "none" }}>夹具重放</dt>
                      <dd style={{ margin: 0 }}>
                        <StatusBadge status={rollback.fixture_passed ? "pass" : "blocked"} label={rollback.fixture_passed ? "已通过" : "未通过"} />
                      </dd>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-3)" }}>
                      <dt className="sc-landmark-label" style={{ textTransform: "none" }}>独立复核者确认</dt>
                      <dd style={{ margin: 0 }}>
                        <StatusBadge
                          status={reviewerConfirmed?.conclusion === "approve" ? "pass" : reviewerConfirmed ? "blocked" : "waiting"}
                          label={reviewerConfirmed ? reviewerConfirmed.conclusion : "未确认"}
                        />
                      </dd>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-3)" }}>
                      <dt className="sc-landmark-label" style={{ textTransform: "none" }}>平台发行者确认</dt>
                      <dd style={{ margin: 0 }}>
                        <StatusBadge
                          status={releaserConfirmed?.conclusion === "approve" ? "pass" : releaserConfirmed ? "blocked" : "waiting"}
                          label={releaserConfirmed ? releaserConfirmed.conclusion : "未确认"}
                        />
                      </dd>
                    </div>
                  </dl>
                  <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
                    {!reviewerConfirmed && rollback.status === "proposed" && (
                      <>
                        <Button
                          variant="secondary"
                          size="sm"
                          isLoading={busy === `confirm-ind:${rollback.rollback_id}`}
                          onClick={() =>
                            run(`confirm-ind:${rollback.rollback_id}`, () =>
                              confirmRollback(rollback.rollback_id, "independent", "approve", rollbackOpinion)
                            )
                          }
                        >
                          独立复核者确认
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          disabled={!rollbackOpinion.trim()}
                          isLoading={busy === `reject-ind:${rollback.rollback_id}`}
                          onClick={() =>
                            run(`reject-ind:${rollback.rollback_id}`, () =>
                              confirmRollback(rollback.rollback_id, "independent", "reject", rollbackOpinion)
                            )
                          }
                        >
                          复核者拒绝
                        </Button>
                      </>
                    )}
                    {!releaserConfirmed && rollback.status === "proposed" && (
                      <Button
                        variant="secondary"
                        size="sm"
                        isLoading={busy === `confirm-plat:${rollback.rollback_id}`}
                        onClick={() =>
                          run(`confirm-plat:${rollback.rollback_id}`, () =>
                            confirmRollback(rollback.rollback_id, "platform", "approve", rollbackOpinion)
                          )
                        }
                      >
                        平台发行者确认
                      </Button>
                    )}
                    {approved && (
                      <Button
                        size="sm"
                        isLoading={busy === `execute:${rollback.rollback_id}`}
                        onClick={() => run(`execute:${rollback.rollback_id}`, () => executeRollback(rollback.rollback_id))}
                      >
                        执行回滚
                      </Button>
                    )}
                    {!executed && rollback.status !== "rejected" && (
                      <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)", flex: "1 1 12rem", minWidth: "10rem" }}>
                        <span className="sc-landmark-label" style={{ textTransform: "none" }}>意见</span>
                        <input
                          value={rollbackOpinion}
                          onChange={(event) => setRollbackOpinion(event.target.value)}
                          placeholder="确认或拒绝意见"
                          style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)", minHeight: "var(--target-size)" }}
                        />
                      </label>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
