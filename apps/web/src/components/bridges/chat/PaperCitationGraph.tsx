"use client";

import { useId, useState } from "react";
import type { PaperCitationEdge } from "@/lib/api";

/** 仅渲染实际取得的引用边；文字列表提供键盘与屏幕阅读器等价路径。 */
export function PaperCitationGraph({ edges }: { edges: PaperCitationEdge[] }) {
  const [expanded, setExpanded] = useState(false);
  const marker = useId().replace(/:/g, "");
  if (!edges.length) return null;
  const cited = new Set(edges.map((edge) => edge.cited_candidate_id));
  const titles = new Map<string, string>();
  edges.forEach((edge) => {
    titles.set(edge.citing_candidate_id, edge.citing_title);
    titles.set(edge.cited_candidate_id, edge.cited_title);
  });
  const positions = new Map<string, { x: number; y: number }>();
  let left = 0;
  let right = 0;
  titles.forEach((_, id) => {
    positions.set(id, { x: cited.has(id) ? 570 : 180,
      y: 44 + 70 * (cited.has(id) ? right++ : left++) });
  });
  const height = 44 + 70 * Math.max(left, right);
  return (
    <details data-testid="paper-search-citations"
      onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary style={{ minHeight: "var(--target-size)", cursor: "pointer" }}>
        引用关系（{edges.length}）
      </summary>
      <p style={{ fontSize: "var(--text-xs)" }}>
        箭头方向为“引用方 → 被引用方”；仅展示本轮取得的真实 OpenAlex 引用记录。
      </p>
      {expanded && (
        <div style={{ maxHeight: 440, overflowY: "auto" }}>
        <svg role="img" aria-label="论文引用关系图" viewBox={`0 0 750 ${height}`}
          style={{ width: "100%", height: "auto", color: "var(--color-text-primary)" }}>
          <defs><marker id={marker} viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
          </marker></defs>
          {edges.map((edge, index) => {
            const from = positions.get(edge.citing_candidate_id)!;
            const to = positions.get(edge.cited_candidate_id)!;
            const forward = from.x < to.x;
            const x1 = from.x + (forward ? 150 : -150);
            const x2 = to.x + (forward ? -156 : 156);
            return <path key={index}
              d={`M ${x1} ${from.y} C ${x1 + 70} ${from.y}, ${x2 - 70} ${to.y}, ${x2} ${to.y}`}
              fill="none" stroke="currentColor" markerEnd={`url(#${marker})`} />;
          })}
          {Array.from(titles).map(([id, title]) => {
            const position = positions.get(id)!;
            return <g key={id} transform={`translate(${position.x},${position.y})`}>
              <title>{title}</title>
              <rect x="-150" y="-22" width="300" height="44" rx="8"
                fill="var(--color-surface)" stroke="currentColor" />
              <text textAnchor="middle" dominantBaseline="middle" fill="currentColor" fontSize="16">
                {title.length > 30 ? `${title.slice(0, 29)}…` : title}
              </text>
            </g>;
          })}
        </svg>
        </div>
      )}
      <ul style={{ paddingLeft: "var(--space-5)" }}>
        {edges.map((edge, index) => <li key={index}>
          {edge.citing_title} → {edge.cited_title}
        </li>)}
      </ul>
    </details>
  );
}
