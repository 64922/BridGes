import type { CSSProperties } from "react";

/** 输入区图标按钮的共同命中区与视觉基线（附件/提醒/工具栏共用）。 */
export const iconButtonStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: "var(--target-size)",
  minHeight: "var(--target-size)",
  border: "none",
  borderRadius: "var(--radius-md)",
  backgroundColor: "transparent",
  color: "var(--color-text-secondary)",
  cursor: "pointer",
};
