"use client";

import { useEffect, useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { Button } from "@/components/design-system/Button";

import styles from "./ProfileCenter.module.css";

interface ReasonConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  /** 危险操作（撤回 / 删除）使用危险色按钮。 */
  danger?: boolean;
  reasonPlaceholder?: string;
  reasonRequired?: boolean;
  onSubmit: (reason: string) => Promise<void>;
  onClose: () => void;
}

/**
 * 需要原因与确认的操作对话框：撤回、冻结、解冻、删除记录。
 *
 * 危险操作要求二次确认并填写原因；提交失败时错误留在对话框内，不关闭、
 * 不显示假成功。
 */
export function ReasonConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  danger = false,
  reasonPlaceholder = "请填写操作原因（用于审计记录）",
  reasonRequired = true,
  onSubmit,
  onClose,
}: ReasonConfirmDialogProps) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setReason("");
    setError(null);
    setSubmitting(false);
  }, [open]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = reason.trim();
    if (reasonRequired && !trimmed) {
      setError("请填写操作原因，以便审计记录。");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(trimmed || "用户未说明原因");
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "操作失败，请稍后重试。");
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title={title} description={description}>
      <form className={styles.dialogStack} onSubmit={submit} noValidate>
        <div>
          <label htmlFor="confirm-reason">
            操作原因
            {reasonRequired && (
              <span aria-hidden="true" style={{ color: "var(--color-status-error)" }}>
                {" "}
                *
              </span>
            )}
          </label>
          <input
            id="confirm-reason"
            type="text"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder={reasonPlaceholder}
            required={reasonRequired}
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
          <Button
            variant={danger ? "danger" : "primary"}
            size="md"
            type="submit"
            isLoading={submitting}
          >
            {confirmLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
