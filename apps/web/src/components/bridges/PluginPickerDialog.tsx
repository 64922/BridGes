/**
 * 「选择已启用插件」对话框（Issue 36）。
 *
 * 可用集合 = 当前账户「已安装且启用」的插件：SKILL 插件（内置 + 用户包，
 * enabled）与 MCP 服务器（enabled）。分区展示能力/数据类别/权限披露，
 * 多选后确认全量替换会话选择（随对话持久化）；停用、卸载或撤权后的
 * 失效项由服务端清洗并在打开时解释影响（removedSelections）。五态
 * loading/error/permission/empty/内容均可用键盘完成；确认后不残留半
 * 状态（取消无副作用）。
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { listMcpServers, listPlugins } from "@/lib/api";
import type {
  ChatPluginSelectionItem,
  RemovedPluginSelection,
} from "@/lib/api";
import { Dialog } from "@/components/bridges/Dialog";
import { Icon } from "@/components/design-system/Icon";
import { StateBlock } from "@/components/bridges/StateBlock";

interface PluginPickerDialogProps {
  open: boolean;
  onClose: () => void;
  /** 当前已选插件（契约模型列表，确认后全量替换）。 */
  selected: ChatPluginSelectionItem[];
  /** 确认回调；传空数组表示清空全部选择；names 为显示名映射（chip 用）。 */
  onSelect: (
    selection: ChatPluginSelectionItem[],
    names: Record<string, string>
  ) => void;
  /** 本次读取时被服务端清洗的失效插件（停用/卸载/撤权，含中文原因）。 */
  removedSelections?: RemovedPluginSelection[];
}

interface AvailableSkill {
  kind: "skill";
  plugin_id: string;
  name: string;
  version: string;
  description: string;
  capabilities: string[];
  dataCategories: string[];
  source: string;
}

interface AvailableMcp {
  kind: "mcp";
  plugin_id: string;
  name: string;
  version: string;
  description: string;
  dataCategories: string[];
  networkDomains: string[];
  filesystemRead: string[];
  filesystemWrite: string[];
  externalCommands: string[];
  sensitiveOperations: string[];
  failureReason: string | null;
}

type AvailablePlugin = AvailableSkill | AvailableMcp;

function selectionKey(item: ChatPluginSelectionItem): string {
  return `${item.kind}:${item.plugin_id}`;
}

function categoryLabel(value: string): string {
  const labels: Record<string, string> = {
    current_message_text: "当前消息文本",
    attachment_files: "附件片段",
  };
  return labels[value] ?? value;
}

function iconFor(plugin: AvailablePlugin): "plugins" | "mcpServer" {
  return plugin.kind === "skill" ? "plugins" : "mcpServer";
}

/** 数据类别摘要（披露）：与权限清单一一对应，未声明即不接收对话数据。 */
function categoriesText(plugin: AvailablePlugin): string {
  if (plugin.dataCategories.length === 0) return "不接收对话数据";
  return `数据类别：${plugin.dataCategories.map(categoryLabel).join("、")}`;
}

function permissionsText(plugin: AvailablePlugin): string {
  if (plugin.kind === "skill") return "";
  const parts: string[] = [];
  if (plugin.networkDomains.length > 0) parts.push(`网络：${plugin.networkDomains.join("、")}`);
  if (plugin.filesystemRead.length > 0) parts.push(`读取目录：${plugin.filesystemRead.join("、")}`);
  if (plugin.filesystemWrite.length > 0) parts.push(`写入目录：${plugin.filesystemWrite.join("、")}`);
  if (plugin.externalCommands.length > 0) parts.push(`外部命令：${plugin.externalCommands.join("、")}`);
  if (plugin.sensitiveOperations.length > 0) parts.push("敏感操作（每次调用单独确认）");
  return parts.join("；");
}

export function PluginPickerDialog({
  open,
  onClose,
  selected,
  onSelect,
  removedSelections = [],
}: PluginPickerDialogProps) {
  const [phase, setPhase] = useState<"loading" | "error" | "ready">("loading");
  const [errorText, setErrorText] = useState("");
  const [available, setAvailable] = useState<AvailablePlugin[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setPhase("loading");
    setErrorText("");
    try {
      const [plugins, mcp] = await Promise.all([listPlugins(), listMcpServers()]);
      const skills: AvailablePlugin[] = [
        ...(plugins.builtin ?? [])
          .filter((item) => item.enabled)
          .map(
            (item): AvailableSkill => ({
              kind: "skill",
              plugin_id: item.skill_id,
              name: item.name,
              version: item.version,
              description: item.description,
              capabilities: item.capabilities ?? [],
              dataCategories: item.data_categories ?? [],
              source: item.source,
            })
          ),
        ...(plugins.user ?? [])
          .filter((item) => item.status === "installed" && item.enabled)
          .map(
            (item): AvailableSkill => ({
              kind: "skill",
              plugin_id: item.plugin_id,
              name: item.name,
              version: item.version,
              description: item.description ?? "",
              capabilities: item.capabilities ?? [],
              dataCategories: item.data_categories ?? [],
              source: item.source ?? "用户包",
            })
          ),
      ];
      const servers: AvailablePlugin[] = (mcp.servers ?? [])
        .filter((server) => server.enabled)
        .map(
          (server): AvailableMcp => ({
            kind: "mcp",
            plugin_id: server.mcp_id,
            name: server.name,
            version: server.version,
            description: server.description ?? "",
            dataCategories: server.permissions.data_categories ?? [],
            networkDomains: server.permissions.network_domains ?? [],
            filesystemRead: server.permissions.filesystem_read ?? [],
            filesystemWrite: server.permissions.filesystem_write ?? [],
            externalCommands: server.permissions.external_commands ?? [],
            sensitiveOperations: server.permissions.sensitive_operations ?? [],
            failureReason: server.failure_reason ?? null,
          })
        );
      setAvailable([...skills, ...servers]);
      // 初始勾选：当前有效选择 ∩ 可用集合（失效项已由服务端清洗解释）。
      const known = new Set([...skills, ...servers].map((item) => selectionKey(item)));
      setPicked(
        new Set(selected.filter((item) => known.has(selectionKey(item))).map(selectionKey))
      );
      setPhase("ready");
    } catch (error) {
      setPhase("error");
      setErrorText(error instanceof Error ? error.message : "插件列表加载失败，请稍后重试。");
    }
  }, [selected]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const skills = useMemo(() => available.filter((item) => item.kind === "skill"), [available]);
  const servers = useMemo(() => available.filter((item) => item.kind === "mcp"), [available]);

  const toggle = useCallback((key: string) => {
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const confirm = useCallback(() => {
    const result = available
      .filter((item) => picked.has(selectionKey(item)))
      .map(
        (item): ChatPluginSelectionItem => ({
          kind: item.kind,
          plugin_id: item.plugin_id,
        })
      );
    const names: Record<string, string> = {};
    for (const item of available) {
      names[selectionKey(item)] = item.name;
    }
    onSelect(result, names);
    onClose();
  }, [available, picked, onSelect, onClose]);

  const renderItem = (plugin: AvailablePlugin) => {
    const key = selectionKey(plugin);
    const checked = picked.has(key);
    return (
      <div
        key={key}
        role="checkbox"
        aria-checked={checked}
        tabIndex={0}
        data-testid={`plugin-picker-item-${plugin.plugin_id}`}
        onKeyDown={(event) => {
          if (event.key === " " || event.key === "Enter") {
            event.preventDefault();
            toggle(key);
          }
        }}
        onClick={() => toggle(key)}
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: "var(--space-3)",
          padding: "var(--space-3)",
          borderRadius: "var(--radius-md)",
          border: `1px solid ${checked ? "var(--color-accent-primary)" : "var(--color-border)"}`,
          backgroundColor: checked ? "var(--color-accent-primary-soft)" : "var(--color-bg-secondary)",
          cursor: "pointer",
        }}
      >
        <span
          aria-hidden
          style={{
            width: "2.25rem",
            height: "2.25rem",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            borderRadius: "var(--radius-sm)",
            backgroundColor: "var(--color-surface)",
            color: "var(--color-text-primary)",
            flexShrink: 0,
          }}
        >
          <Icon name={iconFor(plugin)} size={18} />
        </span>
        <span style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)", minWidth: 0 }}>
          <span style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
            <span style={{ fontWeight: 600, fontSize: "var(--text-sm)", color: "var(--color-text-primary)" }}>
              {plugin.name}
            </span>
            <span
              style={{
                fontSize: "var(--text-xs)",
                padding: "0 var(--space-1)",
                borderRadius: "999px",
                border: "1px solid var(--color-border)",
                color: "var(--color-text-secondary)",
              }}
            >
              v{plugin.version}
            </span>
            {plugin.kind === "mcp" && plugin.failureReason && (
              <span role="status" style={{ fontSize: "var(--text-xs)", color: "var(--color-status-error)" }}>
                {plugin.failureReason}
              </span>
            )}
          </span>
          {plugin.description && (
            <span style={{ fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
              {plugin.description}
            </span>
          )}
          {plugin.kind === "skill" && plugin.capabilities.length > 0 && (
            <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>
              能力：{plugin.capabilities.join("、")}
            </span>
          )}
          <span
            data-testid={`plugin-picker-categories-${plugin.plugin_id}`}
            style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}
          >
            {categoriesText(plugin)}
          </span>
          {permissionsText(plugin) && (
            <span
              data-testid={`plugin-picker-permissions-${plugin.plugin_id}`}
              style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}
            >
              {permissionsText(plugin)}
            </span>
          )}
        </span>
        <span
          aria-hidden
          style={{
            marginLeft: "auto",
            width: "1.125rem",
            height: "1.125rem",
            borderRadius: "var(--radius-sm)",
            border: `1px solid ${checked ? "var(--color-accent-primary)" : "var(--color-border)"}`,
            backgroundColor: checked ? "var(--color-accent-primary)" : "transparent",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          {checked && <Icon name="check" size={12} aria-hidden />}
        </span>
      </div>
    );
  };

  const section = (title: string, items: AvailablePlugin[], emptyHint: string) => (
    <section aria-label={title} style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
      <h3 style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>{title}</h3>
      {items.length === 0 ? (
        <p
          data-testid="plugin-picker-empty"
          style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}
        >
          {emptyHint}
        </p>
      ) : (
        items.map(renderItem)
      )}
    </section>
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="选择已启用插件"
      description="选择本对话可用的插件与 MCP 服务器；选择随对话保存。停用、卸载或撤权后将立即从可用集合移除并说明影响。"
    >
      {removedSelections.length > 0 && (
        <div
          role="status"
          data-testid="plugin-picker-removed"
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-1)",
            padding: "var(--space-2) var(--space-3)",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--color-status-wait-bg)",
            backgroundColor: "var(--color-status-wait-bg)",
            fontSize: "var(--text-sm)",
            color: "var(--color-text-primary)",
          }}
        >
          {removedSelections.map((entry) => (
            <span key={`${entry.kind}:${entry.plugin_id}`}>
              「{entry.name}」已从本对话可用插件中移除：{entry.reason}
            </span>
          ))}
        </div>
      )}
      {phase === "loading" && (
        <StateBlock kind="loading" title="正在加载可用插件…" />
      )}
      {phase === "error" && (
        <StateBlock kind="error" title={errorText} actionLabel="重试" onAction={() => void load()} />
      )}
      {phase === "ready" && (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
            {section("SKILL 插件", skills, "当前没有可选择的已启用 SKILL 插件，请先到插件中心安装或启用。")}
            {section("MCP 服务器", servers, "当前没有可选择的已启用 MCP 服务器，请先到插件中心安装或启用。")}
            {available.length === 0 && (
              <p data-testid="plugin-picker-empty-all" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>
                当前账户没有任何可选择的插件，请先到插件中心安装并启用。
              </p>
            )}
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)", marginTop: "var(--space-4)" }}>
            <button type="button" onClick={onClose} data-testid="plugin-picker-cancel" style={{ padding: "var(--space-2) var(--space-4)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border)", backgroundColor: "transparent", color: "var(--color-text-primary)", cursor: "pointer" }}>
              取消
            </button>
            <button
              type="button"
              onClick={confirm}
              data-testid="plugin-picker-confirm"
              disabled={phase !== "ready"}
              style={{ padding: "var(--space-2) var(--space-4)", borderRadius: "var(--radius-md)", border: "none", backgroundColor: "var(--color-accent-primary)", color: "var(--color-text-on-accent)", cursor: "pointer" }}
            >
              确认选择（{picked.size}）
            </button>
          </div>
        </>
      )}
    </Dialog>
  );
}
