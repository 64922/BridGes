"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { PasswordField } from "@/components/bridges/PasswordField";
import { StateBlock } from "@/components/bridges/StateBlock";
import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { Icon } from "@/components/design-system/Icon";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { useAuth } from "@/context/AuthContext";
import {
  ApiError,
  fetchKeySettings,
  type KeySettingsProjection,
  reauthenticate,
} from "@/lib/api";

import styles from "./AccountSettings.module.css";

type PageState = "loading" | "ready" | "reauth" | "error";

/** Protected key-settings page with a truthful pre-Issue-10 empty state. */
export function KeySettings() {
  const { user, refreshSession } = useAuth();
  const [pageState, setPageState] = useState<PageState>("loading");
  const [settings, setSettings] = useState<KeySettingsProjection | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | undefined>();
  const [submitting, setSubmitting] = useState(false);
  const [reauthenticated, setReauthenticated] = useState(false);
  const initialLoadStarted = useRef(false);

  const load = useCallback(async () => {
    setPageState("loading");
    setLoadError(null);
    try {
      const result = await fetchKeySettings();
      setSettings(result);
      setPageState("ready");
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === "reauth_required") {
        setPageState("reauth");
        return;
      }
      if (cause instanceof ApiError && cause.status === 401) {
        await refreshSession();
        return;
      }
      setLoadError(
        cause instanceof Error ? cause.message : "密钥状态读取失败，请稍后重试。"
      );
      setPageState("error");
    }
  }, [refreshSession]);

  useEffect(() => {
    if (initialLoadStarted.current) return;
    initialLoadStarted.current = true;
    void load();
  }, [load]);

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

  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <header>
          <p className={styles.eyebrow}>账户设置 · 密钥设置</p>
          <h1 className={styles.title}>模型连接与能力状态</h1>
          <p className={styles.lead}>
            此页面只展示当前账户的密钥配置状态。任何密钥内容都不会出现在页面、URL 或日志中。
          </p>
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
              <section className={styles.card} aria-labelledby="key-status-title">
                <div className={styles.cardHeader}>
                  <div>
                    <h2 id="key-status-title" className={styles.cardTitle}>百炼密钥</h2>
                    <p className={styles.cardDescription}>
                      状态来自受保护接口，不以演示数据或 Stub 冒充可用能力。
                    </p>
                  </div>
                  <span className={styles.statusBadge} data-testid="key-status">
                    <Icon name="alert" size={16} aria-hidden />
                    尚未配置
                  </span>
                </div>

                <div className={styles.warningBanner} role="status">
                  <Icon name="alert" size={22} aria-hidden />
                  <div>
                    <strong>{settings.message}</strong>
                    <p className={styles.hint}>当前没有可验证的模型能力，系统不会显示“连接成功”。</p>
                  </div>
                </div>

                <div className={styles.nextStep}>
                  <Icon name="settings" size={24} aria-hidden />
                  <div>
                    <h3>下一步</h3>
                    <p>{settings.next_step}</p>
                  </div>
                </div>
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
