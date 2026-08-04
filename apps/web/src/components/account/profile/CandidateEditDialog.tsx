"use client";

import { useEffect, useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { Button } from "@/components/design-system/Button";
import type { ProfileCandidate } from "@/lib/api";

import styles from "./ProfileCenter.module.css";

interface CandidateEditDialogProps {
  open: boolean;
  candidate: ProfileCandidate | null;
  onSubmit: (value: string, scenes: string[]) => Promise<void>;
  onClose: () => void;
}

/**
 * 候选"编辑后确认"对话框（Issue 26）。
 *
 * 修改候选的值与适用范围后以 MODIFY 决策提交，修改内容保留在版本历史中；
 * 写入失败时错误显示在对话框内，不关闭、不显示假成功。
 */
export function CandidateEditDialog({
  open,
  candidate,
  onSubmit,
  onClose,
}: CandidateEditDialogProps) {
  const [value, setValue] = useState(candidate?.value_or_rule || "");
  const [scenesText, setScenesText] = useState(
    candidate ? (candidate.applicable_scenes || []).join("，") : ""
  );
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setValue(candidate?.value_or_rule || "");
    setScenesText(candidate ? (candidate.applicable_scenes || []).join("，") : "");
    setError(null);
    setSubmitting(false);
  }, [open, candidate]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) {
      setError("请填写候选内容。");
      return;
    }
    const scenes = scenesText
      .split(/[，,]/)
      .map((scene) => scene.trim())
      .filter(Boolean);
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(trimmed, scenes);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败，请稍后重试。");
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="编辑后确认候选画像"
      description="修改内容与适用范围后确认；修改前的原值保留在候选记录中。"
    >
      <form className={styles.dialogStack} onSubmit={submit} noValidate>
        <div>
          <label htmlFor="candidate-value">
            候选内容
            <span aria-hidden="true" style={{ color: "var(--color-status-error)" }}>
              {" "}
              *
            </span>
          </label>
          <textarea
            id="candidate-value"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            rows={3}
            required
            style={{
              width: "100%",
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border-strong)",
              font: "inherit",
              lineHeight: "var(--line-height-relaxed)",
              resize: "vertical",
            }}
          />
        </div>
        <div>
          <label htmlFor="candidate-scenes">适用范围（用逗号分隔，可为空表示通用）</label>
          <input
            id="candidate-scenes"
            type="text"
            value={scenesText}
            onChange={(event) => setScenesText(event.target.value)}
            placeholder="例如：学习模式，日常陪伴"
            style={{
              width: "100%",
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border-strong)",
              font: "inherit",
            }}
          />
        </div>
        {error && (
          <p role="alert" className={styles.errorText}>
            {error}
          </p>
        )}
        <div className={styles.dialogActions}>
          <Button variant="ghost" size="md" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button variant="primary" size="md" type="submit" isLoading={submitting}>
            确认
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
