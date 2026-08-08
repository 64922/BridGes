"use client";

/**
 * 任务安排：QQ SMTP 邮件提醒（Issue 33）。
 *
 * - SMTP 授权码配置卡：保存后触发自发自收验证，状态芯片呈现
 *   未配置/验证中/已验证/验证失败（含原因与重新验证路径）；
 *   收件人与发件人固定为当前账户 QQ 邮箱，不可修改。
 * - 时区设置：解析与展示使用的 IANA 时区。
 * - 新建/编辑提醒：自然语言 → 带时区结构化日程 + 简练邮件预览，
 *   用户确认后才持久化；画像适配可关闭并查看本次使用类别。
 * - 提醒列表：暂停/恢复/编辑/取消/手动补发 + 可展开投递记录
 *   （发送/失败/跳过/补发/手动重试）。
 *
 * 全部操作带中文加载/错误/空态；敏感操作（授权码保存/删除）走
 * 近期密码确认门；切换账户由 accountRevision 重挂本组件。
 */

import { useEffect, useRef, useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { PasswordField } from "@/components/bridges/PasswordField";
import { StateBlock } from "@/components/bridges/StateBlock";
import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { Icon } from "@/components/design-system/Icon";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { useAuth } from "@/context/AuthContext";
import {
  ApiError,
  cancelReminder,
  classifyApiError,
  createReminder,
  deleteSmtpCode,
  fetchReminderSettings,
  fetchSmtpSettings,
  listReminderDeliveries,
  listReminders,
  parseReminder,
  pauseReminder,
  reauthenticate,
  resumeReminder,
  saveSmtpCode,
  sendReminderNow,
  updateReminder,
  updateReminderSettings,
  verifySmtpNow,
  type ParsedReminderPreview,
  type ReminderDeliveryProjection,
  type ReminderProjection,
  type ReminderRepeatRule,
  type SmtpSettingsProjection,
} from "@/lib/api";
import { useApiQuery } from "@/lib/data";

import styles from "./TaskSchedule.module.css";


// ---------------------------------------------------------------------------
// 状态与标签（永不只靠颜色表达状态：图标 + 文字）
// ---------------------------------------------------------------------------

const SMTP_STATUS_META: Record<
  string,
  { label: string; icon: "check" | "alert" | "info" | "retry"; tone: "success" | "error" | "neutral" | "active" }
> = {
  unconfigured: { label: "未配置", icon: "info", tone: "neutral" },
  verifying: { label: "验证中", icon: "info", tone: "active" },
  verified: { label: "已验证", icon: "check", tone: "success" },
  failed: { label: "验证失败", icon: "alert", tone: "error" },
};

const REMINDER_STATUS_META: Record<
  string,
  { label: string; icon: "check" | "alert" | "info" | "pause"; tone: "success" | "error" | "neutral" | "active" }
> = {
  enabled: { label: "启用中", icon: "check", tone: "success" },
  paused: { label: "已暂停", icon: "pause", tone: "active" },
  completed: { label: "已完成", icon: "check", tone: "neutral" },
  cancelled: { label: "已取消", icon: "info", tone: "neutral" },
};

const WEEKDAY_LABELS = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"];

const REPEAT_LABELS: Record<ReminderRepeatRule, string> = {
  once: "一次",
  daily: "每天",
  weekdays: "工作日",
  weekly_days: "每周",
  monthly_day: "每月",
};

const DELIVERY_KIND_LABELS: Record<string, string> = {
  scheduled: "发送",
  catch_up: "补发",
  manual_retry: "手动重试",
};

const DELIVERY_OUTCOME_META: Record<
  string,
  { label: string; tone: "success" | "error" | "neutral" }
> = {
  sent: { label: "成功", tone: "success" },
  failed: { label: "失败", tone: "error" },
  skipped: { label: "跳过", tone: "neutral" },
};

// 常见 IANA 时区（Asia/Shanghai 为 QQ 用户默认）。
const TIMEZONE_OPTIONS = [
  "Asia/Shanghai",
  "Asia/Hong_Kong",
  "Asia/Tokyo",
  "Asia/Singapore",
  "Asia/Kuala_Lumpur",
  "Australia/Sydney",
  "Europe/London",
  "Europe/Paris",
  "America/New_York",
  "America/Los_Angeles",
  "America/Chicago",
  "America/Sao_Paulo",
  "UTC",
];

const AUTH_CODE_RE = /^[A-Za-z0-9]{10,32}$/;

// 轮询覆盖收件确认窗口（120 秒）+ 余量：1.5s × 100 = 150 秒。
const POLL_INTERVAL_MS = 1500;
const POLL_LIMIT = 100;

// 验证进行中的阶段文案（Issue 10：区分「发送中」与「等待收件」，
// 不提前宣称已验证；晚到邮件在窗口内持续轮询收敛）。
function verifyingHint(settings: SmtpSettingsProjection | null): string {
  const state = settings?.attempt_state;
  if (state === "smtp_connecting") return "正在发送验证邮件…";
  if (state === "mail_sent" || state === "waiting_receipt") {
    return "验证邮件已发送，正在确认收件（最长 120 秒；邮件可能延迟到达）。";
  }
  return "验证中，请稍候…";
}

function repeatLabel(reminder: ReminderProjection): string {
  const rule = reminder.schedule.repeat;
  if (rule === "weekly_days") {
    const days = (reminder.schedule.repeat_weekdays ?? [])
      .map((day) => (WEEKDAY_LABELS[day] ?? "").replace("周", ""))
      .join("、");
    return `每周${days}`;
  }
  if (rule === "monthly_day") {
    return `每月${reminder.schedule.repeat_month_day}日`;
  }
  return REPEAT_LABELS[rule] ?? rule;
}

function formatLocal(utcIso: string | null | undefined, timezone: string): string {
  if (!utcIso) return "—";
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date(utcIso));
  } catch {
    return utcIso;
  }
}

function StatusChip({
  tone,
  icon,
  label,
}: {
  tone: "success" | "error" | "neutral" | "active";
  icon: React.ComponentProps<typeof Icon>["name"];
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
    <span
      className={styles.statusChip}
      style={{ color: config.color, backgroundColor: config.bg }}
    >
      <Icon name={icon} size={14} aria-hidden />
      <span>{label}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// SMTP 配置卡
// ---------------------------------------------------------------------------

interface SmtpSetupCardProps {
  settings: SmtpSettingsProjection | null;
  onChanged: (next: SmtpSettingsProjection) => void;
  onSessionExpired: () => void;
}

/** SMTP 卡片内可自动重试的操作（原页再认证后重放）。 */
type SmtpAction = "save" | "reverify" | "remove";

function SmtpSetupCard({
  settings,
  onChanged,
  onSessionExpired,
}: SmtpSetupCardProps) {
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState<string | undefined>();
  const [saving, setSaving] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // 原页再认证（Issue 10）：reauth_required 不再整页阻断，卡片内密码
  // 确认成功后自动重试原命令；密码与授权码只留在组件内存，成功/取消
  // 后立即清空。pendingActionRef 记录等待重放的操作。
  const [reauthOpen, setReauthOpen] = useState(false);
  const [reauthPassword, setReauthPassword] = useState("");
  const [reauthError, setReauthError] = useState<string | undefined>();
  const [reauthBusy, setReauthBusy] = useState(false);
  const pendingActionRef = useRef<SmtpAction | null>(null);

  const handleChanged = (next: SmtpSettingsProjection, message: string) => {
    onChanged(next);
    setNotice(message);
    setActionError(null);
  };

  /** 执行敏感操作：reauth_required → 打开原页密码确认（不跳转不刷新）。 */
  const execute = async (
    action: SmtpAction,
    runner: () => Promise<SmtpSettingsProjection>
  ): Promise<SmtpSettingsProjection | null> => {
    try {
      return await runner();
    } catch (cause) {
      const kind = classifyApiError(cause);
      if (kind === "reauth") {
        pendingActionRef.current = action;
        setReauthOpen(true);
        setReauthError(undefined);
        setReauthPassword("");
        return null;
      }
      if (kind === "session") {
        onSessionExpired();
        return null;
      }
      throw cause;
    }
  };

  const save = async () => {
    setCodeError(undefined);
    if (!AUTH_CODE_RE.test(code)) {
      setCodeError("请输入 QQ 邮箱授权码（16 位字母数字），而不是 QQ 登录密码。");
      return;
    }
    setSaving(true);
    setActionError(null);
    try {
      const result = await execute("save", () => saveSmtpCode(code));
      if (result) {
        handleChanged(result, "授权码已保存，正在发送验证邮件…");
        setCode("");
      }
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "授权码保存失败，请稍后重试。");
    } finally {
      setSaving(false);
    }
  };

  const reverify = async () => {
    setVerifying(true);
    setActionError(null);
    try {
      const result = await execute("reverify", () => verifySmtpNow());
      if (result) {
        handleChanged(result, "已发起重新验证，请稍候。");
      }
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "重新验证失败，请稍后重试。");
    } finally {
      setVerifying(false);
    }
  };

  const remove = async () => {
    setDeleting(true);
    setActionError(null);
    try {
      const result = await execute("remove", () => deleteSmtpCode());
      if (result) {
        handleChanged(result, "已删除授权码并停止邮件提醒。");
        setConfirmingDelete(false);
      }
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "删除失败，请稍后重试。");
    } finally {
      setDeleting(false);
    }
  };

  /** 密码确认成功：立即清空密码并自动重试原命令（授权码无需重输）。 */
  const confirmReauth = async () => {
    setReauthError(undefined);
    if (!reauthPassword) {
      setReauthError("请输入当前账户密码。");
      return;
    }
    setReauthBusy(true);
    try {
      await reauthenticate(reauthPassword);
    } catch (cause) {
      const kind = classifyApiError(cause);
      if (kind === "session") {
        onSessionExpired();
        return;
      }
      // 密码错误：只显示局部错误并聚焦密码字段，不清空授权码、不跳转
      setReauthError(
        cause instanceof Error ? cause.message : "密码确认失败，请重试。"
      );
      return;
    } finally {
      setReauthBusy(false);
    }
    const action = pendingActionRef.current;
    pendingActionRef.current = null;
    setReauthPassword("");
    setReauthOpen(false);
    if (action === "save") await save();
    else if (action === "reverify") await reverify();
    else if (action === "remove") await remove();
  };

  /** 取消密码确认：清空敏感内存（密码；save 场景连同授权码），不改服务器。 */
  const cancelReauth = () => {
    const action = pendingActionRef.current;
    pendingActionRef.current = null;
    setReauthOpen(false);
    setReauthPassword("");
    setReauthError(undefined);
    if (action === "save") setCode("");
  };

  const status = settings
    ? SMTP_STATUS_META[settings.status] ?? SMTP_STATUS_META.unconfigured
    : SMTP_STATUS_META.unconfigured;

  return (
    <section className={`sc-card ${styles.card}`} aria-labelledby="smtp-title">
      <div className={styles.cardHeader}>
        <div>
          <h2 id="smtp-title" className="sc-section-title">
            QQ 邮箱提醒设置
          </h2>
          <p className={styles.hint}>
            收件人与发件人固定为你的 QQ 邮箱（{settings?.qq_email ?? "—"}），
            提醒只发给你自己，不会扩散到第三方。
          </p>
        </div>
        {settings && (
          <StatusChip tone={status.tone} icon={status.icon} label={status.label} />
        )}
      </div>

      {settings?.status === "failed" && (
        <div className={styles.failedBox} role="alert">
          <strong>验证失败：</strong>
          {settings.error_message ?? "邮箱授权码验证未通过。"}
          {settings.status === "failed" && (
            <Button variant="secondary" size="sm" onClick={reverify} isLoading={verifying}>
              重新验证
            </Button>
          )}
        </div>
      )}

      {settings?.status === "verifying" && (
        <p role="status" className={styles.notice}>
          {verifyingHint(settings)}
        </p>
      )}

      <form
        className={styles.codeForm}
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <PasswordField
          id="smtp-auth-code"
          label="QQ 邮箱授权码"
          value={code}
          onChange={setCode}
          error={codeError}
          hint="在 QQ 邮箱「设置 → 账号 → 开启 SMTP 服务」中生成；系统不接收 QQ 登录密码。"
          autoComplete="off"
          placeholder="16 位字母数字授权码"
        />
        <div className={styles.codeActions}>
          <Button type="submit" isLoading={saving} disabled={!code}>
            保存并验证
          </Button>
          {settings?.status === "verified" && (
            <>
              <Button variant="ghost" size="sm" onClick={reverify} isLoading={verifying}>
                重新验证
              </Button>
              {!confirmingDelete ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfirmingDelete(true)}
                >
                  删除授权码
                </Button>
              ) : (
                <span className={styles.confirmRow}>
                  <span className={styles.confirmText}>确认删除？</span>
                  <Button variant="danger" size="sm" onClick={remove} isLoading={deleting}>
                    确认删除
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setConfirmingDelete(false)}
                  >
                    取消
                  </Button>
                </span>
              )}
            </>
          )}
        </div>
      </form>

      {notice && (
        <p role="status" className={styles.notice}>
          {notice}
        </p>
      )}
      {actionError && <ErrorSummary errors={[actionError]} />}

      <Dialog
        open={reauthOpen}
        onClose={cancelReauth}
        title="安全确认"
        description="此操作需要近期密码确认。请输入当前 BridGes 账户密码以继续，无需重新登录；确认后自动完成原操作。"
      >
        <div className={styles.dialogBody}>
          <PasswordField
            id="reauth-password"
            label="当前账户密码"
            value={reauthPassword}
            onChange={(value) => {
              setReauthPassword(value);
              setReauthError(undefined);
            }}
            error={reauthError}
            autoComplete="current-password"
            autoFocus
          />
          <div className={styles.dialogActions}>
            <Button variant="secondary" onClick={cancelReauth}>
              取消
            </Button>
            <Button onClick={() => void confirmReauth()} isLoading={reauthBusy}>
              确认并继续
            </Button>
          </div>
        </div>
      </Dialog>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 新建 / 编辑提醒对话框（自然语言 → 预览 → 确认）
// ---------------------------------------------------------------------------

interface ReminderDialogProps {
  open: boolean;
  onClose: () => void;
  timezone: string;
  editing: ReminderProjection | null;
  onSaved: (reminder: ReminderProjection) => void;
  onRequireReauth: () => void;
  onSessionExpired: () => void;
}

function ReminderDialog({
  open,
  onClose,
  timezone,
  editing,
  onSaved,
  onRequireReauth,
  onSessionExpired,
}: ReminderDialogProps) {
  const [rawText, setRawText] = useState("");
  const [textError, setTextError] = useState<string | undefined>();
  const [parsing, setParsing] = useState(false);
  const [preview, setPreview] = useState<ParsedReminderPreview | null>(null);
  const [useProfile, setUseProfile] = useState(true);
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // 编辑模式预填原文；切换编辑目标时重置。
  useEffect(() => {
    if (!open) return;
    setRawText(editing?.raw_text ?? "");
    setPreview(null);
    setTextError(undefined);
    setActionError(null);
    setUseProfile(Boolean(editing?.profile_usage.enabled) || !editing);
  }, [open, editing]);

  const parse = async () => {
    setTextError(undefined);
    setActionError(null);
    if (!rawText.trim()) {
      setTextError("请输入提醒内容，例如「明天早上八点提醒我复习 transformer」。");
      return;
    }
    setParsing(true);
    try {
      const result = await parseReminder(rawText.trim(), timezone, useProfile);
      setPreview(result);
    } catch (cause) {
      const kind = classifyApiError(cause);
      if (kind === "reauth") {
        onRequireReauth();
      } else if (kind === "session") {
        onSessionExpired();
      } else {
        setActionError(
          cause instanceof Error ? cause.message : "解析失败，请检查输入后重试。"
        );
        setPreview(null);
      }
    } finally {
      setParsing(false);
    }
  };

  const confirm = async () => {
    if (!preview) return;
    setSaving(true);
    setActionError(null);
    try {
      const saved = editing
        ? await updateReminder(editing.reminder_id, preview, useProfile)
        : await createReminder(preview, useProfile);
      onSaved(saved);
      onClose();
    } catch (cause) {
      const kind = classifyApiError(cause);
      if (kind === "reauth") {
        onRequireReauth();
      } else if (kind === "session") {
        onSessionExpired();
      } else {
        // 预览与解析不一致（跨分钟边界）时自动重新解析并提示
        if (cause instanceof ApiError && cause.code === "preview_mismatch") {
          setActionError("确认内容已变化，已为你重新解析，请再次确认。");
          await parse();
        } else {
          setActionError(
            cause instanceof Error ? cause.message : "保存失败，请稍后重试。"
          );
        }
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={editing ? "编辑提醒" : "新建提醒"}
      description={
        editing
          ? "修改后将从新的确认内容重新启用提醒；历史投递记录保留。"
          : "用自然语言描述时间、重复规则和主题，确认预览后才会启用。"
      }
    >
      <div className={styles.dialogBody}>
        <label className={styles.fieldLabel} htmlFor="reminder-text">
          提醒内容
        </label>
        <textarea
          id="reminder-text"
          className={styles.textarea}
          rows={3}
          value={rawText}
          onChange={(event) => {
            setRawText(event.target.value);
            setTextError(undefined);
          }}
          onBlur={() => {
            if (!rawText.trim()) {
              setTextError("请输入提醒内容。");
            }
          }}
          aria-invalid={Boolean(textError)}
          aria-describedby={textError ? "reminder-text-error" : undefined}
          placeholder="例如：明天早上八点提醒我复习 transformer"
          data-testid="reminder-text"
        />
        {textError && (
          <p id="reminder-text-error" className={styles.fieldError} role="alert">
            {textError}
          </p>
        )}

        <div className={styles.profileRow}>
          <label className={styles.switchLabel} htmlFor="reminder-use-profile">
            <input
              id="reminder-use-profile"
              type="checkbox"
              checked={useProfile}
              onChange={(event) => {
                setUseProfile(event.target.checked);
                setPreview(null); // 开关变化后需重新解析
              }}
              data-testid="reminder-use-profile"
            />
            按画像适配提醒措辞
          </label>
        </div>

        {preview && (
          <div className={styles.previewBox} data-testid="reminder-preview">
            <h3 className={styles.previewTitle}>确认提醒内容</h3>
            <dl className={styles.previewGrid}>
              <dt>时区</dt>
              <dd>{preview.schedule.timezone}</dd>
              <dt>首次执行</dt>
              <dd>{preview.schedule.first_run_local}</dd>
              <dt>重复规则</dt>
              <dd>
                {preview.schedule.repeat === "weekly_days"
                  ? `每周${(preview.schedule.repeat_weekdays ?? [])
                      .map((day) => WEEKDAY_LABELS[day] ?? "")
                      .join("、")}`
                  : preview.schedule.repeat === "monthly_day"
                    ? `每月${preview.schedule.repeat_month_day}日`
                    : REPEAT_LABELS[preview.schedule.repeat]}
              </dd>
              <dt>主题</dt>
              <dd>{preview.subject}</dd>
            </dl>
            <div className={styles.bodyPreview}>
              <strong>邮件正文预览</strong>
              <pre>{preview.body_preview}</pre>
            </div>
            {preview.profile_usage.enabled && (
              <p className={styles.profileNote}>
                本次使用画像类别：
                {(preview.profile_usage.categories ?? []).map((category) => (
                  <span key={category} className={styles.categoryChip}>
                    {category}
                  </span>
                ))}
              </p>
            )}
          </div>
        )}

        {actionError && <ErrorSummary errors={[actionError]} />}

        <div className={styles.dialogActions}>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          {!preview ? (
            <Button onClick={parse} isLoading={parsing} data-testid="parse-reminder">
              解析并预览
            </Button>
          ) : (
            <Button onClick={confirm} isLoading={saving} data-testid="confirm-reminder">
              {editing ? "保存修改" : "确认创建"}
            </Button>
          )}
        </div>
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 提醒列表（卡片 + 可展开投递记录）
// ---------------------------------------------------------------------------

function DeliveryList({ reminderId, version }: { reminderId: string; version: number }) {
  const [deliveries, setDeliveries] = useState<ReminderDeliveryProjection[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDeliveries(null);
    setError(null);
    listReminderDeliveries(reminderId)
      .then(setDeliveries)
      .catch((cause) =>
        setError(cause instanceof Error ? cause.message : "投递记录读取失败。")
      );
  }, [reminderId, version]);

  if (error) {
    return <p className={styles.fieldError}>{error}</p>;
  }
  if (deliveries === null) {
    return <LoadingStatus message="投递记录加载中…" />;
  }
  if (deliveries.length === 0) {
    return <p className={styles.hint}>还没有投递记录；到点后系统会自动发送。</p>;
  }
  return (
    <ul className={styles.deliveryList}>
      {deliveries.map((delivery) => {
        const kindLabel = DELIVERY_KIND_LABELS[delivery.kind] ?? delivery.kind;
        const outcome = DELIVERY_OUTCOME_META[delivery.outcome] ?? {
          label: delivery.outcome,
          tone: "neutral" as const,
        };
        return (
          <li key={delivery.delivery_id} className={styles.deliveryRow}>
            <StatusChip
              tone={outcome.tone}
              icon={delivery.outcome === "failed" ? "alert" : delivery.outcome === "skipped" ? "info" : "check"}
              label={`${kindLabel}${delivery.outcome === "skipped" ? "" : "·" + outcome.label}`}
            />
            <span className={styles.deliveryTime}>
              {formatLocal(delivery.attempted_at, "UTC")}
            </span>
            {delivery.delayed && <span className={styles.delayedBadge}>延迟补发</span>}
            {delivery.error_message && (
              <span className={styles.deliveryError}>{delivery.error_message}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function ReminderCard({
  reminder,
  timezone,
  onChanged,
  onEdit,
  onRequireReauth,
  onSessionExpired,
}: {
  reminder: ReminderProjection;
  timezone: string;
  onChanged: (next: ReminderProjection) => void;
  onEdit: (reminder: ReminderProjection) => void;
  onRequireReauth: () => void;
  onSessionExpired: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [deliveriesVersion, setDeliveriesVersion] = useState(0);
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const status = REMINDER_STATUS_META[reminder.status] ?? REMINDER_STATUS_META.enabled;

  const act = async (action: string, runner: () => Promise<ReminderProjection>) => {
    setBusy(action);
    setError(null);
    try {
      onChanged(await runner());
    } catch (cause) {
      const kind = classifyApiError(cause);
      if (kind === "reauth") {
        onRequireReauth();
      } else if (kind === "session") {
        onSessionExpired();
      } else {
        setError(cause instanceof Error ? cause.message : "操作失败，请稍后重试。");
      }
    } finally {
      setBusy(null);
    }
  };

  const sendNow = async () => {
    setBusy("send-now");
    setError(null);
    try {
      await sendReminderNow(reminder.reminder_id);
      setExpanded(true);
      setDeliveriesVersion((version) => version + 1); // 刷新投递记录
    } catch (cause) {
      const kind = classifyApiError(cause);
      if (kind === "reauth") {
        onRequireReauth();
      } else if (kind === "session") {
        onSessionExpired();
      } else {
        setError(cause instanceof Error ? cause.message : "手动补发失败，请稍后重试。");
      }
    } finally {
      setBusy(null);
    }
  };

  const cancel = async () => {
    await act("cancel", () => cancelReminder(reminder.reminder_id));
    setConfirmingCancel(false);
  };

  return (
    <li className={`sc-card ${styles.reminderCard}`}>
      <div className={styles.reminderHeader}>
        <div className={styles.reminderTitleRow}>
          <h3 className={styles.reminderTitle}>{reminder.subject}</h3>
          <StatusChip tone={status.tone} icon={status.icon} label={status.label} />
        </div>
        <div className={styles.reminderMeta}>
          <span className={styles.repeatBadge}>{repeatLabel(reminder)}</span>
          <span className={styles.nextRun}>
            下次执行：{formatLocal(reminder.next_run_at, timezone)}（{timezone}）
          </span>
          {reminder.pause_reason && (
            <span className={styles.pauseReason}>{reminder.pause_reason}</span>
          )}
        </div>
      </div>

      <div className={styles.reminderActions}>
        {reminder.status === "enabled" && (
          <Button
            variant="ghost"
            size="sm"
            isLoading={busy === "pause"}
            onClick={() => void act("pause", () => pauseReminder(reminder.reminder_id))}
            data-testid="reminder-pause"
          >
            暂停
          </Button>
        )}
        {reminder.status === "paused" && (
          <Button
            variant="ghost"
            size="sm"
            isLoading={busy === "resume"}
            onClick={() => void act("resume", () => resumeReminder(reminder.reminder_id))}
            data-testid="reminder-resume"
          >
            恢复
          </Button>
        )}
        {(reminder.status === "enabled" || reminder.status === "paused") && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onEdit(reminder)}
            data-testid="reminder-edit"
          >
            编辑
          </Button>
        )}
        {reminder.status !== "cancelled" && (
          <Button
            variant="ghost"
            size="sm"
            isLoading={busy === "send-now"}
            onClick={() => void sendNow()}
            data-testid="reminder-send-now"
          >
            手动补发
          </Button>
        )}
        {!confirmingCancel && reminder.status !== "cancelled" && reminder.status !== "completed" ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setConfirmingCancel(true)}
            data-testid="reminder-cancel"
          >
            取消
          </Button>
        ) : confirmingCancel ? (
          <span className={styles.confirmRow}>
            <span className={styles.confirmText}>确认取消？</span>
            <Button variant="danger" size="sm" onClick={cancel} isLoading={busy === "cancel"}>
              确认取消
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirmingCancel(false)}>
              返回
            </Button>
          </span>
        ) : null}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          data-testid="reminder-deliveries-toggle"
        >
          {expanded ? "收起投递记录" : "查看投递记录"}
        </Button>
      </div>

      {error && <ErrorSummary errors={[error]} />}
      {expanded && (
        <DeliveryList reminderId={reminder.reminder_id} version={deliveriesVersion} />
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// 页面主组件
// ---------------------------------------------------------------------------

/** 主数据包：SMTP 设置 + 时区设置 + 提醒列表（一次加载，hook 统一重载）。 */
async function loadTaskScheduleData() {
  const [smtpSettings, reminderSettings, reminderList] = await Promise.all([
    fetchSmtpSettings(),
    fetchReminderSettings(),
    listReminders(),
  ]);
  return { smtpSettings, timezone: reminderSettings.timezone, reminders: reminderList };
}

export function TaskSchedule() {
  const { refreshSession } = useAuth();
  const [permissionDenied, setPermissionDenied] = useState(false);
  // 验证轮询期间的实时覆盖：smtp 展示优先用轮询结果，主数据刷新后丢弃。
  const [liveSmtp, setLiveSmtp] = useState<SmtpSettingsProjection | null>(null);
  const [timezone, setTimezone] = useState("Asia/Shanghai");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ReminderProjection | null>(null);

  // 主数据加载（SMTP 设置/时区/提醒列表）：loading/error/reload 由
  // useApiQuery 统一管理；会话过期经 error 分类后刷新会话再重载。
  const { data, error: queryError, loading, reload } = useApiQuery(
    "task-schedule",
    loadTaskScheduleData
  );
  const smtp = liveSmtp ?? data?.smtpSettings ?? null;
  const reminders = data?.reminders ?? [];

  // 主数据更新后同步时区并丢弃轮询覆盖（回到服务端权威值）。
  useEffect(() => {
    if (!data) return;
    setTimezone(data.timezone);
    setLiveSmtp(null);
  }, [data]);

  // 加载错误分类：reauth 直接显示重登录页；session 过期先刷新会话再重载。
  useEffect(() => {
    if (!queryError) return;
    const kind = classifyApiError(queryError);
    if (kind === "reauth") {
      setPermissionDenied(true);
    } else if (kind === "session") {
      void refreshSession().then(() => reload());
    }
  }, [queryError, refreshSession, reload]);

  // 验证进行中轮询（保存/重新验证后状态自动收敛；成功 verified 时触发主数据重载）。
  useEffect(() => {
    if (!smtp || smtp.status !== "verifying") return;
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      if (attempts > POLL_LIMIT) {
        window.clearInterval(timer);
        return;
      }
      fetchSmtpSettings()
        .then((next) => {
          setLiveSmtp(next);
          if (next.status !== "verifying") {
            window.clearInterval(timer);
            if (next.status === "verified") {
              reload();
            }
          }
        })
        .catch(() => window.clearInterval(timer));
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
    // smtp 仅用于起始守卫；轮询回调不读它，只按状态变化重启。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [smtp?.status, reload]);

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };

  if (loading) {
    return <LoadingStatus message="任务安排加载中…" />;
  }
  if (permissionDenied) {
    return (
      <StateBlock
        kind="error"
        title="需要重新输入密码"
        description="此页面包含敏感设置（QQ 邮箱授权码），请重新登录当前账户后再访问。"
      />
    );
  }
  if (queryError) {
    return <StateBlock kind="error" title="任务安排加载失败" description={queryError.message} />;
  }

  const verified = smtp?.status === "verified";

  return (
    <>
      <SmtpSetupCard
        settings={smtp}
        onChanged={setLiveSmtp}
        onSessionExpired={() => void refreshSession()}
      />

      <section className={`sc-card ${styles.card}`} aria-labelledby="timezone-title">
        <div className={styles.cardHeader}>
          <div>
            <h2 id="timezone-title" className="sc-section-title">
              时区设置
            </h2>
            <p className={styles.hint}>
              提醒时间按此地区换算并保存 UTC 执行时刻；切换后新提醒使用新时区。
            </p>
          </div>
        </div>
        <label className={styles.fieldLabel} htmlFor="reminder-timezone">
          时区
        </label>
        <select
          id="reminder-timezone"
          className={styles.select}
          value={timezone}
          onChange={(event) => {
            const next = event.target.value;
            setTimezone(next);
            // 时区保存失败静默（原实现 setLoadError 在 ready 态也不展示）。
            void updateReminderSettings(next).catch(() => {});
          }}
          data-testid="reminder-timezone"
        >
          {TIMEZONE_OPTIONS.map((zone) => (
            <option key={zone} value={zone}>
              {zone}
            </option>
          ))}
        </select>
      </section>

      <section className={styles.listSection} aria-labelledby="reminders-title">
        <div className={styles.listHeader}>
          <h2 id="reminders-title" className="sc-section-title">
            我的提醒
          </h2>
          <Button
            onClick={openCreate}
            disabled={!verified}
            title={verified ? undefined : "请先完成邮箱验证"}
            data-testid="new-reminder"
          >
            <Icon name="plus" size={18} aria-hidden />
            新建提醒
          </Button>
        </div>
        {!verified && (
          <p className={styles.hint}>
            完成 QQ 邮箱自发自收验证后即可创建提醒；未验证的账户不能启用邮件提醒。
          </p>
        )}
        {reminders.length === 0 ? (
          <StateBlock
            kind="empty"
            title="还没有提醒"
            description={
              verified
                ? "点击「新建提醒」，用一句话描述时间与内容，例如「明天早上八点提醒我复习 transformer」。"
                : "先在上方配置并验证 QQ 邮箱授权码，验证通过后即可创建提醒。"
            }
            actionLabel="返回新聊天"
            actionHref="/"
          />
        ) : (
          <ul className={styles.reminderList}>
            {reminders.map((reminder) => (
              <ReminderCard
                key={reminder.reminder_id}
                reminder={reminder}
                timezone={timezone}
                onChanged={reload}
                onEdit={(target) => {
                  setEditing(target);
                  setDialogOpen(true);
                }}
                onRequireReauth={() => setPermissionDenied(true)}
                onSessionExpired={() => void refreshSession()}
              />
            ))}
          </ul>
        )}
      </section>

      <ReminderDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        timezone={timezone}
        editing={editing}
        onSaved={reload}
        onRequireReauth={() => setPermissionDenied(true)}
        onSessionExpired={() => void refreshSession()}
      />
    </>
  );
}
