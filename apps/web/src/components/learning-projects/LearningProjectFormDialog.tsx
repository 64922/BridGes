"use client";

import { useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { FormField } from "@/components/bridges/FormField";
import { Button } from "@/components/design-system/Button";

interface LearningProjectFormDialogProps {
  mode: "create" | "rename";
  initialName?: string;
  initialDescription?: string;
  busy: boolean;
  /** 服务端错误（如 invalid_name），展示在字段下方。 */
  error: string;
  onSubmit: (values: { name: string; description: string }) => void;
  onClose: () => void;
}

/**
 * 新建/改名学习项目共用的表单对话框（Issue 19）。
 *
 * 名称必填（字段旁内联中文错误），说明可选；Enter 提交、Esc 取消，
 * 焦点陷阱与焦点归还由 Dialog 保证。调用方在打开时条件挂载本组件，
 * 保证每次打开的初始值与服务端投影一致。
 */
export function LearningProjectFormDialog({
  mode,
  initialName = "",
  initialDescription = "",
  busy,
  error,
  onSubmit,
  onClose,
}: LearningProjectFormDialogProps) {
  const [name, setName] = useState(initialName);
  const [description, setDescription] = useState(initialDescription);
  const [nameError, setNameError] = useState("");

  const title = mode === "create" ? "新建学习项目" : "修改学习项目";
  const submitLabel = mode === "create" ? "创建" : "保存";

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    const trimmed = name.trim();
    if (!trimmed) {
      setNameError("请输入项目名称。");
      return;
    }
    onSubmit({ name: trimmed, description: description.trim() });
  };

  return (
    <Dialog
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title={title}
      description="学习项目是把相关对话与项目文件组织在一起的文件夹，仅当前账户可见。"
    >
      <form
        onSubmit={handleSubmit}
        style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}
      >
        <FormField
          id="learning-project-name"
          label="名称"
          value={name}
          onChange={(value) => {
            setName(value);
            if (nameError) setNameError("");
          }}
          error={nameError}
          required
          placeholder="例如：线性代数期末复习"
        />
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          <label htmlFor="learning-project-description">说明（可选）</label>
          <textarea
            id="learning-project-description"
            name="learning-project-description"
            rows={3}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="这个项目要解决什么学习目标？"
            style={{
              width: "100%",
              padding: "0.625rem var(--space-3)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border-strong)",
              fontSize: "var(--text-base)",
              backgroundColor: "var(--color-surface)",
              color: "var(--color-text-primary)",
              resize: "vertical",
            }}
          />
        </div>
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
            marginTop: "var(--space-2)",
          }}
        >
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button type="submit" variant="primary" isLoading={busy}>
            {submitLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
