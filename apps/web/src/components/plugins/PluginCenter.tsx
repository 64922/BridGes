"use client";

/**
 * 插件中心（Issue 34）。
 *
 * - 内置插件：PDF / Documents / bridges-humanizer 随应用发布、版本固定、
 *   只读不可卸载；账户可启停。PDF 与 Documents 提供「演示」——对上传的
 *   受支持附件执行真实解析并展示统计与预览；humanizer 提供「在聊天中
 *   使用」——跳转新聊天并自动打开人味化对话框（既有真实生成合同）。
 * - 我的插件：用户上传声明式 zip 包，安装前完成安全闭锁检查与内容预览
 *   确认；可启停、卸载；安装失败进入可恢复失败态（原因 + 重新上传）。
 *
 * 页面覆盖加载/空/错误/权限/内容状态；全部操作带中文反馈；切换账户由
 * AppShell 的 accountRevision 重挂本组件清态。
 */

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { Dialog } from "@/components/bridges/Dialog";
import { StateBlock } from "@/components/bridges/StateBlock";
import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { Icon, type IconName } from "@/components/design-system/Icon";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { useAuth } from "@/context/AuthContext";
import {
  ApiError,
  checkPluginPackage,
  classifyApiError,
  demoBuiltinPlugin,
  disablePlugin,
  enablePlugin,
  installPluginPackage,
  listPlugins,
  uninstallPlugin,
  type BuiltinPluginProjection,
  type PluginCheckResult,
  type PluginDemoProjection,
  type PluginFileEntry,
  type PluginListProjection,
  type PluginStatus,
  type UserPluginProjection,
} from "@/lib/api";
import { pluginHumanizerKey } from "@/lib/chat-flow";

import styles from "./PluginCenter.module.css";

import { useApiQuery } from "@/lib/data";

// 与后端 checker.MAX_PLUGIN_BYTES 一致（语言边界必须复制，前端仅预检）。
const MAX_ZIP_BYTES = 5 * 1024 * 1024;

const BUILTIN_ICONS: Record<string, IconName> = {
  "bridges-pdf": "paperSearch",
  "bridges-documents": "knowledgeBase",
  "bridges-humanizer": "humanize",
};

const FILE_KIND_LABELS: Record<string, string> = {
  skill_md: "SKILL.md",
  reference: "参考",
  template: "模板",
  resource: "资源",
};

function StatusChip({
  tone,
  icon,
  label,
}: {
  tone: "success" | "error" | "neutral" | "active";
  icon: IconName;
  label: string;
}) {
  const palette: Record<string, { color: string; bg: string }> = {
    success: { color: "var(--color-status-success)", bg: "var(--color-status-success-bg)" },
    error: { color: "var(--color-status-error)", bg: "var(--color-status-error-bg)" },
    neutral: { color: "var(--color-status-unknown)", bg: "var(--color-status-unknown-bg)" },
    active: { color: "var(--color-accent-secondary)", bg: "var(--color-status-info-bg)" },
  };
  const config = palette[tone];
  return (
    <span className={styles.statusChip} style={{ color: config.color, backgroundColor: config.bg }}>
      <Icon name={icon} size={14} aria-hidden />
      <span>{label}</span>
    </span>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// ---------------------------------------------------------------------------
// 内置插件卡
// ---------------------------------------------------------------------------

function BuiltinPluginCard({
  plugin,
  busy,
  onToggle,
  onDemo,
  onUseInChat,
}: {
  plugin: BuiltinPluginProjection;
  busy: boolean;
  onToggle: (plugin: BuiltinPluginProjection) => void;
  onDemo: (plugin: BuiltinPluginProjection) => void;
  onUseInChat: (plugin: BuiltinPluginProjection) => void;
}) {
  return (
    <article className={`sc-card ${styles.pluginCard}`} data-testid={`builtin-${plugin.skill_id}`}>
      <div className={styles.cardHeader}>
        <span className={styles.cardIcon} aria-hidden="true">
          <Icon name={BUILTIN_ICONS[plugin.skill_id] ?? "plugins"} size={22} />
        </span>
        <div className={styles.cardTitleBlock}>
          <h3 className={styles.cardTitle}>{plugin.name}</h3>
          <div className={styles.chipRow}>
            <StatusChip tone="neutral" icon="check" label="内置" />
            <span className={styles.versionBadge} title={`固定版本 ${plugin.version}`}>
              v{plugin.version}
            </span>
            {plugin.enabled ? (
              <StatusChip tone="success" icon="check" label="已启用" />
            ) : (
              <StatusChip tone="neutral" icon="pause" label="已停用" />
            )}
          </div>
        </div>
      </div>
      <p className={styles.description}>{plugin.description}</p>
      <ul className={styles.capabilityList}>
        {(plugin.capabilities ?? []).map((capability) => (
          <li key={capability}>{capability}</li>
        ))}
      </ul>
      <dl className={styles.metaList}>
        <div>
          <dt>来源</dt>
          <dd>{plugin.source}</dd>
        </div>
        <div>
          <dt>授权</dt>
          <dd>{plugin.license}</dd>
        </div>
        <div>
          <dt>将接收的数据</dt>
          <dd>{(plugin.data_categories ?? []).join("；")}</dd>
        </div>
      </dl>
      <div className={styles.cardActions}>
        <Button
          variant={plugin.enabled ? "secondary" : "primary"}
          size="sm"
          onClick={() => onToggle(plugin)}
          isLoading={busy}
          aria-label={plugin.enabled ? `停用 ${plugin.name}` : `启用 ${plugin.name}`}
        >
          {plugin.enabled ? "停用" : "启用"}
        </Button>
        {plugin.demo_kind === "parse" ? (
          <Button variant="ghost" size="sm" onClick={() => onDemo(plugin)} disabled={busy}>
            演示
          </Button>
        ) : (
          <Button variant="ghost" size="sm" onClick={() => onUseInChat(plugin)} disabled={busy}>
            在聊天中使用
          </Button>
        )}
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------
// 用户插件卡
// ---------------------------------------------------------------------------

function UserPluginCard({
  plugin,
  busy,
  onToggle,
  onUninstall,
  onRetryUpload,
}: {
  plugin: UserPluginProjection;
  busy: boolean;
  onToggle: (plugin: UserPluginProjection) => void;
  onUninstall: (plugin: UserPluginProjection) => void;
  onRetryUpload: (plugin: UserPluginProjection) => void;
}) {
  const failed = plugin.status === "install_failed";
  return (
    <article className={`sc-card ${styles.pluginCard}`} data-testid={`user-${plugin.plugin_id}`}>
      <div className={styles.cardHeader}>
        <span className={styles.cardIcon} aria-hidden="true">
          <Icon name="plugins" size={22} />
        </span>
        <div className={styles.cardTitleBlock}>
          <h3 className={styles.cardTitle}>{plugin.name}</h3>
          <div className={styles.chipRow}>
            {!failed && (
              <span className={styles.versionBadge} title={`固定版本 ${plugin.version}`}>
                v{plugin.version}
              </span>
            )}
            {failed ? (
              <StatusChip tone="error" icon="alert" label="安装失败" />
            ) : plugin.enabled ? (
              <StatusChip tone="success" icon="check" label="已启用" />
            ) : (
              <StatusChip tone="neutral" icon="pause" label="已停用" />
            )}
          </div>
        </div>
      </div>
      {failed ? (
        <p className={styles.failureReason} role="alert">
          {plugin.failure_reason ?? "安装未通过安全检查，请修正包后重新上传。"}
        </p>
      ) : (
        <>
          {plugin.description && <p className={styles.description}>{plugin.description}</p>}
          {(plugin.capabilities?.length ?? 0) > 0 && (
            <ul className={styles.capabilityList}>
              {(plugin.capabilities ?? []).map((capability) => (
                <li key={capability}>{capability}</li>
              ))}
            </ul>
          )}
          {(plugin.data_categories?.length ?? 0) > 0 && (
            <dl className={styles.metaList}>
              <div>
                <dt>将接收的数据</dt>
                <dd>{(plugin.data_categories ?? []).join("；")}</dd>
              </div>
            </dl>
          )}
        </>
      )}
      <div className={styles.cardActions}>
        {failed ? (
          <Button variant="primary" size="sm" onClick={() => onRetryUpload(plugin)}>
            重新上传
          </Button>
        ) : (
          <Button
            variant={plugin.enabled ? "secondary" : "primary"}
            size="sm"
            onClick={() => onToggle(plugin)}
            isLoading={busy}
            aria-label={plugin.enabled ? `停用 ${plugin.name}` : `启用 ${plugin.name}`}
          >
            {plugin.enabled ? "停用" : "启用"}
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onUninstall(plugin)}
          disabled={failed}
          aria-label={`卸载 ${plugin.name}`}
        >
          卸载
        </Button>
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------
// 安装对话框：选文件 → 检查 → 预览确认 / 拒绝原因 → 安装 → 成功
// ---------------------------------------------------------------------------

type UploadStep = "pick" | "checking" | "preview" | "rejected" | "installing" | "done";

function UploadPluginDialog({
  open,
  onClose,
  onInstalled,
}: {
  open: boolean;
  onClose: () => void;
  onInstalled: (message: string) => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState<UploadStep>("pick");
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [checkResult, setCheckResult] = useState<PluginCheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setStep("pick");
    setFile(null);
    setProgress(null);
    setCheckResult(null);
    setError(null);
  }, [open]);

  const pickFile = (picked: File | null) => {
    setError(null);
    setCheckResult(null);
    setStep("pick");
    if (!picked) return;
    if (!picked.name.toLowerCase().endsWith(".zip")) {
      setError("请选择 .zip 格式的声明式 SKILL 包。");
      return;
    }
    if (picked.size > MAX_ZIP_BYTES) {
      setError("插件包超过 5 MB 大小限制，请压缩后重试。");
      return;
    }
    setFile(picked);
    void runCheck(picked);
  };

  const runCheck = async (picked: File) => {
    setStep("checking");
    setProgress(0);
    try {
      const result = await checkPluginPackage(picked, (loaded, total) => {
        setProgress(total > 0 ? Math.round((loaded / total) * 100) : 0);
      });
      setCheckResult(result);
      setStep(result.ok ? "preview" : "rejected");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "检查失败，请稍后重试。");
      setStep("pick");
    }
  };

  const confirmInstall = async () => {
    if (!file) return;
    setStep("installing");
    setError(null);
    try {
      await installPluginPackage(file);
      setStep("done");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "安装失败，请稍后重试。");
      setStep("preview");
    }
  };

  const rerun = () => {
    if (!file) {
      fileInputRef.current?.click();
      return;
    }
    setError(null);
    setCheckResult(null);
    void runCheck(file);
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="安装插件"
      description="只接受声明式 SKILL 包：SKILL.md、静态参考资料、模板与资源。"
    >
      <input
        ref={fileInputRef}
        type="file"
        accept=".zip"
        tabIndex={-1}
        style={{ display: "none" }}
        data-testid="plugin-file-input"
        onChange={(event) => pickFile(event.target.files?.[0] ?? null)}
      />
      {step === "pick" && (
        <div className={styles.uploadZone}>
          <p className={styles.hint}>
            包内不得包含脚本、可执行文件、符号链接或越界路径；安装前会完成安全检查与内容预览。
          </p>
          <Button variant="primary" onClick={() => fileInputRef.current?.click()}>
            选择 zip 包
          </Button>
          {error && <ErrorSummary errors={[error]} />}
        </div>
      )}
      {step === "checking" && (
        <div className={styles.uploadZone}>
          <LoadingStatus message={progress === null ? "正在检查插件包…" : `正在检查插件包… ${progress}%`} />
        </div>
      )}
      {step === "rejected" && checkResult && (
        <div className={styles.uploadZone} data-testid="plugin-check-rejected">
          <p className={styles.rejectTitle} role="alert">
            安装检查未通过，请修正后重新上传
          </p>
          <ErrorSummary errors={checkResult.rejected_reasons ?? []} />
          <div className={styles.dialogActions}>
            <Button variant="secondary" onClick={rerun}>
              重新检查
            </Button>
            <Button variant="ghost" onClick={onClose}>
              取消
            </Button>
          </div>
        </div>
      )}
      {step === "preview" && checkResult && (
        <div className={styles.previewBlock} data-testid="plugin-check-preview">
          <dl className={styles.metaList}>
            <div>
              <dt>包名</dt>
              <dd>{checkResult.name}</dd>
            </div>
            <div>
              <dt>固定版本</dt>
              <dd>{checkResult.version}</dd>
            </div>
            {checkResult.description && (
              <div>
                <dt>说明</dt>
                <dd>{checkResult.description}</dd>
              </div>
            )}
            {checkResult.source && (
              <div>
                <dt>来源</dt>
                <dd>{checkResult.source}</dd>
              </div>
            )}
            {(checkResult.capabilities?.length ?? 0) > 0 && (
              <div>
                <dt>声明能力</dt>
                <dd>{(checkResult.capabilities ?? []).join("；")}</dd>
              </div>
            )}
            <div>
              <dt>将接收的数据</dt>
              <dd>
                {(checkResult.data_categories?.length ?? 0) > 0
                  ? (checkResult.data_categories ?? []).join("；")
                  : "未声明（安装后不会主动读取任何数据）"}
              </dd>
            </div>
          </dl>
          <h4 className={styles.previewSubtitle}>内容清单（{checkResult.file_count} 个文件）</h4>
          <ul className={styles.fileList} data-testid="plugin-file-list">
            {(checkResult.files ?? []).map((entry: PluginFileEntry) => (
              <li key={entry.path}>
                <span className={styles.filePath}>{entry.path}</span>
                <span className={styles.fileKind}>
                  {FILE_KIND_LABELS[entry.kind] ?? entry.kind}
                </span>
                <span className={styles.fileSize}>{formatBytes(entry.size)}</span>
              </li>
            ))}
          </ul>
          <div className={styles.dialogActions}>
            <Button
              variant="primary"
              onClick={() => void confirmInstall()}
              data-testid="plugin-confirm-install"
            >
              确认安装
            </Button>
            <Button variant="ghost" onClick={onClose}>
              取消
            </Button>
          </div>
          {error && <ErrorSummary errors={[error]} />}
        </div>
      )}
      {step === "installing" && (
        <div className={styles.uploadZone}>
          <LoadingStatus message="正在安装…" />
        </div>
      )}
      {step === "done" && (
        <div className={styles.uploadZone} data-testid="plugin-install-done" role="status">
          <span className={styles.successIcon}>
            <Icon name="check" size={28} aria-hidden />
          </span>
          <p>插件安装成功，已按当前账户启用。</p>
          <Button
            variant="primary"
            onClick={() => {
              onInstalled("插件安装成功，已按当前账户启用。");
              onClose();
            }}
          >
            完成
          </Button>
        </div>
      )}
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 内置能力演示对话框（真实解析）
// ---------------------------------------------------------------------------

function DemoDialog({
  plugin,
  onClose,
}: {
  plugin: BuiltinPluginProjection | null;
  onClose: () => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [phase, setPhase] = useState<"pick" | "running" | "result" | "error">("pick");
  const [result, setResult] = useState<PluginDemoProjection | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!plugin) return;
    setPhase("pick");
    setResult(null);
    setError(null);
  }, [plugin]);

  const run = async (file: File | null) => {
    if (!plugin || !file) return;
    setPhase("running");
    setError(null);
    try {
      setResult(await demoBuiltinPlugin(plugin.skill_id, file));
      setPhase("result");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "解析失败，请稍后重试。");
      setPhase("error");
    }
  };

  return (
    <Dialog
      open={plugin !== null}
      onClose={onClose}
      title={plugin ? `演示：${plugin.name}` : "演示"}
      description={
        plugin
          ? "上传一个受支持的附件，本机执行真实解析并展示统计与文本预览。"
          : undefined
      }
    >
      <input
        ref={fileInputRef}
        type="file"
        accept=".pdf,.docx,.txt,.md,.png,.jpg,.jpeg,.gif,.webp"
        tabIndex={-1}
        style={{ display: "none" }}
        data-testid="plugin-demo-file-input"
        onChange={(event) => void run(event.target.files?.[0] ?? null)}
      />
      {phase === "pick" && (
        <div className={styles.uploadZone}>
          <p className={styles.hint}>
            支持 PDF、DOCX、TXT、Markdown 与常见图片；解析只在本机进行，不上传第三方。
          </p>
          <Button variant="primary" onClick={() => fileInputRef.current?.click()}>
            选择附件
          </Button>
        </div>
      )}
      {phase === "running" && (
        <div className={styles.uploadZone}>
          <LoadingStatus message="正在真实解析附件…" />
        </div>
      )}
      {phase === "result" && result && (
        <div className={styles.demoResult} data-testid="plugin-demo-result" role="status">
          <p className={styles.demoFilename}>{result.filename}</p>
          <dl className={styles.metaList}>
            <div>
              <dt>解析器</dt>
              <dd>{result.parser_version}</dd>
            </div>
            <div>
              <dt>页数</dt>
              <dd>{result.pages}</dd>
            </div>
            <div>
              <dt>章节</dt>
              <dd>{result.sections}</dd>
            </div>
            <div>
              <dt>字符数</dt>
              <dd>{result.char_count}</dd>
            </div>
          </dl>
          <p className={styles.demoPreview}>{result.preview || "（无可提取文本）"}</p>
          <Button variant="secondary" onClick={() => fileInputRef.current?.click()}>
            换一个附件
          </Button>
        </div>
      )}
      {phase === "error" && (
        <div className={styles.uploadZone}>
          {error && <ErrorSummary errors={[error]} />}
          <div className={styles.dialogActions}>
            <Button variant="secondary" onClick={() => fileInputRef.current?.click()}>
              重新选择
            </Button>
            <Button variant="ghost" onClick={onClose}>
              关闭
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 页面主组件
// ---------------------------------------------------------------------------

export function PluginCenter() {
  const router = useRouter();
  const { refreshSession } = useAuth();
  const [permissionDenied, setPermissionDenied] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [demoPlugin, setDemoPlugin] = useState<BuiltinPluginProjection | null>(null);
  const [uninstallTarget, setUninstallTarget] = useState<UserPluginProjection | null>(null);
  const [busyPluginId, setBusyPluginId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // 列表加载：loading/error/reload 由 useApiQuery 统一管理。
  const { data: list, error, loading, reload } = useApiQuery("plugins", () => listPlugins());

  // 加载错误分类：reauth 直接显示权限页；session 过期先刷新会话再重载。
  useEffect(() => {
    if (!error) return;
    const kind = classifyApiError(error);
    if (kind === "reauth") {
      setPermissionDenied(true);
    } else if (kind === "session") {
      void refreshSession().then(() => reload());
    }
  }, [error, refreshSession, reload]);

  const guard = (cause: unknown): boolean => {
    const kind = classifyApiError(cause);
    if (kind === "reauth") {
      setPermissionDenied(true);
      return true;
    }
    if (kind === "session") {
      void refreshSession().then(() => reload());
      return true;
    }
    return false;
  };

  const toggleUser = async (plugin: UserPluginProjection) => {
    setBusyPluginId(plugin.plugin_id);
    setNotice(null);
    try {
      await (plugin.enabled ? disablePlugin(plugin.plugin_id) : enablePlugin(plugin.plugin_id));
      reload();
    } catch (cause) {
      if (!guard(cause)) {
        setNotice("操作失败，请稍后重试。");
      }
    } finally {
      setBusyPluginId(null);
    }
  };

  const toggleBuiltin = async (plugin: BuiltinPluginProjection) => {
    setBusyPluginId(plugin.skill_id);
    setNotice(null);
    try {
      await (
        plugin.enabled
          ? disablePlugin(plugin.skill_id)
          : enablePlugin(plugin.skill_id)
      );
      reload();
      setNotice(plugin.enabled ? `已停用 ${plugin.name}` : `已启用 ${plugin.name}`);
    } catch (cause) {
      if (!guard(cause)) {
        setNotice("操作失败，请稍后重试。");
      }
    } finally {
      setBusyPluginId(null);
    }
  };

  const confirmUninstall = async () => {
    if (!uninstallTarget) return;
    setBusyPluginId(uninstallTarget.plugin_id);
    setNotice(null);
    try {
      await uninstallPlugin(uninstallTarget.plugin_id);
      setUninstallTarget(null);
      reload();
      setNotice(`已卸载 ${uninstallTarget.name}`);
    } catch (cause) {
      if (!guard(cause)) {
        setNotice("卸载失败，请稍后重试。");
      }
    } finally {
      setBusyPluginId(null);
    }
  };

  const useHumanizerInChat = () => {
    sessionStorage.setItem(pluginHumanizerKey(), "1");
    router.push("/");
  };

  if (loading) {
    return <LoadingStatus message="插件中心加载中…" />;
  }
  if (permissionDenied) {
    return (
      <StateBlock
        kind="permission"
        title="没有访问插件的权限"
        description="会话需要重新验证，请重新登录当前账户后再访问插件中心。"
      />
    );
  }
  if (error) {
    return (
      <StateBlock
        kind="error"
        title="插件中心加载失败"
        description={error.message ?? "请稍后重试。"}
        actionLabel="重试"
        onAction={reload}
      />
    );
  }

  const builtin = list?.builtin ?? [];
  const user = list?.user ?? [];
  const failed = user.filter((plugin) => plugin.status === "install_failed");
  const installed = user.filter((plugin) => plugin.status !== "install_failed");

  return (
    <div className={styles.center}>
      <div className={styles.topRow}>
        <div>
          <h2 className="sc-section-title">插件中心</h2>
          <p className={styles.hint}>
            内置插件随应用发布；用户插件只接受声明式 SKILL 包，安装前完成安全检查。
          </p>
        </div>
        <Button variant="primary" onClick={() => setUploadOpen(true)}>
          安装插件
        </Button>
      </div>

      {notice && (
        <p className={styles.notice} role="status">
          {notice}
        </p>
      )}

      <section aria-labelledby="builtin-title">
        <h3 id="builtin-title" className={styles.sectionTitle}>
          内置插件
        </h3>
        <div className={styles.builtinGrid}>
          {builtin.map((plugin) => (
            <BuiltinPluginCard
              key={plugin.skill_id}
              plugin={plugin}
              busy={busyPluginId === plugin.skill_id}
              onToggle={() => void toggleBuiltin(plugin)}
              onDemo={setDemoPlugin}
              onUseInChat={useHumanizerInChat}
            />
          ))}
        </div>
      </section>

      <section aria-labelledby="user-title">
        <h3 id="user-title" className={styles.sectionTitle}>
          我的插件
        </h3>
        {installed.length === 0 && failed.length === 0 ? (
          <StateBlock
            kind="empty"
            title="当前账户还没有用户插件"
            description="上传声明式 SKILL 包（SKILL.md、静态参考、模板与资源），安装前会展示内容清单与安全结果。"
            actionLabel="安装插件"
            onAction={() => setUploadOpen(true)}
          />
        ) : (
          <div className={styles.userList}>
            {[...installed, ...failed].map((plugin) => (
              <UserPluginCard
                key={plugin.package_id}
                plugin={plugin}
                busy={busyPluginId === plugin.plugin_id}
                onToggle={() => void toggleUser(plugin)}
                onUninstall={setUninstallTarget}
                onRetryUpload={() => setUploadOpen(true)}
              />
            ))}
          </div>
        )}
      </section>

      <UploadPluginDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onInstalled={(message) => {
          setNotice(message);
          reload();
        }}
      />
      <DemoDialog plugin={demoPlugin} onClose={() => setDemoPlugin(null)} />
      <Dialog
        open={uninstallTarget !== null}
        onClose={() => setUninstallTarget(null)}
        title="卸载插件"
        description="卸载后该插件从当前账户移除，包内容一并清理，此操作不可撤销。"
      >
        <div className={styles.uploadZone}>
          <p className={styles.hint}>
            确定要卸载「{uninstallTarget?.name ?? ""}」（v{uninstallTarget?.version ?? ""}）吗？
          </p>
          <div className={styles.dialogActions}>
            <Button
              variant="danger"
              onClick={() => void confirmUninstall()}
              isLoading={busyPluginId === uninstallTarget?.plugin_id}
            >
              确认卸载
            </Button>
            <Button variant="ghost" onClick={() => setUninstallTarget(null)}>
              取消
            </Button>
          </div>
          {notice && <p className={styles.notice} role="status">{notice}</p>}
        </div>
      </Dialog>
    </div>
  );
}
