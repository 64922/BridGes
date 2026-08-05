"use client";

import { useEffect, useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import type { ImageAssetProjection, ImageTaskKind } from "@/lib/api";

interface ImageSourceOption {
  key: string;
  /** 资产版本来源：assetId + versionId */
  assetId?: string;
  versionId?: string;
  /** 聊天附件对象来源 */
  objectId?: string;
  label: string;
  hint: string;
}

interface ImageDialogProps {
  open: boolean;
  onClose: () => void;
  /** 当前对话中可选的来源图片（本账户图片资产版本，含替代文本摘要）。 */
  assets: ImageAssetProjection[];
  /** 当前对话中可选的图片附件（本账户聊天附件对象）。 */
  attachmentOptions: ImageSourceOption[];
  /** 提交：宿主执行真实发送（真实消息流）；返回是否成功。 */
  onSubmit: (payload: {
    kind: ImageTaskKind;
    prompt: string;
    sourceVersionId?: string;
    sourceObjectId?: string;
  }) => Promise<boolean>;
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
 * 图片生成/编辑任务对话框（Issue 31）。
 *
 * 生成页签：只收集提示词（尺寸固定 1024×1024，用户不可选——固定矩阵）。
 * 编辑页签：选择当前账户有权访问的来源图片（本对话图片资产版本或图片
 * 附件）+ 编辑指令。提交走真实消息流程（image 载荷创建异步任务），任务
 * 状态卡与资产卡在消息流中呈现，不在此处伪造任何图片结果。
 */
export function ImageDialog({
  open,
  onClose,
  assets,
  attachmentOptions,
  onSubmit,
}: ImageDialogProps) {
  const [tab, setTab] = useState<ImageTaskKind>("generate");
  const [prompt, setPrompt] = useState("");
  const [selectedKey, setSelectedKey] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

  // 打开时重置表单（任务输入不跨任务残留）；来源选项变化时校正选中项。
  useEffect(() => {
    if (!open) return;
    setFormError("");
    setSubmitting(false);
  }, [open]);

  const options: ImageSourceOption[] = [
    ...assets.flatMap((asset) =>
      (asset.versions ?? []).map((version) => ({
        key: `asset:${asset.asset_id}:${version.version_id}`,
        assetId: asset.asset_id,
        versionId: version.version_id,
        label: `图片资产 · 版本 ${version.created_at.slice(0, 16).replace("T", " ")}`,
        hint: version.prompt.slice(0, 40) || "（无提示摘要）",
      })),
    ),
    ...attachmentOptions,
  ];

  useEffect(() => {
    if (
      tab === "edit" &&
      selectedKey &&
      !options.some((option) => option.key === selectedKey)
    ) {
      setSelectedKey("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅来源列表变化时校正
  }, [options, tab]);

  const submit = async () => {
    if (submitting) return;
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt) {
      setFormError(tab === "edit" ? "请输入编辑指令。" : "请输入要生成的画面描述。");
      return;
    }
    if (tab === "edit" && !selectedKey) {
      setFormError("请选择要编辑的来源图片。");
      return;
    }
    setSubmitting(true);
    setFormError("");
    const selected = options.find((option) => option.key === selectedKey);
    try {
      const accepted = await onSubmit({
        kind: tab,
        prompt: cleanPrompt,
        sourceVersionId: selected?.assetId ? selected.versionId : undefined,
        sourceObjectId: selected?.objectId,
      });
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
      title="图片生成与编辑"
      description="固定使用 qwen-image-2.0-pro-2026-06-22 模型：输入画面描述生成图片，或选择当前账户有权访问的图片执行编辑；结果在对话中呈现，可查看版本、修改替代文本、下载与删除。"
    >
      <div style={{ display: "grid", gap: "var(--space-3)" }}>
        <div role="tablist" aria-label="任务类型" style={{ display: "flex", gap: "var(--space-2)" }}>
          {(
            [
              { value: "generate", label: "生成图片" },
              { value: "edit", label: "编辑图片" },
            ] as const
          ).map((item) => (
            <button
              key={item.value}
              type="button"
              role="tab"
              aria-selected={tab === item.value}
              data-testid={`image-tab-${item.value}`}
              onClick={() => setTab(item.value)}
              style={{
                padding: "var(--space-1) var(--space-3)",
                border: `1px solid ${
                  tab === item.value ? "var(--color-accent-primary)" : "var(--color-border)"
                }`,
                borderRadius: "var(--radius-md)",
                background: tab === item.value ? "var(--color-accent-primary-soft)" : "transparent",
                color: "var(--color-text-primary)",
                cursor: "pointer",
                fontSize: "var(--text-sm)",
              }}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div>
          <label htmlFor="image-prompt" style={labelStyle}>
            {tab === "edit" ? "编辑指令" : "画面描述"}
            <span style={{ color: "var(--color-status-error)" }}>*</span>
          </label>
          <textarea
            id="image-prompt"
            data-testid="image-prompt-input"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder={
              tab === "edit"
                ? "例如：把背景改为夜空，主体保持不动…"
                : "例如：一张分子结构的科学示意图，深蓝背景，标注关键官能团…"
            }
            rows={4}
            style={{ ...fieldStyle, resize: "vertical" }}
          />
          <p style={{ margin: "var(--space-1) 0 0", fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
            {tab === "edit"
              ? "编辑只发送你选择的这一张图片与编辑指令，不发送其他内容。"
              : `生成尺寸固定为 1024×1024（${"固定模型矩阵，用户不可选"}）。`}
          </p>
        </div>

        {tab === "edit" && (
          <div>
            <span id="image-source-label" style={labelStyle}>
              来源图片<span style={{ color: "var(--color-status-error)" }}>*</span>
            </span>
            {options.length === 0 ? (
              <p
                data-testid="image-source-empty"
                style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}
              >
                当前对话还没有可编辑的图片。请先切换到「生成图片」创建一张，或上传图片附件。
              </p>
            ) : (
              <ul
                role="listbox"
                aria-labelledby="image-source-label"
                aria-label="来源图片"
                data-testid="image-source-list"
                style={{
                  display: "grid",
                  gap: "var(--space-2)",
                  margin: 0,
                  padding: 0,
                  listStyle: "none",
                  maxHeight: "12rem",
                  overflowY: "auto",
                }}
              >
                {options.map((option) => (
                  <li key={option.key}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={selectedKey === option.key}
                      data-testid="image-source-option"
                      onClick={() => setSelectedKey(option.key)}
                      style={{
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "flex-start",
                        gap: "var(--space-1)",
                        width: "100%",
                        padding: "var(--space-2) var(--space-3)",
                        border: `1px solid ${
                          selectedKey === option.key
                            ? "var(--color-accent-primary)"
                            : "var(--color-border)"
                        }`,
                        borderRadius: "var(--radius-md)",
                        background:
                          selectedKey === option.key
                            ? "var(--color-accent-primary-soft)"
                            : "var(--color-surface)",
                        color: "var(--color-text-primary)",
                        cursor: "pointer",
                        fontSize: "var(--text-sm)",
                        textAlign: "left",
                      }}
                    >
                      <span>{option.label}</span>
                      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                        {option.hint}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {formError && (
          <p role="alert" data-testid="image-form-error" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
            {formError}
          </p>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)", marginTop: "var(--space-2)" }}>
          <Button variant="secondary" onClick={onClose} data-testid="image-cancel">
            取消
          </Button>
          <Button
            variant="primary"
            onClick={() => void submit()}
            disabled={!prompt.trim() || submitting || (tab === "edit" && !selectedKey)}
            data-testid="image-submit"
          >
            <Icon name="imagePicture" size={16} aria-hidden />
            {submitting ? "正在发送…" : tab === "edit" ? "提交编辑" : "开始生成"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
