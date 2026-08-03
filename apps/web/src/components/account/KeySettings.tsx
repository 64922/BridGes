"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { PasswordField } from "@/components/bridges/PasswordField";
import { StateBlock } from "@/components/bridges/StateBlock";
import { Button } from "@/components/design-system/Button";
import { ButtonLink } from "@/components/design-system/ButtonLink";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { Icon } from "@/components/design-system/Icon";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { useAuth } from "@/context/AuthContext";
import {
  ApiError,
  classifyApiError,
  deleteKeySettings,
  fetchKeySettings,
  probeAllCapabilities,
  reauthenticate,
  retryCapabilityProbe,
  saveKeySettings,
  type CapabilityProbeSummary,
  type KeySettingsProjection,
} from "@/lib/api";

import styles from "./AccountSettings.module.css";

type PageState = "loading" | "ready" | "reauth" | "error";

interface StatusMeta {
  label: string;
  icon: "check" | "alert" | "info" | "retry";
  tone: "success" | "error" | "neutral" | "active";
}

const STATUS_META: Record<string, StatusMeta> = {
  not_probed: { label: "未探测", icon: "info", tone: "neutral" },
  probing: { label: "探测中", icon: "info", tone: "active" },
  available: { label: "可用", icon: "check", tone: "success" },
  unavailable: { label: "不可用", icon: "alert", tone: "error" },
};

const POLL_INTERVAL_MS = 1500;
const POLL_LIMIT = 120;

/** 受保护的密钥设置页：录入/替换/删除百炼 Key + 固定能力真实探测状态。 */
export function KeySettings() {
  const { user, refreshSession } = useAuth();
  const [pageState, setPageState] = useState<PageState>("loading");
  const [settings, setSettings] = useState<KeySettingsProjection | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | undefined>();
  const [submitting, setSubmitting] = useState(false);
  const [reauthenticated, setReauthenticated] = useState(false);

  // 密钥录入/替换表单
  const [keyValue, setKeyValue] = useState("");
  const [keyError, setKeyError] = useState<string | undefined>();
  const [saving, setSaving] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // 删除确认（两步式，纯键盘可达）
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const initialLoadStarted = useRef(false);
  const pollStarted = useRef(false);

  const applySettings = useCallback((next: KeySettingsProjection) => {
    setSettings(next);
    setPageState("ready");
  }, []);

  const load = useCallback(async () => {
    setPageState("loading");
    setLoadError(null);
    try {
      applySettings(await fetchKeySettings());
    } catch (cause) {
      const kind = classifyApiError(cause);
      if (kind === "reauth") {
        setPageState("reauth");
        return;
      }
      if (kind === "session") {
        await refreshSession();
        return;
      }
      setLoadError(
        cause instanceof Error ? cause.message : "密钥状态读取失败，请稍后重试。"
      );
      setPageState("error");
    }
  }, [applySettings, refreshSession]);

  useEffect(() => {
    if (initialLoadStarted.current) return;
    initialLoadStarted.current = true;
    void load();
  }, [load]);

  const anyProbing = (settings?.capabilities ?? []).some(
    (capability) => capability.status === "probing"
  );

  // 探测进行中轮询：保存/重试后逐项呈现真实状态，直到全部完成。
  useEffect(() => {
    if (!anyProbing) {
      pollStarted.current = false;
      return;
    }
    if (pollStarted.current) return;
    pollStarted.current = true;
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      if (attempts > POLL_LIMIT) {
        window.clearInterval(timer);
        pollStarted.current = false;
        return;
      }
      void fetchKeySettings()
        .then((next) => {
          applySettings(next);
          if (!(next.capabilities ?? []).some((c) => c.status === "probing")) {
            window.clearInterval(timer);
            pollStarted.current = false;
            setNotice("能力探测已完成。");
          }
        })
        .catch((cause) => {
          const kind = classifyApiError(cause);
          if (kind === "reauth" || kind === "session") {
            window.clearInterval(timer);
            pollStarted.current = false;
            if (kind === "reauth") {
              setPageState("reauth");
            } else {
              void refreshSession();
            }
          }
        });
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [anyProbing, applySettings, refreshSession]);

  const submitReauthentication = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!password) {
      setPasswordError("请输入当前账户密码。");
      document.getElementById("key-settings-password")?.focus();
      return;
    }
    setSubmitting(true);
    setPasswordError(undefined);
    try {
      await reauthenticate(password);
      setPassword("");
      setReauthenticated(true);
      await load();
    } catch (cause) {
      if (
        cause instanceof ApiError &&
        cause.status === 401 &&
        cause.code === "reauthentication_failed"
      ) {
        setPasswordError(cause.message);
      } else if (cause instanceof ApiError && cause.status === 401) {
        await refreshSession();
      } else {
        setPasswordError(
          cause instanceof Error ? cause.message : "身份确认失败，请稍后重试。"
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  const submitKey = async (event: React.FormEvent) => {
    event.preventDefault();
    const value = keyValue.trim();
    if (!value) {
      setKeyError("请输入百炼 Key。");
      document.getElementById("bridges-key-input")?.focus();
      return;
    }
    setSaving(true);
    setKeyError(undefined);
    setNotice(null);
    try {
      const next = await saveKeySettings(value);
      setKeyValue("");
      setReplacing(false);
      setConfirmingDelete(false);
      applySettings(next);
      setNotice("Key 已保存，正在用非用户数据逐项真实探测固定能力。");
    } catch (cause) {
      const kind = classifyApiError(cause);
      if (kind === "reauth") {
        setPageState("reauth");
        return;
      }
      if (kind === "session") {
        await refreshSession();
        return;
      }
      setKeyError(
        cause instanceof Error ? cause.message : "保存失败，请稍后重试。"
      );
    } finally {
      setSaving(false);
    }
  };

  const submitDelete = async () => {
    setDeleting(true);
    setNotice(null);
    try {
      const next = await deleteKeySettings();
      setConfirmingDelete(false);
      setReplacing(false);
      applySettings(next);
      setNotice("Key 已删除，能力探测状态已复位。");
    } catch (cause) {
      const kind = classifyApiError(cause);
      if (kind === "reauth") {
        setPageState("reauth");
        return;
      }
      if (kind === "session") {
        await refreshSession();
        return;
      }
      setNotice(
        cause instanceof Error ? cause.message : "删除失败，请稍后重试。"
      );
    } finally {
      setDeleting(false);
    }
  };

  const submitRetry = async (capabilityId: string) => {
    setNotice(null);
    try {
      applySettings(await retryCapabilityProbe(capabilityId));
    } catch (cause) {
      const kind = classifyApiError(cause);
      if (kind === "reauth") {
        setPageState("reauth");
        return;
      }
      if (kind === "session") {
        await refreshSession();
        return;
      }
      setNotice(
        cause instanceof Error ? cause.message : "重试失败，请稍后再试。"
      );
    }
  };

  const submitProbeAll = async () => {
    setNotice(null);
    try {
      applySettings(await probeAllCapabilities());
    } catch (cause) {
      const kind = classifyApiError(cause);
      if (kind === "reauth") {
        setPageState("reauth");
        return;
      }
      if (kind === "session") {
        await refreshSession();
        return;
      }
      setNotice(
        cause instanceof Error ? cause.message : "重新探测失败，请稍后再试。"
      );
    }
  };

  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <header>
          <p className={styles.eyebrow}>账户设置 · 密钥设置</p>
          <h1 className={styles.title}>模型连接与能力状态</h1>
          <p className={styles.lead}>
            百炼 Key 只保存在当前账户的本机凭据库中，任何密钥内容都不会出现在页面、URL、日志或响应里。
          </p>
          <div style={{ marginTop: "var(--space-4)" }}>
            <ButtonLink href="/account/settings" variant="secondary">
              返回设置中心
            </ButtonLink>
          </div>
        </header>

        <div className={styles.stack}>
          {pageState === "loading" && (
            <section className={styles.card} aria-label="正在读取密钥状态">
              <StateBlock
                kind="loading"
                title="正在读取密钥状态"
                description="仅在当前账户授权确认后显示设置结果。"
              />
            </section>
          )}

          {pageState === "error" && (
            <section className={styles.card}>
              <StateBlock
                kind="error"
                title="密钥状态读取失败"
                description={loadError || "请检查连接后重试。"}
                actionLabel="重新读取"
                onAction={() => void load()}
              />
            </section>
          )}

          {pageState === "reauth" && (
            <section className={styles.card} aria-labelledby="reauth-title">
              <div className={styles.warningBanner}>
                <Icon name="account" size={22} aria-hidden />
                <div>
                  <strong id="reauth-title">需要重新确认身份</strong>
                  <p className={styles.hint}>
                    密钥设置属于敏感操作。请输入当前账户密码，确认结果只在短时间内有效。
                  </p>
                </div>
              </div>
              <form
                className={styles.formStack}
                onSubmit={submitReauthentication}
                noValidate
                style={{ marginTop: "var(--space-6)" }}
              >
                {passwordError && (
                  <ErrorSummary title="身份确认失败" errors={[passwordError]} />
                )}
                <PasswordField
                  id="key-settings-password"
                  label="当前账户密码"
                  value={password}
                  onChange={(value) => {
                    setPassword(value);
                    setPasswordError(undefined);
                  }}
                  error={passwordError}
                  hint={`正在确认 ${user?.username || "当前账户"}，密码不会被保存。`}
                  required
                  autoComplete="current-password"
                />
                <div className={styles.actions}>
                  {submitting && <LoadingStatus message="正在确认身份…" />}
                  <Button type="submit" variant="primary" disabled={submitting}>
                    {submitting ? "正在确认…" : "确认并继续"}
                  </Button>
                </div>
              </form>
            </section>
          )}

          {pageState === "ready" && settings && (
            <>
              {reauthenticated && (
                <div className={styles.successBanner} role="status">
                  <Icon name="check" size={20} aria-hidden />
                  <span>身份确认成功，已安全读取当前账户的配置状态。</span>
                </div>
              )}

              {notice && (
                <div className={styles.infoBanner} role="status">
                  <Icon name="info" size={20} aria-hidden />
                  <span>{notice}</span>
                </div>
              )}

              <section className={styles.card} aria-labelledby="key-card-title">
                <div className={styles.cardHeader}>
                  <div>
                    <h2 id="key-card-title" className={styles.cardTitle}>百炼密钥</h2>
                    <p className={styles.cardDescription}>
                      {settings.configured
                        ? `已配置（尾号 ${settings.key_tail ?? ""}）。替换或删除都需要再次确认密码。`
                        : "录入后系统会用非用户数据逐项真实探测固定能力，绝不显示 Stub 成功。"}
                    </p>
                  </div>
                  <span
                    className={styles.statusBadge}
                    data-testid="key-status"
                    data-configured={settings.configured}
                  >
                    <Icon
                      name={settings.configured ? "check" : "alert"}
                      size={16}
                      aria-hidden
                    />
                    {settings.configured ? "已配置" : "尚未配置"}
                  </span>
                </div>

                {!settings.configured && (
                  <form
                    className={styles.formStack}
                    onSubmit={submitKey}
                    noValidate
                  >
                    {keyError && (
                      <ErrorSummary title="保存失败" errors={[keyError]} />
                    )}
                    <PasswordField
                      id="bridges-key-input"
                      label="百炼 API Key"
                      value={keyValue}
                      onChange={(value) => {
                        setKeyValue(value);
                        setKeyError(undefined);
                      }}
                      error={keyError}
                      hint="Key 只保存到当前账户的受保护凭据库（本机凭据管理器或加密卷），不会写入数据库、日志或浏览器存储。"
                      placeholder="sk-…"
                      required
                      autoComplete="off"
                    />
                    <div className={styles.actions}>
                      {saving && <LoadingStatus message="正在保存并探测…" />}
                      <Button type="submit" variant="primary" disabled={saving}>
                        {saving ? "正在保存…" : "保存并逐项探测"}
                      </Button>
                    </div>
                  </form>
                )}

                {settings.configured && !replacing && (
                  <div className={styles.keySummary}>
                    <div className={styles.keySummaryMeta}>
                      <span className={styles.modelChip} data-testid="key-tail">
                        {settings.key_tail}
                      </span>
                      {settings.updated_at && (
                        <span className={styles.hint}>
                          最近更新：{new Date(settings.updated_at).toLocaleString("zh-CN")}
                        </span>
                      )}
                    </div>
                    <div className={styles.actions}>
                      <Button
                        variant="secondary"
                        onClick={() => setReplacing(true)}
                      >
                        替换密钥
                      </Button>
                      <Button
                        variant="danger"
                        onClick={() => setConfirmingDelete(true)}
                        disabled={deleting}
                      >
                        {deleting ? "正在删除…" : "删除密钥"}
                      </Button>
                    </div>
                  </div>
                )}

                {settings.configured && replacing && (
                  <form
                    className={styles.formStack}
                    onSubmit={submitKey}
                    noValidate
                  >
                    {keyError && (
                      <ErrorSummary title="替换失败" errors={[keyError]} />
                    )}
                    <PasswordField
                      id="bridges-key-input"
                      label="新的百炼 API Key"
                      value={keyValue}
                      onChange={(value) => {
                        setKeyValue(value);
                        setKeyError(undefined);
                      }}
                      error={keyError}
                      hint="保存新 Key 后将以同一固定矩阵重新逐项探测。"
                      placeholder="sk-…"
                      required
                      autoComplete="off"
                    />
                    <div className={styles.actions}>
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={() => {
                          setReplacing(false);
                          setKeyError(undefined);
                        }}
                        disabled={saving}
                      >
                        取消
                      </Button>
                      <Button type="submit" variant="primary" disabled={saving}>
                        {saving ? "正在保存…" : "保存新 Key 并重新探测"}
                      </Button>
                    </div>
                  </form>
                )}

                <Dialog
                  open={Boolean(settings.configured && confirmingDelete)}
                  onClose={() => {
                    if (!deleting) setConfirmingDelete(false);
                  }}
                  title="确认删除百炼 Key"
                  description="删除后当前账户将无法使用 AI 能力，需重新录入 Key 才能再次探测。"
                >
                  <div className={styles.deleteConfirmActions}>
                    <Button
                      variant="ghost"
                      onClick={() => setConfirmingDelete(false)}
                      disabled={deleting}
                    >
                      取消
                    </Button>
                    <Button
                      variant="danger"
                      onClick={() => void submitDelete()}
                      disabled={deleting}
                      data-testid="confirm-delete-key"
                    >
                      {deleting ? "正在删除…" : "确认删除"}
                    </Button>
                  </div>
                </Dialog>
              </section>

              <section
                className={styles.card}
                aria-labelledby="capability-list-title"
              >
                <div className={styles.cardHeader}>
                  <div>
                    <h2 id="capability-list-title" className={styles.cardTitle}>
                      固定能力矩阵
                    </h2>
                    <p className={styles.cardDescription}>
                      能力与模型绑定固定（ADR-0009），不提供模型选择或备用模型；失败只允许对同一绑定重试。
                    </p>
                  </div>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => void submitProbeAll()}
                    disabled={!settings.configured || anyProbing}
                  >
                    重新探测全部
                  </Button>
                </div>

                <ul className={styles.capabilityList} aria-label="能力探测状态">
                  {(settings.capabilities ?? []).map((capability) => (
                    <CapabilityRow
                      key={capability.capability_id}
                      capability={capability}
                      configured={settings.configured}
                      onRetry={() => void submitRetry(capability.capability_id)}
                    />
                  ))}
                </ul>

                {anyProbing && (
                  <div
                    className={styles.probingNotice}
                    role="status"
                    aria-live="polite"
                  >
                    <LoadingStatus message="正在逐项真实探测，请稍候…" />
                  </div>
                )}
              </section>

              <div className={styles.infoBanner}>
                <Icon name="info" size={20} aria-hidden />
                <span>安全提醒：不要把百炼 Key 粘贴到聊天、文件名或问题反馈中。</span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function CapabilityRow({
  capability,
  configured,
  onRetry,
}: {
  capability: CapabilityProbeSummary;
  configured: boolean;
  onRetry: () => void;
}) {
  const meta = STATUS_META[capability.status] ?? STATUS_META.not_probed;
  return (
    <li className={styles.capabilityItem}>
      <div className={styles.capabilityMain}>
        <div className={styles.capabilityTitle}>
          <span className={styles.capabilityName}>{capability.display_name}</span>
          <code className={styles.modelChip}>{capability.model_id}</code>
        </div>
        <p className={styles.hint}>
          {capability.message || (configured ? "尚未探测。" : "配置密钥后开始探测。")}
        </p>
      </div>
      <div className={styles.capabilityActions}>
        <span
          className={`${styles.probeBadge} ${styles[`probeTone_${meta.tone}`]}`}
          data-testid={`capability-${capability.capability_id}`}
          data-status={capability.status}
        >
          <Icon name={meta.icon} size={14} aria-hidden />
          {meta.label}
        </span>
        {capability.can_retry && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onRetry}
            aria-label={`重试${capability.display_name}探测（同一模型绑定）`}
          >
            <Icon name="retry" size={14} aria-hidden />
            重试
          </Button>
        )}
      </div>
    </li>
  );
}
