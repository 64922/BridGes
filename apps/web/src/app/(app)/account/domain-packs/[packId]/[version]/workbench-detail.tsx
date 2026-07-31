"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { StatusBadge } from "@/components/design-system/StatusBadge";
import { WorkbenchLifecycle } from "./workbench-lifecycle";
import {
  addConflictDisclosure,
  assignReleaser,
  assignReviewer,
  declareConflictOfInterest,
  fetchSession,
  getSemanticDiff,
  getWorkbenchRecord,
  prepareGrayRelease,
  releasePack,
  submitContentSignature,
  submitIndependentSignature,
  type ConflictDisclosure,
  type ReviewAttestation,
  type SemanticDiff,
  type WorkbenchPackRecord,
} from "@/lib/api";

const STAGES: { id: string; label: string }[] = [
  { id: "drafting", label: "编写与预检" },
  { id: "content_signed", label: "内容签名" },
  { id: "review_assigned", label: "分配独立复核" },
  { id: "review_completed", label: "复核完成" },
  { id: "changes_requested", label: "变更请求" },
  { id: "release_preflight", label: "发行预检" },
  { id: "gray_release_ready", label: "灰度就绪" },
  { id: "released", label: "已发行" },
];

const CATEGORY_LABELS: Record<string, string> = {
  rule: "规则",
  wording: "措辞",
  human_gate: "人工门",
  source: "来源",
  fixture: "夹具",
  dependency: "依赖",
  validator: "校验器",
};

const ROLE_LABELS: Record<string, string> = {
  content: "内容签名",
  independent: "独立验证",
  platform: "发行签名",
};

const CHANGE_LABELS: Record<string, string> = {
  added: "新增",
  removed: "删除",
  modified: "修改",
};

function stageIndex(stage: string): number {
  const index = STAGES.findIndex((item) => item.id === stage);
  return index === -1 ? 0 : index;
}

function signatureStatus(attestation: ReviewAttestation | undefined): "pass" | "waiting" | "blocked" {
  if (!attestation) return "waiting";
  return attestation.conclusion === "approve" ? "pass" : "blocked";
}

function formatTime(value: string | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function shortId(value: string | undefined, length = 12): string {
  if (!value) return "—";
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

export function WorkbenchDetail({ packId, version }: { packId: string; version: string }) {
  const [record, setRecord] = useState<WorkbenchPackRecord | null>(null);
  const [diff, setDiff] = useState<SemanticDiff | null>(null);
  const [accountId, setAccountId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [opinion, setOpinion] = useState("");
  const [disclosures, setDisclosures] = useState("");
  const [reviewerId, setReviewerId] = useState("");
  const [releaserId, setReleaserId] = useState("");
  const [minorityItem, setMinorityItem] = useState("");
  const [minorityOpinion, setMinorityOpinion] = useState("");

  const refresh = useCallback(async () => {
    try {
      setError(null);
      const session = await fetchSession();
      setAccountId(session.subject.account_id);
      setRecord(await getWorkbenchRecord(packId, version));
      setDiff(await getSemanticDiff(packId, version));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [packId, version]);

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

  if (!record && !error) {
    return (
      <div className="sc-card" role="status">
        <Icon name="info" size={18} aria-hidden /> 正在加载工作台记录…
      </div>
    );
  }

  if (!record) {
    return (
      <div className="sc-card" role="alert">
        <p style={{ color: "var(--color-status-error)" }}>{error}</p>
        <Button variant="secondary" onClick={refresh}>重试</Button>
      </div>
    );
  }

  const currentIndex = stageIndex(record.stage);
  const attestations = record.attestations ?? [];
  const contentSig = attestations.find((item) => item.role === "content");
  const independentSig = attestations.find((item) => item.role === "independent");
  const platformSig = attestations.find((item) => item.role === "platform");
  const isMaintainer = accountId === record.maintainer_id;
  const isReviewer = accountId === record.reviewer_id;
  const isReleaser = accountId === record.releaser_id;
  const grayReady = record.gray_candidate?.status === "ready_to_release";

  const stageStatus = (index: number): "pass" | "running" | "waiting" | "blocked" => {
    if (index < currentIndex) return "pass";
    if (index === currentIndex) return record.stage === "changes_requested" ? "blocked" : "running";
    return "waiting";
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
      {error && (
        <p role="alert" style={{ color: "var(--color-status-error)" }}>
          {error}
        </p>
      )}
      {notice && (
        <p role="status" style={{ color: "var(--color-status-success)" }}>
          {notice}
        </p>
      )}

      {/* 全局包头 */}
      <header
        className="sc-card"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: "var(--space-4)",
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
          <p className="sc-landmark-label">领域包专家工作台</p>
          <h1 style={{ margin: 0 }}>
            {packId}
            <span style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-lg)" }}>
              {" "}
              v{version}
            </span>
          </h1>
          <dl
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(9rem, 1fr))",
              gap: "var(--space-3)",
              margin: 0,
              fontSize: "var(--text-sm)",
            }}
          >
            <div>
              <dt className="sc-landmark-label">规范化摘要</dt>
              <dd style={{ margin: 0, fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)" }}>
                {shortId(record.canonical_digest, 20)}
              </dd>
            </div>
            <div>
              <dt className="sc-landmark-label">维护者</dt>
              <dd style={{ margin: 0 }}>{shortId(record.maintainer_id, 12)}</dd>
            </div>
            <div>
              <dt className="sc-landmark-label">复核者</dt>
              <dd style={{ margin: 0 }}>{record.reviewer_id ? shortId(record.reviewer_id, 12) : "未分配"}</dd>
            </div>
            <div>
              <dt className="sc-landmark-label">发行者</dt>
              <dd style={{ margin: 0 }}>{record.releaser_id ? shortId(record.releaser_id, 12) : "未分配"}</dd>
            </div>
          </dl>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "var(--space-2)" }}>
          <StatusBadge status={stageStatus(currentIndex)} label={STAGES[currentIndex].label} />
          <StatusBadge
            status={record.lifecycle_status === "active" ? "pass" : record.lifecycle_status === "revoked" ? "fail" : "waiting"}
            label={`治理状态 ${record.lifecycle_status}`}
          />
          <span style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
            夹具 {Number(record.gray_candidate?.fixture_summary?.total ?? (record.fixture_results ?? []).length)} 项 ·{" "}
            {record.gray_candidate ? `人工门 ${Number(record.gray_candidate.fixture_summary?.human_gate_count ?? 0)} 项` : "未灰度"}
          </span>
        </div>
      </header>

      {/* 阶段轨道 */}
      <nav aria-label="发行流程阶段">
        <ol
          role="list"
          style={{
            display: "flex",
            listStyle: "none",
            margin: 0,
            padding: 0,
            overflowX: "auto",
            gap: "var(--space-1)",
          }}
        >
          {STAGES.map((stage, index) => {
            const status = stageStatus(index);
            return (
              <li key={stage.id} style={{ flex: "0 0 auto", minWidth: "7.5rem" }}>
                <div
                  aria-current={index === currentIndex ? "step" : undefined}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-2)",
                    padding: "var(--space-2) var(--space-3)",
                    borderRadius: "var(--radius-md)",
                    border: "1px solid var(--color-border)",
                    backgroundColor:
                      index === currentIndex
                        ? "var(--color-bg-secondary)"
                        : "var(--color-surface)",
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
                          : status === "blocked"
                            ? "var(--color-status-error)"
                            : "var(--color-text-secondary)",
                      backgroundColor:
                        status === "pass"
                          ? "var(--color-status-success)"
                          : status === "blocked"
                            ? "var(--color-status-error-bg)"
                            : "var(--color-bg-secondary)",
                      border:
                        status === "running" ? "1px solid var(--color-accent-primary)" : "none",
                    }}
                  >
                    {status === "pass" ? (
                      <Icon name="check" size={14} aria-hidden />
                    ) : (
                      index + 1
                    )}
                  </span>
                  <span style={{ fontSize: "var(--text-sm)", fontWeight: index === currentIndex ? 600 : 400 }}>
                    {stage.label}
                  </span>
                </div>
              </li>
            );
          })}
        </ol>
      </nav>

      {/* 三栏主区 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1.1fr) minmax(0, 1.6fr) minmax(0, 1.2fr)",
          gap: "var(--space-5)",
          alignItems: "start",
        }}
        className="workbench-grid"
      >
        {/* 左栏：责任泳道与待办 */}
        <section className="sc-card" aria-labelledby="lanes-title" style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <h2 id="lanes-title" className="sc-section-title" style={{ marginBottom: 0 }}>
            责任泳道
          </h2>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
            {[
              { role: "维护者", person: record.maintainer_id, active: isMaintainer, done: Boolean(contentSig) },
              { role: "独立复核者", person: record.reviewer_id, active: isReviewer, done: Boolean(independentSig) },
              { role: "平台发行者", person: record.releaser_id, active: isReleaser, done: Boolean(platformSig) },
            ].map((lane) => (
              <div
                key={lane.role}
                style={{
                  border: "1px solid var(--color-border)",
                  borderRadius: "var(--radius-md)",
                  padding: "var(--space-3)",
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-1)",
                  backgroundColor: lane.active ? "var(--color-bg-secondary)" : undefined,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--space-2)" }}>
                  <strong style={{ fontSize: "var(--text-sm)" }}>{lane.role}</strong>
                  <StatusBadge
                    status={lane.done ? "pass" : lane.person ? "waiting" : "blocked"}
                    label={lane.done ? "已签名" : lane.person ? "等待操作" : "未分配"}
                  />
                </div>
                <span style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
                  {lane.person ? shortId(lane.person, 12) : "尚未分配"}
                </span>
                {lane.active && lane.role === "独立复核者" && record.stage === "review_assigned" && (
                  <span style={{ color: "var(--color-status-wait)", fontSize: "var(--text-sm)" }}>
                    等待你重跑夹具并提交独立验证
                  </span>
                )}
                {lane.active && lane.role === "平台发行者" && record.stage === "gray_release_ready" && (
                  <span style={{ color: "var(--color-status-wait)", fontSize: "var(--text-sm)" }}>
                    等待你确认签名与门后追加发行签名
                  </span>
                )}
              </div>
            ))}
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
            <h3 className="sc-landmark-label">签名状态</h3>
            <ul role="list" style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
              {[
                { label: ROLE_LABELS.content, attestation: contentSig },
                { label: ROLE_LABELS.independent, attestation: independentSig },
                { label: ROLE_LABELS.platform, attestation: platformSig },
              ].map(({ label, attestation }) => (
                <li key={label} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--space-2)", fontSize: "var(--text-sm)" }}>
                  <span>{label}</span>
                  {attestation ? (
                    <span style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", color: "var(--color-text-secondary)" }}>
                      {shortId(attestation.person_id, 8)}
                      <StatusBadge status={signatureStatus(attestation)} label={attestation.conclusion} />
                    </span>
                  ) : (
                    <StatusBadge status="waiting" label="未签名" />
                  )}
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* 中栏：语义 Diff 画布 */}
        <section className="sc-card" aria-labelledby="diff-title" style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <header>
            <h2 id="diff-title" className="sc-section-title" style={{ marginBottom: "var(--space-1)" }}>
              语义 Diff
            </h2>
            <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)", margin: 0 }}>
              {diff ? `${diff.from_version} → ${diff.to_version} · ${(diff.entries ?? []).length} 项判定差异` : "加载中"}
            </p>
          </header>
          {diff && (diff.entries ?? []).length > 0 ? (
            <ul role="list" style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
              {(diff.entries ?? []).map((entry, index) => (
                <li
                  key={`${entry.category}-${entry.item_id}-${index}`}
                  style={{
                    border: "1px solid var(--color-border)",
                    borderRadius: "var(--radius-md)",
                    padding: "var(--space-3)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "var(--space-1)",
                  }}
                >
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
                      {CATEGORY_LABELS[entry.category] ?? entry.category}
                    </span>
                    <span
                      style={{
                        fontSize: "var(--text-xs)",
                        fontWeight: 600,
                        padding: "0.125rem 0.5rem",
                        borderRadius: "var(--radius-full)",
                        color:
                          entry.change === "added"
                            ? "var(--color-status-success)"
                            : entry.change === "removed"
                              ? "var(--color-status-error)"
                              : "var(--color-status-wait)",
                        backgroundColor:
                          entry.change === "added"
                            ? "var(--color-status-success-bg)"
                            : entry.change === "removed"
                              ? "var(--color-status-error-bg)"
                              : "var(--color-status-wait-bg)",
                      }}
                    >
                      {CHANGE_LABELS[entry.change] ?? entry.change}
                    </span>
                    {entry.significance === "high" && (
                      <span
                        style={{
                          fontSize: "var(--text-xs)",
                          fontWeight: 600,
                          padding: "0.125rem 0.5rem",
                          borderRadius: "var(--radius-full)",
                          color: "var(--color-status-error)",
                          backgroundColor: "var(--color-status-error-bg)",
                        }}
                      >
                        高影响
                      </span>
                    )}
                    <strong style={{ fontSize: "var(--text-sm)", flex: 1, minWidth: 0 }}>
                      {entry.label}
                    </strong>
                  </div>
                  <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
                    {entry.impact}
                  </p>
                  {(entry.change === "modified" || entry.change === "removed") && (
                    <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-status-error)", wordBreak: "break-all" }}>
                      旧：{typeof entry.old_value === "string" ? entry.old_value : JSON.stringify(entry.old_value)}
                    </p>
                  )}
                  {(entry.change === "modified" || entry.change === "added") && (
                    <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-status-success)", wordBreak: "break-all" }}>
                      新：{typeof entry.new_value === "string" ? entry.new_value : JSON.stringify(entry.new_value)}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p style={{ color: "var(--color-text-tertiary)" }}>与上一版本相比没有判定差异。</p>
          )}
        </section>

        {/* 右栏：操作与检查器 */}
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-5)" }}>
          <section className="sc-card" aria-labelledby="actions-title" style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
            <h2 id="actions-title" className="sc-section-title" style={{ marginBottom: 0 }}>
              操作
            </h2>

            <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
              <span className="sc-landmark-label" style={{ textTransform: "none" }}>利益冲突声明</span>
              <textarea
                value={disclosures}
                onChange={(event) => setDisclosures(event.target.value)}
                placeholder="例如：无相关利益，或说明与来源/资助方的关系"
                rows={2}
                style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)" }}
              />
              <Button
                variant="secondary"
                size="sm"
                isLoading={busy === "coi"}
                disabled={!disclosures.trim()}
                onClick={() =>
                  run("coi", () =>
                    declareConflictOfInterest(
                      packId,
                      version,
                      disclosures.split("\n").map((line) => line.trim()).filter(Boolean)
                    )
                  )
                }
              >
                声明本版本利益冲突
              </Button>
            </label>

            {(isMaintainer || isReviewer || isReleaser) && (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                <span className="sc-landmark-label" style={{ textTransform: "none" }}>
                  少数意见与未决依据
                </span>
                <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                  <span style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>对象引用（如规则或夹具 ID）</span>
                  <input
                    value={minorityItem}
                    onChange={(event) => setMinorityItem(event.target.value)}
                    placeholder="例如：rule.units"
                    style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)" }}
                  />
                </label>
                <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                  <span style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>少数意见</span>
                  <textarea
                    value={minorityOpinion}
                    onChange={(event) => setMinorityOpinion(event.target.value)}
                    rows={2}
                    style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)" }}
                  />
                </label>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!minorityItem.trim() || !minorityOpinion.trim()}
                  isLoading={busy === "disclosure"}
                  onClick={() =>
                    run("disclosure", () =>
                      addConflictDisclosure(packId, version, {
                        item_ref: minorityItem.trim(),
                        minority_opinion: minorityOpinion.trim(),
                      })
                    ).then(() => {
                      setMinorityItem("");
                      setMinorityOpinion("");
                    })
                  }
                >
                  登记少数意见
                </Button>
              </div>
            )}

            {isMaintainer && record.stage === "drafting" && !contentSig && (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                  <span className="sc-landmark-label" style={{ textTransform: "none" }}>签名意见</span>
                  <textarea
                    value={opinion}
                    onChange={(event) => setOpinion(event.target.value)}
                    rows={2}
                    style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)" }}
                  />
                </label>
                <Button
                  isLoading={busy === "content"}
                  onClick={() => run("content", () => submitContentSignature(packId, version, opinion))}
                >
                  提交内容签名
                </Button>
              </div>
            )}

            {isMaintainer && record.stage === "content_signed" && !record.reviewer_id && (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                  <span className="sc-landmark-label" style={{ textTransform: "none" }}>独立复核者账户 ID</span>
                  <input
                    value={reviewerId}
                    onChange={(event) => setReviewerId(event.target.value)}
                    style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)" }}
                  />
                </label>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!reviewerId.trim()}
                  isLoading={busy === "reviewer"}
                  onClick={() =>
                    run("reviewer", () => assignReviewer(packId, version, reviewerId.trim())).then(() => setReviewerId(""))
                  }
                >
                  分配独立复核者
                </Button>
              </div>
            )}

            {isMaintainer && record.stage === "content_signed" && !record.releaser_id && (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                  <span className="sc-landmark-label" style={{ textTransform: "none" }}>平台发行者账户 ID</span>
                  <input
                    value={releaserId}
                    onChange={(event) => setReleaserId(event.target.value)}
                    style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)" }}
                  />
                </label>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!releaserId.trim()}
                  isLoading={busy === "releaser"}
                  onClick={() =>
                    run("releaser", () => assignReleaser(packId, version, releaserId.trim())).then(() => setReleaserId(""))
                  }
                >
                  分配平台发行者
                </Button>
              </div>
            )}

            {isReviewer && record.stage === "review_assigned" && !independentSig && (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                  <span className="sc-landmark-label" style={{ textTransform: "none" }}>复核意见</span>
                  <textarea
                    value={opinion}
                    onChange={(event) => setOpinion(event.target.value)}
                    rows={2}
                    style={{ font: "inherit", padding: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-strong)" }}
                  />
                </label>
                <div style={{ display: "flex", gap: "var(--space-2)" }}>
                  <Button
                    isLoading={busy === "independent"}
                    onClick={() => run("independent", () => submitIndependentSignature(packId, version, opinion))}
                  >
                    提交独立验证
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={!opinion.trim()}
                    isLoading={busy === "independent-change"}
                    onClick={() =>
                      run("independent-change", () =>
                        submitIndependentSignature(packId, version, opinion, "needs_changes")
                      )
                    }
                  >
                    提交变更请求
                  </Button>
                </div>
                <span style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
                  变更请求把流程退回编写阶段，发行者不能覆盖。
                </span>
              </div>
            )}

            {record.stage !== "released" && (isMaintainer || isReviewer || isReleaser) && (
              <Button
                variant="secondary"
                isLoading={busy === "gray"}
                onClick={() => run("gray", () => prepareGrayRelease(packId, version))}
              >
                生成灰度候选
              </Button>
            )}

            {isReleaser && grayReady && (
              <Button
                isLoading={busy === "release"}
                onClick={() => run("release", () => releasePack(packId, version))}
              >
                追加发行签名并激活
              </Button>
            )}
          </section>

          <section className="sc-card" aria-labelledby="gray-title" style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
            <h2 id="gray-title" className="sc-section-title" style={{ marginBottom: 0 }}>
              灰度候选
            </h2>
            {record.gray_candidate ? (
              <>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <StatusBadge
                    status={grayReady ? "pass" : "blocked"}
                    label={record.gray_candidate.status === "ready_to_release" ? "可发行候选" : record.gray_candidate.status}
                  />
                  <span style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
                    {formatTime(record.gray_candidate.created_at)}
                  </span>
                </div>
                <dl style={{ margin: 0, fontSize: "var(--text-sm)", display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <dt className="sc-landmark-label" style={{ textTransform: "none" }}>夹具总数</dt>
                    <dd style={{ margin: 0 }}>{Number(record.gray_candidate.fixture_summary?.total ?? 0)}</dd>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <dt className="sc-landmark-label" style={{ textTransform: "none" }}>人工门夹具</dt>
                    <dd style={{ margin: 0 }}>{Number(record.gray_candidate.fixture_summary?.human_gate_count ?? 0)}</dd>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <dt className="sc-landmark-label" style={{ textTransform: "none" }}>创建者</dt>
                    <dd style={{ margin: 0 }}>{shortId(record.gray_candidate.created_by, 10)}</dd>
                  </div>
                </dl>
                <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
                  {grayReady
                    ? "候选已就绪，但不会自动激活；必须由平台发行者追加发行签名。"
                    : "候选被阻塞；修正阻塞项后重新生成。"}
                </p>
              </>
            ) : (
              <p style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
                尚未生成灰度候选。
              </p>
            )}
          </section>
        </div>
      </div>

      {/* T047：失效、撤销、回滚与重验证 */}
      <WorkbenchLifecycle packId={packId} version={version} />

      <style>{`
        @media (max-width: 1100px) {
          .workbench-grid {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>
    </div>
  );
}
