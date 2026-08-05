"use client";

import { useEffect, useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";

interface VideoDialogProps {
  open: boolean;
  onClose: () => void;
  /** 提交：宿主执行真实发送（真实消息流）；返回是否成功。 */
  onSubmit: (payload: { prompt: string }) => Promise<boolean>;
}

const fieldStyle: React.CSSProperties = {
  width: "100%",
  padding: "var(--space-2) var(--space-3)",
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-md)",
  background: "var(--color-surface)",
  color: "var(--color-text)",
  fontSize: "var(--text-sm)",
  fontFamily: "inherit",
  boxSizing: "border-box",
};

const labelStyle: React.CSSProperties = {
  display: "block",
  marginBottom: "var(--space-1)",
  fontSize: "var(--text-sm)",
  color: "var(--color-text-secondary)",
};

/**
 * 文生视频任务对话框（Issue 32）。
 *
 * 只收集提示词：所有请求固定绑定 wan2.7-t2v-2026-06-12 与当前账户百炼
 * 密钥（ADR-0007：Wan 是模型矩阵唯一非 Qwen 系列例外），尺寸固定
 * 1280×720，界面不提供模型选择。提交走真实消息流程（video 载荷创建
 * 异步任务），任务状态卡与资产卡在消息流中呈现，不在此处伪造任何
 * 视频结果。请求只发送提示词，不发送其他内容。
 */
export function VideoDialog({ open, onClose, onSubmit }: VideoDialogProps) {
  const [prompt, setPrompt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

  // 打开时重置表单（任务输入不跨任务残留）。
  useEffect(() => {
    if (!open) return;
    setFormError("");
    setSubmitting(false);
  }, [open]);

  const submit = async () => {
    if (submitting) return;
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt) {
      setFormError("请输入要生成的视频画面描述。");
      return;
    }
    setSubmitting(true);
    setFormError("");
    try {
      const accepted = await onSubmit({ prompt: cleanPrompt });
      if (accepted === false) {
        setSubmitting(false);
        return;
      }
      onClose();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "发送失败，请重试。");
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="视频生成"
      description="固定使用 wan2.7-t2v-2026-06-12 模型：输入画面描述生成短视频；结果在对话中呈现，可预览、下载、修改可访问文字说明与删除。视频生成需要数分钟，请耐心等待。"
    >
      <div style={{ display: "grid", gap: "var(--space-3)" }}>
        <div>
          <label htmlFor="video-prompt" style={labelStyle}>
            视频画面描述
            <span style={{ color: "var(--color-status-error)" }}>*</span>
          </label>
          <textarea
            id="video-prompt"
            data-testid="video-prompt-input"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="例如：一条静谧的河在晨雾中缓缓流淌，两岸是初春的树林，镜头缓慢推进…"
            rows={4}
            style={{ ...fieldStyle, resize: "vertical" }}
          />
          <p style={{ margin: "var(--space-1) 0 0", fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
            生成尺寸固定为 1280×720，模型固定为 wan2.7-t2v-2026-06-12（固定模型矩阵，用户不可选）。
            请求只发送这段提示词，不发送其他内容。
          </p>
        </div>

        {formError && (
          <p role="alert" data-testid="video-form-error" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
            {formError}
          </p>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)", marginTop: "var(--space-2)" }}>
          <Button variant="secondary" onClick={onClose} data-testid="video-cancel">
            取消
          </Button>
          <Button
            variant="primary"
            onClick={() => void submit()}
            disabled={!prompt.trim() || submitting}
            data-testid="video-submit"
          >
            <Icon name="videoClapper" size={16} aria-hidden />
            {submitting ? "正在发送…" : "开始生成"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
