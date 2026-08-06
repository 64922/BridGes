"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  ApiError,
  classifyApiError,
  createBackup,
  deleteAccount,
  exportAccountData,
  fetchDeletionStatus,
  fetchExportPreview,
  reauthenticate,
  restoreBackup,
  retryDeletion,
  type AccountDeletionProjection,
  type ExportPreviewProjection,
  type RestorePreview,
} from "@/lib/api";
import { formatSize } from "@/lib/format";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { Icon } from "@/components/design-system/Icon";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { Dialog } from "@/components/bridges/Dialog";
import { PasswordField } from "@/components/bridges/PasswordField";
import { StateBlock } from "@/components/bridges/StateBlock";

import styles from "./DataPrivacy.module.css";

/**
 * 数据与隐私页（Issue 37）：导出、删除、备份与恢复四分区。
 * 全部敏感操作（导出/删除/备份/恢复）在对话框内先输入当前账户密码完成
 * 近期再认证，再执行操作；失败原因以中文内联呈现，绝不显示假成功。
 */
export function DataPrivacy() {
  const { user, refreshSession } = useAuth();
  const router = useRouter();

  // 导出预览（确认前可见范围与预计大小）
  const [preview, setPreview] = useState<ExportPreviewProjection | null>(null);
  const [previewState, setPreviewState] = useState<
    "loading" | "ready" | "error"
  >("loading");
  const [previewError, setPreviewError] = useState<string | null>(null);
  const previewStarted = useRef(false);

  // 删除状态（失败可重试提示）
  const [deletion, setDeletion] = useState<AccountDeletionProjection | null>(null);

  // 对话框开关
  const [exportOpen, setExportOpen] = useState(false);
  const [backupOpen, setBackupOpen] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [retryDeleteOpen, setRetryDeleteOpen] = useState(false);

  // 删除/恢复成功后的跳转标记（避免重复跳转）
  const [departed, setDeparted] = useState(false);

  const loadPreview = useCallback(async () => {
    setPreviewState("loading");
    setPreviewError(null);
    try {
      setPreview(await fetchExportPreview());
      setPreviewState("ready");
    } catch (cause) {
      const kind = classifyApiError(cause);
      if (kind === "session") {
        await refreshSession();
        return;
      }
      setPreviewError(
        cause instanceof Error ? cause.message : "导出范围读取失败，请稍后重试。"
      );
      setPreviewState("error");
    }
  }, [refreshSession]);

  useEffect(() => {
    if (previewStarted.current) return;
    previewStarted.current = true;
    void loadPreview();
  }, [loadPreview]);

  const loadDeletionStatus = useCallback(async () => {
    try {
      setDeletion(await fetchDeletionStatus());
    } catch {
      setDeletion(null); // 无删除记录（404）视为正常
    }
  }, []);

  useEffect(() => {
    void loadDeletionStatus();
  }, [loadDeletionStatus]);

  const goToLogin = useCallback(
    (from: string) => {
      if (departed) return;
      setDeparted(true);
      window.location.replace(`/login?from=${from}`);
    },
    [departed]
  );

  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <header>
          <p className={styles.eyebrow}>账户与隐私</p>
          <h1 id="data-privacy-title" className={styles.title}>
            数据与隐私
          </h1>
          <p className={styles.lead}>
            导出、删除、备份与恢复你的本地数据。导出只包含当前账户的内容，
            备份是整套本地数据的加密一致快照；百炼 Key、QQ SMTP 授权码、
            会话令牌与运行密钥永不进入导出或备份。
          </p>
        </header>

        <div className={styles.stack}>
          {/* ------------------------------------------------------------------
              导出数据
              ------------------------------------------------------------------ */}
          <section className={styles.card} aria-labelledby="export-card-title">
            <div className={styles.cardHeader}>
              <div>
                <h2 id="export-card-title" className={styles.cardTitle}>
                  导出数据
                </h2>
                <p className={styles.cardDescription}>
                  导出当前账户的对话、消息、画像及版本、学习项目、提醒与投递记录、
                  插件清单、授权记录和资产清单，生成可阅读且可机器处理的 JSON 文件。
                </p>
              </div>
              <Icon name="download" size={28} aria-hidden />
            </div>

            {previewState === "loading" && (
              <StateBlock kind="loading" title="正在计算导出范围…" />
            )}
            {previewState === "error" && (
              <StateBlock
                kind="error"
                title="导出范围读取失败"
                description={previewError ?? undefined}
                actionLabel="重试"
                onAction={() => void loadPreview()}
              />
            )}
            {previewState === "ready" && preview && (
              <>
                <table className={styles.rangeTable}>
                  <caption className={styles.rangeCaption}>
                    导出范围与预计大小（确认前可见，不含任何秘密）
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">数据类别</th>
                      <th scope="col" className={styles.numCell}>
                        条数
                      </th>
                      <th scope="col" className={styles.numCell}>
                        预计大小
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.categories?.map((item) => (
                      <tr key={item.category}>
                        <th scope="row">{item.label}</th>
                        <td className={styles.numCell}>{item.item_count}</td>
                        <td className={styles.numCell}>
                          {formatSize(item.estimated_bytes)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <th scope="row">总计</th>
                      <td className={styles.numCell}>{preview.total_items}</td>
                      <td className={styles.numCell}>
                        {formatSize(preview.total_estimated_bytes)}
                      </td>
                    </tr>
                  </tfoot>
                </table>
                <p className={styles.secretsNote}>
                  导出不包含百炼 Key、QQ SMTP 授权码、会话令牌、运行密钥或
                  其他账户的数据；资产只含清单元数据，不含文件字节。
                </p>
                <div className={styles.actions}>
                  <Button
                    variant="primary"
                    onClick={() => setExportOpen(true)}
                    aria-label="打开导出确认对话框"
                  >
                    导出数据
                  </Button>
                </div>
              </>
            )}
          </section>

          {/* ------------------------------------------------------------------
              备份与恢复
              ------------------------------------------------------------------ */}
          <section className={styles.card} aria-labelledby="backup-card-title">
            <div className={styles.cardHeader}>
              <div>
                <h2 id="backup-card-title" className={styles.cardTitle}>
                  备份与恢复
                </h2>
                <p className={styles.cardDescription}>
                  创建整套本地 BridGes 数据的加密一致备份，或在必要时恢复
                  到备份时刻。备份不包含凭据与会话，恢复后外部凭据（百炼
                  Key、QQ SMTP 授权码）需重新配置。
                </p>
              </div>
              <Icon name="settings" size={28} aria-hidden />
            </div>

            {deletion && deletion.status === "failed" && (
              <div className={styles.deletionNotice} role="alert">
                <p>
                  上次账户删除未完成（{deletion.last_error ?? "未知原因"}）。
                </p>
                <Button
                  variant="secondary"
                  onClick={() => setRetryDeleteOpen(true)}
                  aria-label="打开重试删除对话框"
                >
                  重试删除
                </Button>
              </div>
            )}

            <div className={styles.splitRow}>
              <div className={styles.splitCell}>
                <h3 className={styles.subTitle}>创建备份</h3>
                <p className={styles.subDescription}>
                  设置备份口令（恢复时需同一口令）。备份包含数据库一致快照、
                  账户隔离对象与身份账户数据，经口令派生密钥加密保护。
                </p>
                <div className={styles.actions}>
                  <Button
                    variant="primary"
                    onClick={() => setBackupOpen(true)}
                    aria-label="打开创建备份对话框"
                  >
                    创建备份
                  </Button>
                </div>
              </div>
              <div className={styles.splitCell}>
                <h3 className={styles.subTitle}>恢复备份</h3>
                <p className={styles.subDescription}>
                  选择 .bridgesbackup 备份文件并输入口令。恢复会替换当前
                  全部本地数据，操作前需键入「恢复」确认。
                </p>
                <div className={styles.actions}>
                  <Button
                    variant="secondary"
                    onClick={() => setRestoreOpen(true)}
                    aria-label="打开恢复备份对话框"
                  >
                    恢复备份
                  </Button>
                </div>
              </div>
            </div>
          </section>

          {/* ------------------------------------------------------------------
              危险区：删除账户
              ------------------------------------------------------------------ */}
          <section
            className={`${styles.card} ${styles.dangerCard}`}
            aria-labelledby="delete-card-title"
          >
            <div className={styles.cardHeader}>
              <div>
                <h2 id="delete-card-title" className={styles.cardTitle}>
                  删除账户
                </h2>
                <p className={styles.cardDescription}>
                  删除当前账户及其全部本地数据，此操作不可撤销。删除前请
                  先导出数据或创建备份。
                </p>
              </div>
              <Icon name="trash" size={28} aria-hidden />
            </div>
            <ul className={styles.dangerList}>
              <li>撤销全部会话并停止进行中的回答与后台任务；</li>
              <li>一致删除对话、画像、项目、提醒、插件授权与资产记录；</li>
              <li>删除本地对象文件、索引、缓存与待执行提醒；</li>
              <li>清除百炼 Key 与 QQ SMTP 授权码（外部服务凭据一并移除）。</li>
            </ul>
            <div className={styles.actions}>
              <Button
                variant="danger"
                onClick={() => setDeleteOpen(true)}
                aria-label="打开删除账户确认对话框"
              >
                删除账户
              </Button>
            </div>
          </section>
        </div>
      </div>

      {/* ------------------------------------------------------------------------
          导出确认对话框（近期密码再认证 → 下载 JSON）
          ------------------------------------------------------------------------ */}
      <SensitiveDialog
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        title="导出数据"
        description={`将导出 ${formatSize(preview?.total_estimated_bytes ?? 0)} 的账户数据 JSON 文件（${preview?.total_items ?? 0} 条记录）。输入当前账户密码确认后开始导出。`}
        busyLabel="导出中…"
        confirmLabel="导出"
        onConfirm={async (password) => {
          await reauthenticate(password);
          await exportAccountData();
        }}
        onError={(cause) => (cause instanceof Error ? cause.message : "导出失败，请稍后重试。")}
      />

      {/* ------------------------------------------------------------------------
          创建备份对话框（口令两次输入 → 下载加密备份）
          ------------------------------------------------------------------------ */}
      <BackupDialog
        open={backupOpen}
        onClose={() => setBackupOpen(false)}
        onSuccess={() => router.refresh()}
      />

      {/* ------------------------------------------------------------------------
          恢复备份对话框（文件 + 口令 + 键入「恢复」→ 预检执行）
          ------------------------------------------------------------------------ */}
      <RestoreDialog
        open={restoreOpen}
        onClose={() => setRestoreOpen(false)}
        onSessionExpired={() => goToLogin("restored")}
      />

      {/* ------------------------------------------------------------------------
          删除账户对话框（密码 + 键入「删除」→ 删除并跳登录）
          ------------------------------------------------------------------------ */}
      <DeleteAccountDialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        username={user?.username}
        onDeleted={() => goToLogin("account-deleted")}
        onSessionExpired={() => goToLogin("logout")}
      />

      {/* ------------------------------------------------------------------------
          重试删除对话框（近期密码再认证 → 重试失败的清理）
          ------------------------------------------------------------------------ */}
      <SensitiveDialog
        open={retryDeleteOpen}
        onClose={() => setRetryDeleteOpen(false)}
        title="重试删除账户"
        description="上次删除未完成。重试将补全对象与凭据清理并移除账户身份；完成后会话失效并跳转登录。"
        busyLabel="删除中…"
        confirmLabel="重试删除"
        onConfirm={async () => {
          await retryDeletion();
          goToLogin("account-deleted");
        }}
        onError={(cause) =>
          cause instanceof Error ? cause.message : "重试失败，请稍后重试。"
        }
      />
    </div>
  );
}

/* ----------------------------------------------------------------------------
 * 敏感操作对话框：密码（近期再认证）+ 操作确认后执行
 * -------------------------------------------------------------------------- */

interface SensitiveDialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description: string;
  busyLabel: string;
  confirmLabel: string;
  /** 执行操作：先已通过 reauthenticate(password)，抛错则内联呈现。 */
  onConfirm: (password: string) => Promise<void>;
  onError: (cause: unknown) => string;
}

function SensitiveDialog({
  open,
  onClose,
  title,
  description,
  busyLabel,
  confirmLabel,
  onConfirm,
  onError,
}: SensitiveDialogProps) {
  const [password, setPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | undefined>();
  const [actionError, setActionError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const reset = () => {
    setPassword("");
    setPasswordError(undefined);
    setActionError(null);
    setSubmitting(false);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!password) {
      setPasswordError("请输入当前账户密码。");
      return;
    }
    setSubmitting(true);
    setPasswordError(undefined);
    setActionError(null);
    try {
      await reauthenticate(password);
      await onConfirm(password);
      reset();
      onClose();
    } catch (cause) {
      if (
        cause instanceof ApiError &&
        cause.status === 401 &&
        cause.code === "reauthentication_failed"
      ) {
        setPasswordError(cause.message);
      } else {
        setActionError(onError(cause));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title={title} description={description}>
      <form onSubmit={(event) => void submit(event)} className={styles.dialogForm}>
        {actionError && <ErrorSummary title="操作失败" errors={[actionError]} />}
        <PasswordField
          id="data-privacy-password"
          label="当前账户密码"
          value={password}
          onChange={(value) => {
            setPassword(value);
            setPasswordError(undefined);
          }}
          error={passwordError}
          hint="用于近期密码确认，密码不会被保存。"
          required
          autoComplete="current-password"
        />
        <div className={styles.dialogActions}>
          {submitting && <LoadingStatus message={busyLabel} />}
          <Button type="submit" variant="primary" disabled={submitting}>
            {confirmLabel}
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={onClose}
            disabled={submitting}
          >
            取消
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/* ----------------------------------------------------------------------------
 * 创建备份对话框：口令两次输入（一致性校验）
 * -------------------------------------------------------------------------- */

interface BackupDialogProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

function BackupDialog({ open, onClose, onSuccess }: BackupDialogProps) {
  const [passphrase, setPassphrase] = useState("");
  const [confirmPassphrase, setConfirmPassphrase] = useState("");
  const [password, setPassword] = useState("");
  const [matchError, setMatchError] = useState<string | undefined>();
  const [passwordError, setPasswordError] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const reset = () => {
    setPassphrase("");
    setConfirmPassphrase("");
    setPassword("");
    setMatchError(undefined);
    setPasswordError(undefined);
    setError(null);
    setSubmitting(false);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (passphrase.length < 8) {
      setMatchError("备份口令至少需要 8 个字符。");
      return;
    }
    if (passphrase !== confirmPassphrase) {
      setMatchError("两次输入的口令不一致。");
      return;
    }
    if (!password) {
      setPasswordError("请输入当前账户密码。");
      return;
    }
    setSubmitting(true);
    setMatchError(undefined);
    setPasswordError(undefined);
    setError(null);
    try {
      await reauthenticate(password);
      await createBackup(passphrase);
      reset();
      onSuccess();
      onClose();
    } catch (cause) {
      if (
        cause instanceof ApiError &&
        cause.status === 401 &&
        cause.code === "reauthentication_failed"
      ) {
        setPasswordError(cause.message);
      } else {
        setError(
          cause instanceof Error ? cause.message : "备份创建失败，请稍后重试。"
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="创建本地备份"
      description="备份包含数据库一致快照、账户隔离对象与身份账户数据，经口令派生密钥加密；不包含百炼 Key、SMTP 授权码、会话令牌或运行密钥。"
    >
      <form onSubmit={(event) => void submit(event)} className={styles.dialogForm}>
        {error && <ErrorSummary title="备份创建失败" errors={[error]} />}
        <PasswordField
          id="backup-passphrase"
          label="备份口令"
          value={passphrase}
          onChange={(value) => {
            setPassphrase(value);
            setMatchError(undefined);
          }}
          error={matchError}
          hint="至少 8 个字符；恢复备份时需要使用同一口令。"
          required
          autoComplete="new-password"
        />
        <PasswordField
          id="backup-passphrase-confirm"
          label="再次输入备份口令"
          value={confirmPassphrase}
          onChange={(value) => {
            setConfirmPassphrase(value);
            setMatchError(undefined);
          }}
          error={matchError}
          required
          autoComplete="new-password"
        />
        <PasswordField
          id="backup-account-password"
          label="当前账户密码"
          value={password}
          onChange={(value) => {
            setPassword(value);
            setPasswordError(undefined);
          }}
          error={passwordError}
          hint="用于近期密码确认，密码不会被保存。"
          required
          autoComplete="current-password"
        />
        <div className={styles.dialogActions}>
          {submitting && <LoadingStatus message="创建中…" />}
          <Button type="submit" variant="primary" disabled={submitting}>
            创建备份
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={onClose}
            disabled={submitting}
          >
            取消
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/* ----------------------------------------------------------------------------
 * 恢复备份对话框：文件 + 口令 + 键入「恢复」
 * -------------------------------------------------------------------------- */

interface RestoreDialogProps {
  open: boolean;
  onClose: () => void;
  onSessionExpired: () => void;
}

function RestoreDialog({ open, onClose, onSessionExpired }: RestoreDialogProps) {
  const [file, setFile] = useState<File | null>(null);
  const [passphrase, setPassphrase] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reset = () => {
    setFile(null);
    setPassphrase("");
    setPassword("");
    setConfirmation("");
    setError(null);
    setReasons([]);
    setSubmitting(false);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) {
      setError("请先选择 .bridgesbackup 备份文件。");
      return;
    }
    if (!passphrase) {
      setError("请输入备份口令。");
      return;
    }
    if (!password) {
      setError("请输入当前账户密码。");
      return;
    }
    if (confirmation !== "恢复") {
      setError("请键入「恢复」以确认操作。");
      return;
    }
    setSubmitting(true);
    setError(null);
    setReasons([]);
    try {
      await reauthenticate(password);
      const preview = await restoreBackup(file, passphrase, confirmation);
      if (preview.ok) {
        reset();
        onSessionExpired(); // 身份已替换、会话失效：跳转重新登录
        return;
      }
      setReasons(preview.reasons ?? []);
      setError("恢复预检未通过，现有数据未受影响。");
    } catch (cause) {
      if (
        cause instanceof ApiError &&
        cause.status === 401 &&
        cause.code === "reauthentication_failed"
      ) {
        setError(cause.message);
      } else {
        setError(
          cause instanceof Error ? cause.message : "恢复失败，请稍后重试。"
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="恢复备份"
      description="恢复会替换当前全部本地数据并清除外部凭据，恢复完成后需要重新登录。损坏、篡改或不兼容的备份会被拒绝且不会破坏现有数据。"
    >
      <form onSubmit={(event) => void submit(event)} className={styles.dialogForm}>
        {error && <ErrorSummary title="无法恢复" errors={[error]} />}
        {reasons.length > 0 && (
          <ErrorSummary title="恢复预检未通过" errors={reasons} />
        )}
        <div className={styles.fileRow}>
          <input
            ref={fileInputRef}
            type="file"
            accept=".bridgesbackup"
            data-testid="restore-file-input"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setError(null);
              setReasons([]);
            }}
            className={styles.fileInput}
            aria-label="选择备份文件"
          />
          <Button
            type="button"
            variant="secondary"
            onClick={() => fileInputRef.current?.click()}
            aria-label="选择备份文件"
          >
            {file ? file.name : "选择备份文件"}
          </Button>
        </div>
        <PasswordField
          id="restore-passphrase"
          label="备份口令"
          value={passphrase}
          onChange={setPassphrase}
          hint="创建备份时设置的口令。"
          required
          autoComplete="current-password"
        />
        <PasswordField
          id="restore-account-password"
          label="当前账户密码"
          value={password}
          onChange={setPassword}
          hint="用于近期密码确认，密码不会被保存。"
          required
          autoComplete="current-password"
        />
        <label className={styles.confirmField}>
          <span>请输入「恢复」以确认替换当前全部本地数据</span>
          <input
            type="text"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            className={styles.textInput}
            data-testid="restore-confirmation"
            autoComplete="off"
          />
        </label>
        <div className={styles.dialogActions}>
          {submitting && <LoadingStatus message="恢复中…" />}
          <Button type="submit" variant="danger" disabled={submitting}>
            恢复
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={onClose}
            disabled={submitting}
          >
            取消
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/* ----------------------------------------------------------------------------
 * 删除账户对话框：密码 + 键入「删除」
 * -------------------------------------------------------------------------- */

interface DeleteAccountDialogProps {
  open: boolean;
  onClose: () => void;
  username?: string;
  onDeleted: () => void;
  onSessionExpired: () => void;
}

function DeleteAccountDialog({
  open,
  onClose,
  username,
  onDeleted,
  onSessionExpired,
}: DeleteAccountDialogProps) {
  const [password, setPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | undefined>();
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const reset = () => {
    setPassword("");
    setPasswordError(undefined);
    setConfirmation("");
    setError(null);
    setSubmitting(false);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!password) {
      setPasswordError("请输入当前账户密码。");
      return;
    }
    if (confirmation !== "删除") {
      setError("请键入「删除」以确认删除账户。");
      return;
    }
    setSubmitting(true);
    setPasswordError(undefined);
    setError(null);
    try {
      await reauthenticate(password);
      await deleteAccount(confirmation);
      reset();
      onDeleted();
    } catch (cause) {
      if (
        cause instanceof ApiError &&
        cause.status === 401 &&
        cause.code === "reauthentication_failed"
      ) {
        setPasswordError(cause.message);
      } else {
        const kind = classifyApiError(cause);
        if (kind === "session") {
          onSessionExpired();
          return;
        }
        setError(
          cause instanceof Error ? cause.message : "删除失败，请稍后重试。"
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="删除账户"
      description={`此操作不可撤销：将删除账户${username ? ` ${username} ` : " "}的对话、画像、项目、提醒、插件授权、资产与本地文件，并清除外部凭据。删除前请先导出数据或创建备份。`}
    >
      <form onSubmit={(event) => void submit(event)} className={styles.dialogForm}>
        {error && <ErrorSummary title="无法删除账户" errors={[error]} />}
        <PasswordField
          id="delete-account-password"
          label="当前账户密码"
          value={password}
          onChange={(value) => {
            setPassword(value);
            setPasswordError(undefined);
          }}
          error={passwordError}
          hint="用于近期密码确认，密码不会被保存。"
          required
          autoComplete="current-password"
        />
        <label className={styles.confirmField}>
          <span>请输入「删除」以确认删除账户及其全部本地数据</span>
          <input
            type="text"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            className={styles.textInput}
            data-testid="delete-confirmation"
            autoComplete="off"
          />
        </label>
        <div className={styles.dialogActions}>
          {submitting && <LoadingStatus message="删除中…" />}
          <Button type="submit" variant="danger" disabled={submitting}>
            删除账户
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={onClose}
            disabled={submitting}
          >
            取消
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
