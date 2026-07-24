"use client";

import { useEffect, useState } from "react";

import { fetchHealthSummary, statusText, type HealthProjection } from "@/lib/api";

export default function HealthPanel() {
  const [projection, setProjection] = useState<HealthProjection | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchHealthSummary()
      .then(setProjection)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
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
      <h1>Science Companion</h1>
      <p>版本：{projection.version}</p>
      <dl>
        <dt>存活</dt>
        <dd>{statusText(projection.live)}</dd>
        <dt>就绪</dt>
        <dd>{statusText(projection.ready)}</dd>
        <dt>降级</dt>
        <dd>{statusText(projection.degraded)}</dd>
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
