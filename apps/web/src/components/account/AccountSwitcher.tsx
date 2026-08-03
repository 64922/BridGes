"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  classifyApiError,
  listDeviceAccounts,
  type DeviceAccountProjection,
} from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/design-system/Button";
import { Dialog } from "@/components/bridges/Dialog";
import { FormField } from "@/components/bridges/FormField";
import { PasswordField } from "@/components/bridges/PasswordField";
import { Icon } from "@/components/design-system/Icon";

import styles from "./AccountSwitcher.module.css";

type SwitcherMode =
  | "accounts"
  | "add"
  | "reauth"
  | "confirm-logout-current"
  | "confirm-logout-all";

interface AccountSwitcherProps {
  open: boolean;
  onClose: () => void;
}

function SwitcherAvatar({ account }: { account: DeviceAccountProjection }) {
  if (account.avatar_choice === "uploaded" && account.has_uploaded_avatar) {
    // 设备作用域头像端点：只有已注册在本设备的会话才能读取。
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={`/api/auth/device/accounts/${encodeURIComponent(account.session_id)}/avatar`}
        alt=""
        className={styles.avatarImage}
      />
    );
  }
  return (
    <span className={styles.avatar} aria-hidden="true">
      {account.avatar_choice === "knowledge" ? (
        <Icon name="knowledgeBase" size={20} />
      ) : account.avatar_choice === "constellation" ? (
        <Icon name="learningProject" size={20} />
      ) : account.avatar_choice === "bridge" ? (
        <Icon name="account" size={20} />
      ) : (
        account.username.slice(0, 1).toLocaleUpperCase() || "桥"
      )}
    </span>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  return "暂时无法完成操作，请检查连接后重试。";
}

export function AccountSwitcher({ open, onClose }: AccountSwitcherProps) {
  const {
    addAccount,
    logoutAllAccounts,
    logoutCurrentAccount,
    reauthenticateAccount,
    refreshSession,
    switchAccount,
  } = useAuth();
  const [mode, setMode] = useState<SwitcherMode>("accounts");
  const [accounts, setAccounts] = useState<DeviceAccountProjection[]>([]);
  const [selected, setSelected] = useState<DeviceAccountProjection | null>(null);
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const loadId = useRef(0);

  const loadAccounts = useCallback(async () => {
    const currentLoad = ++loadId.current;
    setLoading(true);
    setError(null);
    try {
      const result = await listDeviceAccounts();
      if (currentLoad === loadId.current) {
        setAccounts(result.accounts);
      }
    } catch (cause) {
      if (currentLoad === loadId.current) {
        if (classifyApiError(cause) === "session") {
          onClose();
          void refreshSession();
          return;
        }
        setError(errorMessage(cause));
      }
    } finally {
      if (currentLoad === loadId.current) {
        setLoading(false);
      }
    }
  }, [onClose, refreshSession]);

  useEffect(() => {
    if (!open) return;
    setMode("accounts");
    setSelected(null);
    setIdentifier("");
    setPassword("");
    void loadAccounts();
  }, [loadAccounts, open]);

  useEffect(() => {
    if (!open || mode === "accounts") return;
    const focusTimer = window.setTimeout(() => {
      document.getElementById(mode === "add" ? "device-account-identifier" : "device-account-password")?.focus();
    }, 0);
    return () => window.clearTimeout(focusTimer);
  }, [mode, open]);

  const selectAccount = async (account: DeviceAccountProjection) => {
    if (account.is_current || loading) return;
    if (account.status === "reauth_required") {
      setSelected(account);
      setPassword("");
      setError(null);
      setMode("reauth");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const applied = await switchAccount(account.session_id);
      if (applied) onClose();
    } catch (cause) {
      if (
        cause instanceof ApiError &&
        (cause.code === "reauth_required" || cause.code === "device_account_unavailable")
      ) {
        setSelected(account);
        setPassword("");
        setMode("reauth");
      } else if (classifyApiError(cause) === "session") {
        onClose();
        void refreshSession();
      } else {
        setError(errorMessage(cause));
      }
    } finally {
      setLoading(false);
    }
  };

  const submitAdd = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!identifier.trim() || !password) {
      setError("请输入用户名或 QQ 邮箱，以及该账户密码。");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const applied = await addAccount(identifier.trim(), password);
      if (applied) onClose();
    } catch (cause) {
      if (classifyApiError(cause) === "session") {
        onClose();
        void refreshSession();
      } else {
        setError(errorMessage(cause));
      }
    } finally {
      setLoading(false);
    }
  };

  const submitReauth = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected || !password) {
      setError("请输入该账户密码。");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const applied = await reauthenticateAccount(selected.session_id, password);
      if (applied) onClose();
    } catch (cause) {
      if (classifyApiError(cause) === "session") {
        onClose();
        void refreshSession();
      } else {
        setError(errorMessage(cause));
      }
    } finally {
      setLoading(false);
    }
  };

  const logoutCurrent = async () => {
    setLoading(true);
    setError(null);
    try {
      const nextAccount = await logoutCurrentAccount();
      onClose();
      if (!nextAccount) {
        window.location.replace("/login?from=device-logout");
      }
    } catch (cause) {
      if (classifyApiError(cause) === "session") {
        onClose();
        void refreshSession();
      } else {
        setError(errorMessage(cause));
      }
    } finally {
      setLoading(false);
    }
  };

  const logoutAll = async () => {
    setLoading(true);
    setError(null);
    try {
      await logoutAllAccounts();
      onClose();
      window.location.replace("/login?from=device-logout");
    } catch (cause) {
      if (classifyApiError(cause) === "session") {
        onClose();
        void refreshSession();
      } else {
        setError(errorMessage(cause));
      }
    } finally {
      setLoading(false);
    }
  };

  const dialogTitle =
    mode === "accounts"
      ? "切换账号"
      : mode === "add"
        ? "添加账户"
        : mode === "reauth"
          ? "重新认证"
          : mode === "confirm-logout-current"
            ? "确认退出当前账户"
            : "确认退出全部账户";

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={dialogTitle}
      description={
        mode === "accounts"
          ? "同一设备上的账户各自拥有独立会话、对话与设置。"
          : mode === "add"
            ? "首次添加必须使用用户名或 QQ 邮箱和密码完成登录。"
            : mode === "reauth"
              ? `“${selected?.username ?? "该账户"}”的会话已失效，请输入该账户密码。`
              : mode === "confirm-logout-current"
                ? "这将撤销当前账户会话；如果设备上还有其他有效账户，将安全切换到下一个账户。"
                : "这将撤销此设备上的全部账户会话，并返回登录页。"
      }
    >
      {mode === "accounts" && (
        <div className={styles.content} data-testid="account-switcher">
          {loading && (
            <div className={styles.state} role="status" aria-live="polite">
              <span className={styles.spinner} aria-hidden="true" />
              正在读取设备账户…
            </div>
          )}
          {error && (
            <div className={styles.error} role="alert">
              <Icon name="alert" size={18} aria-hidden />
              <span>{error}</span>
              <Button variant="ghost" size="sm" onClick={() => void loadAccounts()}>
                重试
              </Button>
            </div>
          )}
          {!loading && !error && (
            <>
              <div className={styles.accountList} role="list" aria-label="设备上的账户">
                {accounts.map((account) => (
                  <button
                    key={account.session_id}
                    type="button"
                    className={styles.accountRow}
                    aria-current={account.is_current ? "true" : undefined}
                    aria-label={`${account.username}，${account.masked_qq_email}${account.is_current ? "，当前账户" : ""}`}
                    onClick={() => void selectAccount(account)}
                    disabled={loading}
                  >
                    <SwitcherAvatar account={account} />
                    <span className={styles.accountCopy}>
                      <strong>{account.username}</strong>
                      <span>{account.masked_qq_email}</span>
                    </span>
                    <span className={styles.accountStatus}>
                      {account.is_current ? "当前账户" : account.status === "active" ? "可切换" : "需密码"}
                    </span>
                  </button>
                ))}
              </div>
              {accounts.length <= 1 && (
                <p className={styles.empty} data-testid="no-other-account">
                  当前设备暂无其他账户。
                </p>
              )}
              <div className={styles.actions}>
                <Button variant="secondary" onClick={() => setMode("add")}>
                  <Icon name="plus" size={18} aria-hidden />
                  添加账户
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setError(null);
                    setMode("confirm-logout-current");
                  }}
                  disabled={loading}
                >
                  退出当前账户
                </Button>
                <Button
                  variant="danger"
                  onClick={() => {
                    setError(null);
                    setMode("confirm-logout-all");
                  }}
                  disabled={loading}
                >
                  退出此设备上的全部账户
                </Button>
              </div>
            </>
          )}
        </div>
      )}

      {mode === "add" && (
        <form className={styles.form} onSubmit={submitAdd}>
          <FormField
            id="device-account-identifier"
            label="用户名或 QQ 邮箱"
            value={identifier}
            onChange={setIdentifier}
            required
            autoComplete="username"
          />
          <PasswordField
            id="device-account-password"
            label="密码"
            value={password}
            onChange={setPassword}
            required
            autoComplete="current-password"
          />
          {error && <p className={styles.errorText} role="alert">{error}</p>}
          <div className={styles.actions}>
            <Button variant="ghost" onClick={() => setMode("accounts")} disabled={loading}>
              取消
            </Button>
            <Button type="submit" isLoading={loading}>
              登录并添加
            </Button>
          </div>
        </form>
      )}

      {mode === "reauth" && selected && (
        <form className={styles.form} onSubmit={submitReauth}>
          <div className={styles.selectedAccount}>
            <SwitcherAvatar account={selected} />
            <div>
              <strong>{selected.username}</strong>
              <p>{selected.masked_qq_email}</p>
            </div>
          </div>
          <PasswordField
            id="device-account-password"
            label="该账户密码"
            value={password}
            onChange={setPassword}
            required
            autoComplete="current-password"
            hint="密码只用于本次会话恢复，不会保存到浏览器。"
            error={error ?? undefined}
          />
          <div className={styles.actions}>
            <Button
              variant="ghost"
              onClick={() => {
                setMode("accounts");
                setSelected(null);
                setError(null);
              }}
              disabled={loading}
            >
              取消
            </Button>
            <Button type="submit" isLoading={loading}>
              确认并切换
            </Button>
          </div>
        </form>
      )}

      {(mode === "confirm-logout-current" || mode === "confirm-logout-all") && (
        <div className={styles.form}>
          <div className={styles.warning} role="alert">
            {mode === "confirm-logout-current"
              ? "当前账户会话将被撤销。"
              : "退出后，此设备上的全部账户会话都会被撤销，需要重新登录才能继续使用。"}
          </div>
          <div className={styles.actions}>
            <Button variant="ghost" onClick={() => setMode("accounts")} disabled={loading}>
              取消
            </Button>
            <Button
              variant="danger"
              onClick={mode === "confirm-logout-current" ? logoutCurrent : logoutAll}
              isLoading={loading}
            >
              {mode === "confirm-logout-current" ? "确认退出当前账户" : "确认退出全部账户"}
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
