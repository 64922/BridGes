/**
 * 「调用 MCP 插件」对话框（Issue 36）。
 *
 * 对当前对话选中的 MCP 服务器发起真实调用：工具名 + JSON 参数由用户
 * 明确指定（不依赖模型臆造）；数据切片披露本次调用发送给插件的内容
 * （当前消息文本与附件片段，可清空）。提交走真实消息流（mcp_call 载荷）
 * → 服务端校验选中 → invoke → 结果卡；敏感操作挂起由消息卡确认。
 * 失败原因中文可操作；取消无残留。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { ChatPluginSelectionItem, McpCallRequestPayload } from "@/lib/api";
import { Dialog } from "@/components/bridges/Dialog";
import { Icon } from "@/components/design-system/Icon";

interface McpInvokeDialogProps {
  open: boolean;
  /** 目标 MCP（必须是本对话已选中的服务器；未选中不会出现入口）。 */
  server: { mcp_id: string; name: string; description?: string } | null;
  onClose: () => void;
  /** 提交真实消息：返回 false 表示失败（由调用方提示）。 */
  onSubmit: (payload: McpCallRequestPayload) => Promise<boolean> | boolean;
  /** 当前对话选中的插件（用于在对话框内展示披露的可用集合）。 */
  selected: ChatPluginSelectionItem[];
}

function parseJson(value: string): { ok: true; data: Record<string, unknown> } | { ok: false; message: string } {
  const trimmed = value.trim();
  if (!trimmed) return { ok: true, data: {} };
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ok: false, message: "参数必须是 JSON 对象，例如 {\"key\": \"value\"}。" };
    }
    return { ok: true, data: parsed as Record<string, unknown> };
  } catch {
    return { ok: false, message: "参数不是合法 JSON，请检查格式。" };
  }
}

export function McpInvokeDialog({
  open,
  server,
  onClose,
  onSubmit,
  selected,
}: McpInvokeDialogProps) {
  const [tool, setTool] = useState("");
  const [inputText, setInputText] = useState("{\n  \n}");
  const [sliceText, setSliceText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorText, setErrorText] = useState("");
  const toolRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setTool("");
    setInputText("{\n  \n}");
    setSliceText("");
    setSubmitting(false);
    setErrorText("");
    // 打开后聚焦工具名输入框（Dialog 焦点陷阱已把首焦点给对话框内元素）
    const timer = window.setTimeout(() => toolRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [open, server]);

  const isSelected = server !== null && selected.some(
    (item) => item.kind === "mcp" && item.plugin_id === server.mcp_id
  );

  const submit = useCallback(async () => {
    if (!server) return;
    const toolName = tool.trim();
    if (!toolName) {
      setErrorText("请填写要调用的工具名。");
      return;
    }
    const parsed = parseJson(inputText);
    if (!parsed.ok) {
      setErrorText(parsed.message);
      return;
    }
    setSubmitting(true);
    setErrorText("");
    try {
      const ok = await onSubmit({
        mcp_id: server.mcp_id,
        tool: toolName,
        input: parsed.data,
        data_slice: {
          text: sliceText,
          attachments: [],
        },
      });
      if (ok !== false) onClose();
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : "调用提交失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  }, [server, tool, inputText, sliceText, onSubmit, onClose]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`调用 MCP 插件${server ? `「${server.name}」` : ""}`}
      description="工具名与参数由你明确指定；本次调用只发送下方数据切片，不含画像、完整聊天历史或项目数据。"
    >
      {!isSelected && (
        <p role="alert" data-testid="mcp-invoke-not-selected" style={{ margin: 0, color: "var(--color-status-error)", fontSize: "var(--text-sm)" }}>
          该 MCP 插件未在本对话选择，无法调用；请先在「+」菜单选择插件。
        </p>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          <span style={{ fontSize: "var(--text-sm)", color: "var(--color-text-primary)" }}>工具名</span>
          <input
            ref={toolRef}
            data-testid="mcp-invoke-tool"
            value={tool}
            onChange={(event) => setTool(event.target.value)}
            placeholder="例如：echo"
            disabled={!isSelected}
            style={{
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-bg-primary)",
              color: "var(--color-text-primary)",
            }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          <span style={{ fontSize: "var(--text-sm)", color: "var(--color-text-primary)" }}>工具参数（JSON）</span>
          <textarea
            data-testid="mcp-invoke-input"
            value={inputText}
            onChange={(event) => setInputText(event.target.value)}
            rows={4}
            spellCheck={false}
            disabled={!isSelected}
            style={{
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-bg-primary)",
              color: "var(--color-text-primary)",
              fontFamily: "var(--font-mono)",
              fontSize: "var(--text-sm)",
              resize: "vertical",
            }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          <span style={{ fontSize: "var(--text-sm)", color: "var(--color-text-primary)" }}>
            授权数据切片（本次调用发送给插件的内容）
          </span>
          <textarea
            data-testid="mcp-invoke-slice"
            value={sliceText}
            onChange={(event) => setSliceText(event.target.value)}
            rows={2}
            placeholder="可填写本次调用明确授权的文本；留空表示只发送参数。"
            disabled={!isSelected}
            style={{
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-bg-primary)",
              color: "var(--color-text-primary)",
              fontSize: "var(--text-sm)",
              resize: "vertical",
            }}
          />
        </label>
        <p
          data-testid="mcp-invoke-disclosure"
          style={{
            margin: 0,
            padding: "var(--space-2) var(--space-3)",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--color-border)",
            backgroundColor: "var(--color-bg-secondary)",
            fontSize: "var(--text-xs)",
            color: "var(--color-text-secondary)",
          }}
        >
          <Icon name="info" size={13} aria-hidden /> 插件只收到本对话框填写的参数与授权文本；每次调用单独记录并审计。
        </p>
        {errorText && (
          <p role="alert" data-testid="mcp-invoke-error" style={{ margin: 0, color: "var(--color-status-error)", fontSize: "var(--text-sm)" }}>
            {errorText}
          </p>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)" }}>
          <button
            type="button"
            onClick={onClose}
            data-testid="mcp-invoke-cancel"
            style={{ padding: "var(--space-2) var(--space-4)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border)", backgroundColor: "transparent", color: "var(--color-text-primary)", cursor: "pointer" }}
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void submit()}
            data-testid="mcp-invoke-submit"
            disabled={!isSelected || submitting}
            style={{ padding: "var(--space-2) var(--space-4)", borderRadius: "var(--radius-md)", border: "none", backgroundColor: "var(--color-accent-primary)", color: "var(--color-text-on-accent)", cursor: "pointer" }}
          >
            {submitting ? "提交中…" : "调用工具"}
          </button>
        </div>
      </div>
    </Dialog>
  );
}
