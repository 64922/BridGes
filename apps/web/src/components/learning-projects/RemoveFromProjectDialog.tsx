"use client";

import { Dialog } from "@/components/bridges/Dialog";
import { Button } from "@/components/design-system/Button";

interface RemoveFromProjectDialogProps {
  conversationTitle: string;
  busy: boolean;
  error: string;
  onConfirm: () => void;
  onClose: () => void;
}

/**
 * 把对话移出学习项目的轻量确认对话框（Issue 19）。
 *
 * 只改变对话归属：历史消息与附件不会被修改，对话保留为独立对话。
 */
export function RemoveFromProjectDialog({
  conversationTitle,
  busy,
  error,
  onConfirm,
  onClose,
}: RemoveFromProjectDialogProps) {
  return (
    <Dialog
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title="移出学习项目？"
      description={`「${conversationTitle}」将从学习项目中移出，保留为独立对话；历史消息与附件不会被修改。`}
    >
      {error && (
        <p role="alert" style={{ fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
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
        <Button variant="primary" size="sm" isLoading={busy} onClick={onConfirm}>
          确认移出
        </Button>
      </div>
    </Dialog>
  );
}
