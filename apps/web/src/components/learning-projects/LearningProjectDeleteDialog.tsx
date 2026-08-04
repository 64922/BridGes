"use client";

import { useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { Button } from "@/components/design-system/Button";

export type ProjectDeleteContents = "keep" | "delete";

interface LearningProjectDeleteDialogProps {
  projectName: string;
  busy: boolean;
  /** 服务端错误（如 409 generation_in_progress 的中文消息），原样展示。 */
  error: string;
  onConfirm: (contents: ProjectDeleteContents) => void;
  onClose: () => void;
}

const OPTIONS: { value: ProjectDeleteContents; label: string; hint: string }[] = [
  {
    value: "keep",
    label: "保留对话",
    hint: "对话保留为独立对话，对话中的原始文件（附件）也一并保留；仅项目文件随项目删除。",
  },
  {
    value: "delete",
    label: "一并删除",
    hint: "对话及其附件、项目文件全部删除，且无法恢复。",
  },
];

/**
 * 删除学习项目的破坏性确认对话框（Issue 19）。
 *
 * 必须显式选择对话与文件的处理方式（保留 / 一并删除），确认按钮文案
 * 随选择变化；409（仍有回答正在生成）等服务端错误以中文原样展示。
 */
export function LearningProjectDeleteDialog({
  projectName,
  busy,
  error,
  onConfirm,
  onClose,
}: LearningProjectDeleteDialogProps) {
  const [contents, setContents] = useState<ProjectDeleteContents>("keep");

  return (
    <Dialog
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title="删除学习项目？"
      description={`将删除「${projectName}」，此操作无法撤销。`}
    >
      <fieldset
        style={{
          border: "none",
          padding: 0,
          margin: 0,
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-2)",
        }}
      >
        <legend
          style={{
            padding: 0,
            marginBottom: "var(--space-1)",
            fontSize: "var(--text-sm)",
            fontWeight: 600,
            color: "var(--color-text-primary)",
          }}
        >
          选择对话与文件的处理方式
        </legend>
        {OPTIONS.map((option) => (
          <label
            key={option.value}
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: "var(--space-2)",
              padding: "var(--space-3)",
              borderRadius: "var(--radius-md)",
              border: `1px solid ${contents === option.value ? "var(--color-accent-primary)" : "var(--color-border)"}`,
              backgroundColor:
                contents === option.value ? "var(--color-accent-primary-soft)" : "transparent",
              cursor: "pointer",
              minHeight: "var(--target-size)",
              transition:
                "background-color var(--motion-duration-fast) var(--motion-easing), " +
                "border-color var(--motion-duration-fast) var(--motion-easing)",
            }}
          >
            <input
              type="radio"
              name="learning-project-delete-contents"
              value={option.value}
              checked={contents === option.value}
              onChange={() => setContents(option.value)}
              style={{ marginTop: "0.25rem" }}
            />
            <span>
              <span style={{ display: "block", fontWeight: 600, color: "var(--color-text-primary)" }}>
                {option.label}
              </span>
              <span
                style={{
                  display: "block",
                  marginTop: "2px",
                  fontSize: "var(--text-sm)",
                  color: "var(--color-text-secondary)",
                }}
              >
                {option.hint}
              </span>
            </span>
          </label>
        ))}
      </fieldset>
      {error && (
        <p
          role="alert"
          style={{
            marginTop: "var(--space-3)",
            padding: "var(--space-3)",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--color-status-error)",
            backgroundColor: "var(--color-status-error-bg)",
            color: "var(--color-status-error)",
            fontSize: "var(--text-sm)",
          }}
        >
          {error}
        </p>
      )}
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: "var(--space-2)",
          marginTop: "var(--space-4)",
        }}
      >
        <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
          取消
        </Button>
        <Button
          variant="danger"
          size="sm"
          isLoading={busy}
          onClick={() => onConfirm(contents)}
        >
          {contents === "keep" ? "删除项目，保留对话" : "全部删除"}
        </Button>
      </div>
    </Dialog>
  );
}
