"use client";

/**
 * MCP 插件中心分区（Issue 35）。
 *
 * 显式授权 MCP 管理：按账户安装固定版本 + 完整性锁定的 MCP 安装描述
 * （MCP.yaml），安装前逐项预览权限清单（网络域名/文件读写目录/外部
 * 命令/数据类别/敏感操作），未声明或未同意权限一律不可用；MCP 在独立
 * 受限进程中运行，展示真实调用统计（次数/最近结果/失败原因）；敏感
 * 操作（写文件/运行外部命令/外发）每次调用独立再次确认（目标与影响），
 * 拒绝即安全终止；可启停、撤权、卸载。全部操作中文反馈、纯键盘可达、
 * 切换账户清态。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { StateBlock } from "@/components/bridges/StateBlock";
import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { Icon, type IconName } from "@/components/design-system/Icon";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { useAuth } from "@/context/AuthContext";
import {
  approveMcpConfirmation,
  checkMcpDescriptor,
  classifyApiError,
  denyMcpConfirmation,
  disableMcp,
  enableMcp,
  installMcpDescriptor,
  invokeMcp,
  listMcpServers,
  revokeMcpPermissions,
  uninstallMcp,
  type McpCallResult,
  type McpCheckResult,
  type McpPermissionManifest,
  type McpSensitiveConfirmation,
  type McpServerProjection,
  type McpStatus,
} from "@/lib/api";

import styles from "./McpCenter.module.css";

type PageState = "loading" | "ready" | "reauth" | "error";

// 与后端 checker.MAX_DESCRIPTOR_BYTES 一致（前端仅预检）。
const MAX_DESCRIPTOR_BYTES = 256 * 1024;

const SENSITIVE_LABELS: Record<string, string> = {
  write_file: "写文件",
  run_command: "运行外部命令",
  send_external: "向外部服务提交",
};

const DATA_CATEGORY_LABELS: Record<string, string> = {
  current_message_text: "当前消息文本",
  attachment_files: "附件文件",
  public_query_terms: "公开查询词",
};

const PERMISSION_GROUPS: { key: keyof McpPermissionManifest; label: string; hint: string }[] = [
  { key: "network_domains", label: "网络域名", hint: "允许访问的 HTTPS 域名" },
  { key: "filesystem_read", label: "可读目录", hint: "允许读取的本地绝对目录" },
  { key: "filesystem_write", label: "可写目录", hint: "允许写入的本地绝对目录（敏感）" },
  { key: "external_commands", label: "外部命令", hint: "允许执行的受控命令（敏感）" },
  { key: "data_categories", label: "数据类别", hint: "每次调用将接收的数据" },
  { key: "sensitive_operations", label: "敏感操作", hint: "执行前必须再次确认" },
];

function McpStatusChip({ status }: { status: McpStatus }) {
  const config: Record<string, { tone: "success" | "error" | "neutral" | "active"; icon: IconName; label: string }> = {
    healthy: { tone: "success", icon: "check", label: "运行中" },
    starting: { tone: "active", icon: "retry", label: "启动中" },
    disabled: { tone: "neutral", icon: "pause", label: "已停用" },
    failed: { tone: "error", icon: "alert", label: "失败" },
    stopped: { tone: "neutral", icon: "stopSquare", label: "已停止" },
  };
  const item = config[status] ?? { tone: "neutral" as const, icon: "info" as IconName, label: status };
  const palette: Record<string, { color: string; bg: string }> = {
    success: { color: "var(--color-status-success)", bg: "var(--color-status-success-bg)" },
    error: { color: "var(--color-status-error)", bg: "var(--color-status-error-bg)" },
    neutral: { color: "var(--color-status-unknown)", bg: "var(--color-status-unknown-bg)" },
    active: { color: "var(--color-accent-secondary)", bg: "var(--color-status-info-bg)" },
  };
  const colors = palette[item.tone];
  return (
    <span className={styles.statusChip} style={{ color: colors.color, backgroundColor: colors.bg }}>
      <Icon name={item.icon} size={14} aria-hidden />
      <span>{item.label}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// 权限清单展示
// ---------------------------------------------------------------------------

function PermissionTable({ permissions }: { permissions: McpPermissionManifest }) {
  return (
    <table className={styles.permissionTable}>
      <caption className="sc-visually-hidden">权限清单</caption>
      <tbody>
        {PERMISSION_GROUPS.map((group) => {
          const values = permissions[group.key] as string[];
          return (
            <tr key={group.key}>
              <th scope="row">
                {group.label}
                <span className={styles.permissionHint}>{group.hint}</span>
              </th>
              <td data-testid={`perm-${group.key}`}>
                {values.length === 0 ? (
                  <span className={styles.permissionEmpty}>无（默认拒绝）</span>
                ) : (
                  <ul className={styles.permissionList}>
                    {values.map((value) => (
                      <li key={value}>
                        <code>
                          {group.key === "sensitive_operations"
                            ? (SENSITIVE_LABELS[value] ?? value)
                            : group.key === "data_categories"
                              ? (DATA_CATEGORY_LABELS[value] ?? value)
                              : value}
                        </code>
                      </li>
                    ))}
                  </ul>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function PermissionSummary({ permissions }: { permissions: McpPermissionManifest }) {
  const parts: string[] = [];
  const count = (key: keyof McpPermissionManifest) => (permissions[key] as string[] | undefined)?.length ?? 0;
  if (count("network_domains") > 0) {
    parts.push(`网络 ${count("network_domains")}`);
  }
  if (count("filesystem_read") > 0) {
    parts.push(`读 ${count("filesystem_read")}`);
  }
  if (count("filesystem_write") > 0) {
    parts.push(`写 ${count("filesystem_write")}`);
  }
  if (count("external_commands") > 0) {
    parts.push(`命令 ${count("external_commands")}`);
  }
  if (count("sensitive_operations") > 0) {
    parts.push(`敏感 ${count("sensitive_operations")}`);
  }
  return (
    <span className={styles.permissionSummary}>
      {parts.length === 0 ? "无权限（默认拒绝）" : parts.join(" · ")}
    </span>
  );
}

// ---------------------------------------------------------------------------
// MCP 卡片
// ---------------------------------------------------------------------------

function McpCard({
  server,
  busy,
  onToggle,
  onInvoke,
  onRevoke,
  onUninstall,
}: {
  server: McpServerProjection;
  busy: boolean;
  onToggle: (server: McpServerProjection) => void;
  onInvoke: (server: McpServerProjection) => void;
  onRevoke: (server: McpServerProjection) => void;
  onUninstall: (server: McpServerProjection) => void;
}) {
  const [permissionsOpen, setPermissionsOpen] = useState(false);
  return (
    <article className={`sc-card ${styles.mcpCard}`} data-testid={`mcp-${server.mcp_id}`}>
      <div className={styles.cardHeader}>
        <div className={styles.cardTitleBlock}>
          <div className={styles.cardTitleRow}>
            <h3 className={styles.cardTitle}>{server.name}</h3>
            <McpStatusChip status={server.status} />
            <span className={styles.versionBadge}>{server.version}</span>
            {server.integrity ? (
              <span className={styles.integrityBadge} title={`完整性锁定 ${server.integrity}`}>
                <Icon name="check" size={13} aria-hidden />
                完整性已锁定
              </span>
            ) : null}
          </div>
          <p className={styles.cardDescription}>{server.description ?? "无能力说明。"}</p>
          <div className={styles.metaRow}>
            <span className={styles.metaItem}>
              <Icon name="info" size={13} aria-hidden />
              来源：{server.source}
            </span>
            <PermissionSummary permissions={server.permissions} />
          </div>
        </div>
      </div>

      <div className={styles.statsRow}>
        <div className={styles.statItem}>
          <strong>{server.call_count}</strong>
          <span>调用次数</span>
        </div>
        <div className={styles.statItem}>
          <strong className={server.last_call_status === "failed" || server.last_call_status === "denied" ? styles.statBad : undefined}>
            {server.last_call_status === "success"
              ? "成功"
              : server.last_call_status === "failed"
                ? "失败"
                : server.last_call_status === "denied"
                  ? "已拒绝"
                  : "—"}
          </strong>
          <span>最近结果</span>
        </div>
        <div className={styles.statItem}>
          <strong>{server.last_call_at ? new Date(server.last_call_at).toLocaleString("zh-CN", { hour12: false }) : "—"}</strong>
          <span>最近调用</span>
        </div>
      </div>

      {server.failure_reason ? (
        <p className={styles.failureReason} role="alert">
          <Icon name="alert" size={14} aria-hidden />
          失败原因：{server.failure_reason}
        </p>
      ) : null}

      <button
        type="button"
        className={styles.permissionsToggle}
        aria-expanded={permissionsOpen}
        onClick={() => setPermissionsOpen((open) => !open)}
      >
        <Icon name={permissionsOpen ? "chevronDown" : "chevronRight"} size={14} aria-hidden />
        权限清单
        {permissionsOpen ? "（收起）" : "（展开）"}
      </button>
      {permissionsOpen ? (
        <div className={styles.permissionsPanel} data-testid={`mcp-permissions-${server.mcp_id}`}>
          <PermissionTable permissions={server.permissions} />
        </div>
      ) : null}

      <div className={styles.cardActions}>
        <Button
          size="sm"
          variant="secondary"
          disabled={busy || server.status === "disabled" || server.status === "failed"}
          onClick={() => onInvoke(server)}
          data-testid={`mcp-invoke-${server.mcp_id}`}
        >
          调用
        </Button>
        {server.enabled ? (
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => onToggle(server)}>
            停用
          </Button>
        ) : (
          <Button
            size="sm"
            variant="secondary"
            disabled={busy || server.status === "failed"}
            onClick={() => onToggle(server)}
          >
            启用
          </Button>
        )}
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => onRevoke(server)}>
          权限
        </Button>
        <Button size="sm" variant="danger" disabled={busy} onClick={() => onUninstall(server)}>
          卸载
        </Button>
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------
// 安装对话框：上传 → 检查 → 权限预览 → 确认安装
// ---------------------------------------------------------------------------

type InstallStep = "pick" | "checking" | "preview" | "rejected" | "installing" | "done";

function McpInstallDialog({ open, onClose, onInstalled }: { open: boolean; onClose: () => void; onInstalled: () => void }) {
  const [step, setStep] = useState<InstallStep>("pick");
  const [fileName, setFileName] = useState("");
  const [check, setCheck] = useState<McpCheckResult | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setStep("pick");
      setFileName("");
      setCheck(null);
      setInstallError(null);
      setSelectedFile(null);
    }
  }, [open]);

  const pickFile = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > MAX_DESCRIPTOR_BYTES) {
      setInstallError(`安装描述超过 ${MAX_DESCRIPTOR_BYTES / 1024} KB 上限，请精简后重试。`);
      setStep("rejected");
      return;
    }
    setFileName(file.name);
    setSelectedFile(file);
    setStep("checking");
    setInstallError(null);
    try {
      const result = await checkMcpDescriptor(file);
      setCheck(result);
      setStep(result.ok ? "preview" : "rejected");
    } catch (cause) {
      setInstallError(cause instanceof Error ? cause.message : "检查失败，请重新选择。");
      setStep("rejected");
    }
  };

  const confirmInstall = async () => {
    const file = selectedFile;
    if (!file) return;
    setStep("installing");
    setInstallError(null);
    try {
      await installMcpDescriptor(file);
      setStep("done");
    } catch (cause) {
      setInstallError(cause instanceof Error ? cause.message : "安装失败，请重试。");
      setStep("rejected");
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title="安装 MCP 服务器">
      {step === "pick" || step === "checking" ? (
        <div className={styles.uploadZone} data-testid="mcp-install-zone">
          <Icon name="uploadFile" size={28} aria-hidden />
          <p>选择 MCP 安装描述（.yaml / .yml）</p>
          <p className={styles.uploadHint}>
            描述包含固定版本、来源、启动命令与权限清单；未锁版本、描述损坏或来源不匹配将拒绝安装。
          </p>
          <input
            ref={inputRef}
            type="file"
            accept=".yaml,.yml"
            className="sc-visually-hidden"
            data-testid="mcp-descriptor-input"
            onChange={(event) => void pickFile(event.target.files?.[0])}
          />
          <Button
            variant="secondary"
            disabled={step === "checking"}
            isLoading={step === "checking"}
            onClick={() => inputRef.current?.click()}
          >
            {step === "checking" ? "正在检查…" : "选择文件"}
          </Button>
          {fileName ? <p className={styles.fileName}>{fileName}</p> : null}
        </div>
      ) : null}

      {step === "rejected" ? (
        <div data-testid="mcp-install-rejected">
          <ErrorSummary title="安装检查未通过" errors={[installError ?? "请修正描述后重新上传。"]} />
          {check?.rejected_reasons?.length ? (
            <ul className={styles.reasonList}>
              {check.rejected_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}
          <div className={styles.dialogActions}>
            <Button variant="secondary" onClick={() => setStep("pick")}>
              重新选择
            </Button>
            <Button variant="ghost" onClick={onClose}>
              取消
            </Button>
          </div>
        </div>
      ) : null}

      {step === "preview" && check ? (
        <div data-testid="mcp-install-preview">
          <p className={styles.previewIntro}>
            将安装 <strong>{check.name}</strong>（标识 {check.mcp_id}，版本{" "}
            <code>{check.version}</code>，来源 {check.source}
            {check.integrity ? `，完整性 ${check.integrity}` : "，完整性由系统安装时锁定"}
            ）。请逐项确认以下权限——未声明或未同意的权限一律不可用。MCP
            服务器程序经平台工具访问文件、网络与命令，平台以受限环境与允许
            清单强制边界，并记录全部调用与越权尝试审计：
          </p>
          <div className={styles.permissionsPanel}>
            <PermissionTable permissions={check.permissions!} />
          </div>
          <div className={styles.dialogActions}>
            <Button
              variant="primary"
              onClick={() => void confirmInstall()}
              data-testid="mcp-confirm-install"
            >
              确认安装
            </Button>
            <Button variant="ghost" onClick={onClose}>
              取消
            </Button>
          </div>
        </div>
      ) : null}

      {step === "installing" ? <LoadingStatus message="正在安装…" /> : null}

      {step === "done" ? (
        <div data-testid="mcp-install-done">
          <p className={styles.successText}>
            <Icon name="check" size={16} aria-hidden />
            安装成功。MCP 已在独立受限进程中就绪，可在插件中心调用。
          </p>
          <div className={styles.dialogActions}>
            <Button
              variant="primary"
              onClick={() => {
                onClose();
                onInstalled();
              }}
            >
              完成
            </Button>
          </div>
        </div>
      ) : null}
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 调用对话框：工具 / 入参 / 数据切片 → 调用 → 结果（或敏感挂起）
// ---------------------------------------------------------------------------

function McpInvokeDialog({
  server,
  open,
  onClose,
  onSensitivePending,
  onInvoked,
}: {
  server: McpServerProjection | null;
  open: boolean;
  onClose: () => void;
  onSensitivePending: (confirmation: McpSensitiveConfirmation) => void;
  onInvoked: () => void;
}) {
  const [tool, setTool] = useState("echo");
  const [inputText, setInputText] = useState("");
  const [sliceText, setSliceText] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<McpCallResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setTool("echo");
      setInputText("");
      setSliceText("");
      setResult(null);
      setError(null);
      setRunning(false);
    }
  }, [open]);

  if (!server) return null;
  const declaredCategories = new Set(server.permissions.data_categories);
  const canSendText = declaredCategories.has("current_message_text");

  const run = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      let parsedInput: Record<string, unknown> = {};
      if (inputText.trim()) {
        parsedInput = JSON.parse(inputText) as Record<string, unknown>;
      }
      const outcome = await invokeMcp(server.mcp_id, {
        tool,
        input: parsedInput,
        data_slice: {
          text: canSendText ? sliceText : "",
          attachments: [],
        },
      });
      if (outcome.status === "sensitive_pending" && outcome.confirmation) {
        onSensitivePending(outcome.confirmation);
        return;
      }
      setResult(outcome);
      onInvoked();
    } catch (cause) {
      if (cause instanceof SyntaxError) {
        setError("入参不是有效的 JSON，请检查后重试。");
      } else {
        setError(cause instanceof Error ? cause.message : "调用失败，请重试。");
      }
    } finally {
      setRunning(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title="调用">
      <div className={styles.formField}>
        <label htmlFor="mcp-tool">工具名</label>
        <input
          id="mcp-tool"
          value={tool}
          onChange={(event) => setTool(event.target.value)}
          data-testid="mcp-tool-input"
        />
      </div>
      <div className={styles.formField}>
        <label htmlFor="mcp-input">入参（JSON，可选）</label>
        <textarea
          id="mcp-input"
          rows={3}
          value={inputText}
          onChange={(event) => setInputText(event.target.value)}
          placeholder='例如 {"path": "C:\\notes\\a.txt"}'
          data-testid="mcp-input-field"
        />
      </div>
      <div className={styles.formField}>
        <label htmlFor="mcp-slice">数据切片（当前消息明确授权的文本）</label>
        <textarea
          id="mcp-slice"
          rows={3}
          value={sliceText}
          onChange={(event) => setSliceText(event.target.value)}
          disabled={!canSendText}
          placeholder={
            canSendText ? "只把这里的内容传给 MCP；不包含画像、历史与项目数据。" : "该 MCP 未声明接收文本，不可传递。"
          }
          data-testid="mcp-slice-input"
        />
        {!canSendText ? (
          <p className={styles.uploadHint}>该 MCP 未声明 current_message_text 类别，本次调用不会接收任何文本。</p>
        ) : null}
      </div>

      {error ? <ErrorSummary errors={[error]} /> : null}
      {result ? (
        <div className={styles.invokeResult} data-testid="mcp-invoke-result">
          {result.status === "success" ? (
            <>
              <p className={styles.successText}>
                <Icon name="check" size={16} aria-hidden />
                调用成功
              </p>
              <pre>{JSON.stringify(result.result, null, 2)}</pre>
            </>
          ) : (
            <p className={styles.failureReason} role="alert">
              <Icon name="alert" size={14} aria-hidden />
              {result.error_message ?? "调用失败"}
            </p>
          )}
        </div>
      ) : null}

      <div className={styles.dialogActions}>
        <Button variant="primary" disabled={running} isLoading={running} onClick={() => void run()}>
          调用
        </Button>
        <Button variant="ghost" onClick={onClose}>
          关闭
        </Button>
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 敏感操作确认对话框（目标 + 影响，仅本次有效）
// ---------------------------------------------------------------------------

function McpSensitiveConfirmDialog({
  confirmation,
  onResolve,
  onClose,
}: {
  confirmation: McpSensitiveConfirmation | null;
  onResolve: (approved: boolean) => void;
  onClose: () => void;
}) {
  if (!confirmation) return null;
  return (
    <Dialog open onClose={onClose} title="敏感操作确认">
      <div className={styles.sensitiveWarning} role="alert">
        <Icon name="alert" size={20} aria-hidden />
        <p>
          <strong>{confirmation.tool}</strong> 请求执行敏感操作：
          <span className={styles.sensitiveKind}>{SENSITIVE_LABELS[confirmation.kind] ?? confirmation.kind}</span>
        </p>
      </div>
      <div className={styles.sensitiveTarget} data-testid="mcp-sensitive-target">
        <span className={styles.sensitiveTargetLabel}>目标</span>
        <code>{confirmation.target}</code>
      </div>
      <p className={styles.sensitiveImpact}>{confirmation.impact}</p>
      <p className={styles.uploadHint}>
        确认仅对本次调用有效，不会扩展成永久授权；拒绝后本次调用立即安全终止。
      </p>
      <div className={styles.dialogActions}>
        <Button
          variant="secondary"
          onClick={() => onResolve(false)}
          data-testid="mcp-sensitive-deny"
        >
          拒绝
        </Button>
        <Button
          variant="danger"
          onClick={() => onResolve(true)}
          data-testid="mcp-sensitive-approve"
        >
          确认执行
        </Button>
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 撤权对话框：逐项开关保留/移除权限
// ---------------------------------------------------------------------------

function McpRevokeDialog({
  server,
  onClose,
  onRevoked,
}: {
  server: McpServerProjection | null;
  onClose: () => void;
  onRevoked: () => void;
}) {
  const [enabledGroups, setEnabledGroups] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (server) {
      const permissions = server.permissions;
      setEnabledGroups({
        network_domains: (permissions.network_domains ?? []).length > 0,
        filesystem_read: (permissions.filesystem_read ?? []).length > 0,
        filesystem_write: (permissions.filesystem_write ?? []).length > 0,
        external_commands: (permissions.external_commands ?? []).length > 0,
        data_categories: (permissions.data_categories ?? []).length > 0,
        sensitive_operations: (permissions.sensitive_operations ?? []).length > 0,
      });
      setError(null);
    }
  }, [server]);

  if (!server) return null;

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      const keep = (group: { key: keyof McpPermissionManifest }) =>
        enabledGroups[group.key] ? (server.permissions[group.key] as string[]) : [];
      const manifest: McpPermissionManifest = {
        network_domains: keep(PERMISSION_GROUPS[0]),
        filesystem_read: keep(PERMISSION_GROUPS[1]),
        filesystem_write: keep(PERMISSION_GROUPS[2]),
        external_commands: keep(PERMISSION_GROUPS[3]),
        data_categories: keep(PERMISSION_GROUPS[4]),
        sensitive_operations: keep(PERMISSION_GROUPS[5]) as McpPermissionManifest["sensitive_operations"],
      };
      await revokeMcpPermissions(server.mcp_id, manifest);
      onRevoked();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "撤权失败，请重试。");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onClose={onClose} title="权限管理">
      <p className={styles.uploadHint}>
        关闭某一项后，该权限立即从允许清单移除；移除敏感权限会终止仍依赖该权限的运行。新调用立即使用新清单。
      </p>
      <div className={styles.revokeGrid} data-testid="mcp-revoke-groups">
        {PERMISSION_GROUPS.map((group) => {
          const values = server.permissions[group.key] as string[];
          const enabled = enabledGroups[group.key] ?? false;
          return (
            <label key={group.key} className={styles.revokeRow}>
              <input
                type="checkbox"
                checked={enabled}
                onChange={(event) =>
                  setEnabledGroups((current) => ({ ...current, [group.key]: event.target.checked }))
                }
                disabled={values.length === 0}
              />
              <span>
                {group.label}
                <span className={styles.uploadHint}>
                  {values.length === 0 ? "（当前无此项）" : `${values.length} 项`}
                </span>
              </span>
            </label>
          );
        })}
      </div>
      {error ? <ErrorSummary errors={[error]} /> : null}
      <div className={styles.dialogActions}>
        <Button variant="primary" disabled={saving} isLoading={saving} onClick={() => void submit()}>
          保存权限
        </Button>
        <Button variant="ghost" onClick={onClose}>
          取消
        </Button>
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// MCP 中心主组件
// ---------------------------------------------------------------------------

export function McpCenter() {
  const { refreshSession } = useAuth();
  const [pageState, setPageState] = useState<PageState>("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [servers, setServers] = useState<McpServerProjection[] | null>(null);
  const [installOpen, setInstallOpen] = useState(false);
  const [invokeTarget, setInvokeTarget] = useState<McpServerProjection | null>(null);
  const [confirmation, setConfirmation] = useState<McpSensitiveConfirmation | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<McpServerProjection | null>(null);
  const [uninstallTarget, setUninstallTarget] = useState<McpServerProjection | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const loaded = useRef(false);

  const load = useCallback(async (silent = false) => {
    if (!silent) {
      setPageState("loading");
    }
    setLoadError(null);
    try {
      const list = await listMcpServers();
      setServers(list.servers ?? []);
      setPageState("ready");
    } catch (cause) {
      const kind = classifyApiError(cause);
      if (kind === "reauth") {
        setPageState("reauth");
        return;
      }
      if (kind === "session") {
        await refreshSession();
        setReloadKey((key) => key + 1);
        return;
      }
      setLoadError(cause instanceof Error ? cause.message : "MCP 状态读取失败，请稍后重试。");
      setPageState("error");
    }
  }, [refreshSession]);

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    void load();
  }, [load]);

  useEffect(() => {
    if (reloadKey === 0) return;
    void load();
  }, [reloadKey, load]);

  const guard = (cause: unknown): boolean => {
    const kind = classifyApiError(cause);
    if (kind === "reauth") {
      setPageState("reauth");
      return true;
    }
    if (kind === "session") {
      void refreshSession().then(() => setReloadKey((key) => key + 1));
      return true;
    }
    return false;
  };

  const toggle = async (server: McpServerProjection) => {
    setBusyId(server.mcp_id);
    setNotice(null);
    try {
      const list = server.enabled ? await disableMcp(server.mcp_id) : await enableMcp(server.mcp_id);
      setServers(list.servers ?? []);
      setNotice(server.enabled ? `已停用 ${server.name}` : `已启用 ${server.name}`);
    } catch (cause) {
      if (!guard(cause)) {
        setNotice(cause instanceof Error ? cause.message : "操作失败，请稍后重试。");
      }
    } finally {
      setBusyId(null);
    }
  };

  const confirmUninstall = async () => {
    if (!uninstallTarget) return;
    setBusyId(uninstallTarget.mcp_id);
    setNotice(null);
    try {
      const list = await uninstallMcp(uninstallTarget.mcp_id);
      setServers(list.servers ?? []);
      setUninstallTarget(null);
      setNotice(`已卸载 ${uninstallTarget.name}`);
    } catch (cause) {
      if (!guard(cause)) {
        setNotice("卸载失败，请稍后重试。");
      }
    } finally {
      setBusyId(null);
    }
  };

  const resolveSensitive = async (approved: boolean) => {
    if (!confirmation) return;
    setNotice(null);
    try {
      const outcome = approved
        ? await approveMcpConfirmation(confirmation.mcp_id, confirmation.confirmation_id)
        : await denyMcpConfirmation(confirmation.mcp_id, confirmation.confirmation_id);
      setConfirmation(null);
      if (outcome.status === "success") {
        setNotice("敏感操作已确认执行，调用完成。");
      } else {
        setNotice(outcome.error_message ?? "调用已终止。");
      }
      void load(true);
    } catch (cause) {
      if (!guard(cause)) {
        setNotice("确认失败，请重试。");
      }
    }
  };

  if (pageState === "loading") {
    return <LoadingStatus message="MCP 插件中心加载中…" />;
  }
  if (pageState === "reauth") {
    return (
      <StateBlock
        kind="permission"
        title="没有访问 MCP 的权限"
        description="会话需要重新验证，请重新登录当前账户后再访问。"
      />
    );
  }
  if (pageState === "error") {
    return (
      <StateBlock
        kind="error"
        title="MCP 插件中心加载失败"
        description={loadError ?? "请稍后重试。"}
        actionLabel="重试"
        onAction={() => void load()}
      />
    );
  }

  const current = servers ?? [];

  return (
    <section aria-labelledby="mcp-center-title" className={styles.mcpSection}>
      <div className={styles.sectionHeader}>
        <div>
          <h2 id="mcp-center-title" className="sc-section-title">
            MCP 服务器
          </h2>
          <p className={styles.sectionHint}>
            按账户安装与治理 MCP：固定版本、逐项权限预览、受限进程运行、敏感操作再次确认。所有调用只接收当前消息明确授权的数据。
          </p>
        </div>
        <Button variant="primary" onClick={() => setInstallOpen(true)} data-testid="mcp-install-open">
          <Icon name="plus" size={16} aria-hidden />
          安装 MCP
        </Button>
      </div>

      {notice ? (
        <p className={styles.notice} role="status" data-testid="mcp-notice">
          {notice}
        </p>
      ) : null}

      {current.length === 0 ? (
        <StateBlock
          kind="empty"
          title="当前账户还没有 MCP 服务器"
          description="安装带固定版本与权限声明的 MCP 安装描述，安装前会逐项展示其权限。"
          actionLabel="安装 MCP"
          onAction={() => setInstallOpen(true)}
        />
      ) : (
        <div className={styles.mcpGrid}>
          {current.map((server) => (
            <McpCard
              key={server.mcp_id}
              server={server}
              busy={busyId === server.mcp_id}
              onToggle={toggle}
              onInvoke={setInvokeTarget}
              onRevoke={setRevokeTarget}
              onUninstall={setUninstallTarget}
            />
          ))}
        </div>
      )}

      <McpInstallDialog
        open={installOpen}
        onClose={() => setInstallOpen(false)}
        onInstalled={() => {
          setNotice("MCP 安装成功。");
          void load();
        }}
      />
      <McpInvokeDialog
        server={invokeTarget}
        open={invokeTarget !== null}
        onClose={() => setInvokeTarget(null)}
        onSensitivePending={(item) => setConfirmation(item)}
        onInvoked={() => void load(true)}
      />
      <McpSensitiveConfirmDialog
        confirmation={confirmation}
        onClose={() => setConfirmation(null)}
        onResolve={(approved) => void resolveSensitive(approved)}
      />
      <McpRevokeDialog
        server={revokeTarget}
        onClose={() => setRevokeTarget(null)}
        onRevoked={() => {
          setRevokeTarget(null);
          setNotice("权限已更新。");
          void load();
        }}
      />
      {uninstallTarget ? (
        <Dialog open onClose={() => setUninstallTarget(null)} title="卸载 MCP">
          <p>
            卸载将停止运行中的进程并删除安装记录与权限清单（调用统计与审计记录按
            账户保留）。此操作不可撤销，确定卸载吗？
          </p>
          <div className={styles.dialogActions}>
            <Button variant="ghost" onClick={() => setUninstallTarget(null)}>
              取消
            </Button>
            <Button variant="danger" disabled={busyId === uninstallTarget.mcp_id} onClick={() => void confirmUninstall()}>
              确认卸载
            </Button>
          </div>
        </Dialog>
      ) : null}
    </section>
  );
}
