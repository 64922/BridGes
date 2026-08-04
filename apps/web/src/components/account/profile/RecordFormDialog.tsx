"use client";

import { useEffect, useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { Button } from "@/components/design-system/Button";
import type {
  ManualAssertionCreateRequest,
  ProfileAssertion,
  ProfileDimension,
  ProfileSensitivityClass,
} from "@/lib/api";

import styles from "./ProfileCenter.module.css";

const sensitivityOptions: { value: ProfileSensitivityClass; label: string }[] = [
  { value: "public", label: "公开" },
  { value: "preference", label: "偏好" },
  { value: "learning", label: "学习" },
  { value: "sensitive", label: "敏感" },
];

interface RecordFormDialogProps {
  open: boolean;
  title: string;
  description: string;
  /** 当前记录的编辑模式；为空表示新增。 */
  record: ProfileAssertion | null;
  /** 新增记录时的画像类别。 */
  dimension: ProfileDimension;
  onSubmit: (
    payload: ManualAssertionCreateRequest,
    reason: string
  ) => Promise<void>;
  onClose: () => void;
}

/**
 * 新增 / 编辑画像记录对话框。
 *
 * 用户声明内容、适用场景、敏感级别、授权范围与来源说明；写入失败时错误
 * 显示在对话框内，不关闭、不显示假成功。
 */
export function RecordFormDialog({
  open,
  title,
  description,
  record,
  dimension,
  onSubmit,
  onClose,
}: RecordFormDialogProps) {
  const [value, setValue] = useState(record?.value_or_rule || "");
  const [scenesText, setScenesText] = useState(
    record ? (record.applicable_scenes || []).join("，") : ""
  );
  const [sensitivity, setSensitivity] = useState<ProfileSensitivityClass>(
    record?.sensitivity_class || "preference"
  );
  const [authorization, setAuthorization] = useState(record?.authorization_scope || "general");
  const [sourceNote, setSourceNote] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setValue(record?.value_or_rule || "");
    setScenesText(record ? (record.applicable_scenes || []).join("，") : "");
    setSensitivity(record?.sensitivity_class || "preference");
    setAuthorization(record?.authorization_scope || "general");
    setSourceNote("");
    setReason("");
    setError(null);
    setSubmitting(false);
  }, [open, record]);

  const scenes = scenesText
    .split(/[，,]/)
    .map((scene) => scene.trim())
    .filter(Boolean);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) {
      setError("请填写记录内容。");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(
        {
          dimension,
          value_or_rule: trimmed,
          applicable_scenes: scenes,
          sensitivity_class: sensitivity,
          authorization_scope: authorization.trim() || "general",
          source_note: sourceNote.trim() || "用户手动记录",
        },
        reason.trim() || "用户更新画像记录"
      );
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败，请稍后重试。");
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title={title} description={description}>
      <form className={styles.dialogStack} onSubmit={submit} noValidate>
        <div>
          <label htmlFor="record-value">
            {record ? "记录内容" : "画像记录内容"}
            <span aria-hidden="true" style={{ color: "var(--color-status-error)" }}>
              {" "}
              *
            </span>
          </label>
          <textarea
            id="record-value"
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
          <label htmlFor="record-scenes">适用场景（用逗号分隔，可为空表示通用）</label>
          <input
            id="record-scenes"
            type="text"
            value={scenesText}
            onChange={(event) => setScenesText(event.target.value)}
            placeholder="例如：快速问答，学习模式"
            style={{
              width: "100%",
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border-strong)",
              font: "inherit",
            }}
          />
        </div>

        <div>
          <label htmlFor="record-sensitivity">敏感级别</label>
          <select
            id="record-sensitivity"
            value={sensitivity}
            onChange={(event) =>
              setSensitivity(event.target.value as ProfileSensitivityClass)
            }
            style={{
              width: "100%",
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border-strong)",
              font: "inherit",
            }}
          >
            {sensitivityOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="record-authorization">授权范围（由你主动设置，系统不会推断）</label>
          <input
            id="record-authorization"
            type="text"
            value={authorization}
            onChange={(event) => setAuthorization(event.target.value)}
            style={{
              width: "100%",
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border-strong)",
              font: "inherit",
            }}
          />
        </div>

        {!record && (
          <div>
            <label htmlFor="record-source">来源说明（记录从何而来，用于证据追溯）</label>
            <input
              id="record-source"
              type="text"
              value={sourceNote}
              onChange={(event) => setSourceNote(event.target.value)}
              placeholder="例如：2026 年 8 月与伙伴的对话中明确表达"
              style={{
                width: "100%",
                padding: "var(--space-2) var(--space-3)",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border-strong)",
                font: "inherit",
              }}
            />
          </div>
        )}

        {record && (
          <div>
            <label htmlFor="record-reason">变更原因</label>
            <input
              id="record-reason"
              type="text"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="例如：目标周期调整"
              style={{
                width: "100%",
                padding: "var(--space-2) var(--space-3)",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border-strong)",
                font: "inherit",
              }}
            />
          </div>
        )}

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
            保存
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
