"use client";

import { useEffect, useState } from "react";

import { StatusBadge } from "@/components/design-system/StatusBadge";
import { fetchHealthSummary, statusText, type HealthProjection } from "@/lib/api";

export default function HealthPanel() {
  const [projection, setProjection] = useState<HealthProjection | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    fetchHealthSummary({ signal: controller.signal })
      .then(setProjection)
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
      });

    return () => controller.abort();
  }, []);

  if (error) {
    return (
      <section aria-label="系统健康状态">
        <p role="alert">无法获取健康状态：{error}</p>
      </section>
    );
  }

  if (!projection) {
    return (
      <section aria-label="系统健康状态">
        <p>正在读取健康状态…</p>
      </section>
    );
  }

  return (
    <section aria-label="系统健康状态">
      <h2>系统健康</h2>
      <p>版本：{projection.version}</p>
      <dl style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", marginTop: "var(--space-3)" }}>
        <dt style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>存活</dt>
        <dd><StatusBadge status={projection.live} label={statusText(projection.live)} /></dd>
        <dt style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)", marginTop: "var(--space-1)" }}>就绪</dt>
        <dd><StatusBadge status={projection.ready} label={statusText(projection.ready)} /></dd>
        <dt style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)", marginTop: "var(--space-1)" }}>降级</dt>
        <dd><StatusBadge status={projection.degraded} label={statusText(projection.degraded)} /></dd>
      </dl>
      {projection.dependencies && projection.dependencies.length > 0 && (
        <>
          <h2>依赖状态</h2>
          <ul>
            {projection.dependencies.map((dep) => (
              <li key={dep.name}>
                {dep.name}：{statusText(dep.status)}
                {dep.message ? ` — ${dep.message}` : ""}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
