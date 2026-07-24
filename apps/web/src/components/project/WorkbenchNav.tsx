"use client";

import { NavLink } from "@/components/design-system/NavLink";

interface WorkbenchNavProps {
  projectId: string;
}

const workbenches = [
  { href: "", label: "项目总览" },
  { href: "/learning", label: "学习实验室" },
  { href: "/expression", label: "科学表达工坊" },
  { href: "/multimedia", label: "多模态科学实验室" },
  { href: "/evidence", label: "证据与校验台" },
];

/**
 * Workbench navigation for a project.
 *
 * Each tab is a real route link with `aria-current="page"` so the active
 * workbench is announced by screen readers and visible through redundant
 * styling.
 */
export function WorkbenchNav({ projectId }: WorkbenchNavProps) {
  const prefix = `/projects/${projectId}`;

  return (
    <nav aria-label="项目工作台" style={{ marginBottom: "var(--space-6)" }}>
      <ul
        role="list"
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "var(--space-2)",
          borderBottom: "1px solid var(--color-border)",
          paddingBottom: "var(--space-2)",
        }}
      >
        {workbenches.map((bench) => {
          const href = `${prefix}${bench.href}`;
          return (
            <li key={bench.label}>
              <NavLink
                href={href}
                exact={bench.href === ""}
                className="workbench-tab"
              >
                {bench.label}
              </NavLink>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
