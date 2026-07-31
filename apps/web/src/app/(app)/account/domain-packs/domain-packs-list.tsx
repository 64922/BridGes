"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { StatusBadge } from "@/components/design-system/StatusBadge";
import {
  listWorkbenchPacks,
  registerWorkbenchPack,
  type WorkbenchPackRecord,
} from "@/lib/api";

const BUILTIN_PACKS: { id: string; version: string; label: string }[] = [
  { id: "mathematics.formal-proof", version: "1.0.0", label: "数学与形式证明" },
  { id: "physics-chemistry.experiment", version: "1.0.0", label: "物理与化学实验测量" },
  { id: "life-science.general-research", version: "1.0.0", label: "生命科学一般研究" },
  { id: "medical.high-risk-education", version: "1.0.0", label: "医学高风险教育" },
  { id: "earth-climate.observation", version: "1.0.0", label: "地球与气候观测" },
  { id: "astronomy.observation-model", version: "1.0.0", label: "天文学观测与模型" },
  { id: "computer-science.software", version: "1.0.0", label: "计算机科学与软件文档" },
  { id: "standards.datasets", version: "1.0.0", label: "跨学科标准与数据集" },
];

const STAGE_LABELS: Record<string, string> = {
  drafting: "编写与预检",
  content_signed: "内容签名",
  review_assigned: "分配独立复核",
  review_completed: "复核完成",
  changes_requested: "变更请求",
  release_preflight: "发行预检",
  gray_release_ready: "灰度就绪",
  released: "已发行",
};

function stageBadge(stage: string): "pass" | "waiting" | "running" | "blocked" | "unknown" {
  switch (stage) {
    case "released":
      return "pass";
    case "drafting":
      return "unknown";
    case "changes_requested":
      return "blocked";
    case "gray_release_ready":
      return "running";
    default:
      return "waiting";
  }
}

export function DomainPacksList() {
  const [records, setRecords] = useState<WorkbenchPackRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const refresh = async () => {
    try {
      setError(null);
      setRecords(await listWorkbenchPacks());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRegister = async (packId: string, version: string) => {
    setBusyId(`${packId}@${version}`);
    try {
      setError(null);
      await registerWorkbenchPack(packId, version);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  if (records === null && !error) {
    return (
      <div className="sc-card" role="status">
        <Icon name="info" size={18} aria-hidden /> 正在加载工作台项目…
      </div>
    );
  }

  const registered = records ?? [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
      {error && (
        <p role="alert" style={{ color: "var(--color-status-error)" }}>
          {error}
        </p>
      )}
      {records === null && error && (
        <div className="sc-card">
          <Button variant="secondary" onClick={refresh}>
            重试
          </Button>
        </div>
      )}

      {registered.length > 0 && (
        <section aria-labelledby="registered-title">
          <h2 id="registered-title" className="sc-landmark-label" style={{ marginBottom: "var(--space-3)" }}>
            已登记版本
          </h2>
          <ul role="list" style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
            {registered.map((record) => (
              <li key={`${record.pack_id}@${record.pack_version}`}>
                <a
                  href={`/account/domain-packs/${encodeURIComponent(record.pack_id)}/${encodeURIComponent(record.pack_version)}`}
                  className="sc-card"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: "var(--space-4)",
                    textDecoration: "none",
                    color: "inherit",
                    flexWrap: "wrap",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", minWidth: 0 }}>
                    <Icon name="project" size={22} aria-hidden />
                    <div>
                      <strong style={{ display: "block" }}>{record.pack_id}</strong>
                      <span style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
                        v{record.pack_version} · 维护者 {record.maintainer_id.slice(0, 8)}
                      </span>
                    </div>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
                    <StatusBadge status={stageBadge(record.stage)} label={STAGE_LABELS[record.stage] ?? record.stage} />
                    <StatusBadge
                      status={record.lifecycle_status === "active" ? "pass" : record.lifecycle_status === "revoked" ? "fail" : "waiting"}
                      label={`治理 ${record.lifecycle_status}`}
                    />
                    <span style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
                      签名 {(record.attestations ?? []).length}/3
                    </span>
                    <Icon name="chevronRight" size={18} aria-hidden />
                  </div>
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="candidate-title">
        <h2 id="candidate-title" className="sc-landmark-label" style={{ marginBottom: "var(--space-3)" }}>
          待登记候选
        </h2>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(17rem, 1fr))",
            gap: "var(--space-4)",
          }}
        >
          {BUILTIN_PACKS.map((pack) => {
            const isRegistered = registered.some(
              (record) => record.pack_id === pack.id && record.pack_version === pack.version
            );
            const isBusy = busyId === `${pack.id}@${pack.version}`;
            return (
              <div className="sc-card" key={pack.id} style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
                <div>
                  <strong style={{ display: "block" }}>{pack.label}</strong>
                  <span style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
                    {pack.id} · v{pack.version}
                  </span>
                </div>
                <Button
                  variant={isRegistered ? "secondary" : "primary"}
                  size="sm"
                  disabled={isRegistered || isBusy}
                  isLoading={isBusy}
                  onClick={() => handleRegister(pack.id, pack.version)}
                >
                  {isRegistered ? "已在工作台" : "登记到工作台"}
                </Button>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
