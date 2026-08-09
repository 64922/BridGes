"use client";

import { useEffect, useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";

interface CareerPlanningDialogProps {
  open: boolean;
  onClose: () => void;
  /** 已存在的真实对话（对话页提供）；新聊天页不提供，用 ensureConversation 预建。 */
  conversationId?: string;
  /** 新聊天页的延迟建会话钩子（提交前预建空对话）。 */
  ensureConversation?: () => Promise<string | undefined>;
  /** 提交：宿主执行真实发送（真实消息流，不伪造结果）；返回是否成功。 */
  onSubmit: (content: string) => Promise<boolean>;
}

/**
 * 生涯规划任务对话框（Issue 29）。
 *
 * 收集生涯问题（自然语言）与本轮画像使用开关；提交走真实消息流程
 * （消息落库、最小画像切片编译与披露、六类输出结果卡），不在此处伪造
 * 任何规划结果。画像开关与输入区 Composer 开关同语义：关闭后本轮模型
 * 请求、审计与披露均不含任何画像内容。
 */
export function CareerPlanningDialog({
  open,
  onClose,
  conversationId,
  ensureConversation,
  onSubmit,
}: CareerPlanningDialogProps) {
  const [question, setQuestion] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

  // 关闭时清空表单（下次打开保持干净起点，任务输入不跨任务残留）
  useEffect(() => {
    if (!open) return;
    setFormError("");
    setSubmitting(false);
  }, [open]);

  const submit = async () => {
    if (submitting) return;
    const content = `生涯规划助手：${question.trim()}`;
    if (!question.trim()) {
      setFormError("请先描述你想规划的学业或职业方向。");
      return;
    }
    setSubmitting(true);
    setFormError("");
    try {
      const accepted = await onSubmit(content);
      if (accepted === false) {
        setSubmitting(false);
        return;
      }
      // 成功后由宿主关闭对话框并进入消息流
      onClose();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "发送失败，请重试。");
      setSubmitting(false);
    }
  };

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

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="生涯规划助手"
      description="基于你授权的画像切片、学习记录与可追溯证据，输出已知事实、待验证假设、可选方向、关键风险、分阶段成长路径与近期学习建议；不作就业、薪酬或录取保证。"
    >
      <div style={{ display: "grid", gap: "var(--space-3)" }}>
        <div>
          <label htmlFor="career-question" style={labelStyle}>
            你想规划的学业或职业方向
            <span style={{ color: "var(--color-status-error)" }}>*</span>
          </label>
          <textarea
            id="career-question"
            data-testid="career-question-input"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="例如：我大二在读计算机科学，目前喜欢数据分析，想知道接下来的选课、实习和考研怎么安排…"
            rows={5}
            style={{ ...fieldStyle, resize: "vertical" }}
          />
          <p style={{ margin: "var(--space-1) 0 0", fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
            可以说明你的阶段、目标、已有基础与约束；规划结果只基于你授权的信息。
          </p>
        </div>

        {formError && (
          <p role="alert" data-testid="career-form-error" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
            {formError}
          </p>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)", marginTop: "var(--space-2)" }}>
          <Button variant="secondary" onClick={onClose} data-testid="career-cancel">
            取消
          </Button>
          <Button
            variant="primary"
            onClick={() => void submit()}
            disabled={!question.trim() || submitting}
            data-testid="career-submit"
          >
            <Icon name="career" size={16} aria-hidden />
            {submitting ? "正在发送…" : "开始规划"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
