"use client";

import { useEffect, useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { FormField } from "@/components/bridges/FormField";
import { StateBlock } from "@/components/bridges/StateBlock";
import { StateSwitcher, type TemplateState } from "@/components/bridges/StateSwitcher";
import { TemplateShell } from "@/components/bridges/TemplateShell";
import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";

type Theme = "light" | "dark";

const PROFILE_STORAGE_KEY = "bridges-template-profile";
const THEME_STORAGE_KEY = "bridges-template-theme";
const DEFAULT_USERNAME = "示例账户";
const DEFAULT_QQ_EMAIL = "123456@qq.com";

/**
 * 设置页桌面模板：分区卡片（个人资料 / 外观 / 账户与安全 / 危险区）。
 *
 * 主题切换是真实功能：写入 <html data-theme> 并持久化到 localStorage；
 * 危险操作通过模态对话框确认（Esc 关闭、焦点归还）。
 * 状态：正常 / 加载中 / 空 / 错误 / 未登录。
 */
export function SettingsTemplate() {
  const [state, setState] = useState<TemplateState>("normal");
  const [theme, setTheme] = useState<Theme>("light");
  const [username, setUsername] = useState(DEFAULT_USERNAME);
  const [qqEmail, setQqEmail] = useState(DEFAULT_QQ_EMAIL);
  const [passwordDialogOpen, setPasswordDialogOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [passwordErrors, setPasswordErrors] = useState<Record<string, string>>({});
  const [passwordChanged, setPasswordChanged] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleted, setDeleted] = useState(false);

  useEffect(() => {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "dark" || stored === "light") {
      setTheme(stored);
      document.documentElement.dataset.theme = stored;
    }
    const storedProfile = window.localStorage.getItem(PROFILE_STORAGE_KEY);
    if (storedProfile) {
      try {
        const profile = JSON.parse(storedProfile) as { username?: unknown; qqEmail?: unknown };
        if (typeof profile.username === "string") setUsername(profile.username);
        if (typeof profile.qqEmail === "string") setQqEmail(profile.qqEmail);
      } catch {
        window.localStorage.removeItem(PROFILE_STORAGE_KEY);
      }
    }
  }, []);

  const applyTheme = (next: Theme) => {
    setTheme(next);
    document.documentElement.dataset.theme = next;
    window.localStorage.setItem(THEME_STORAGE_KEY, next);
  };

  const openPasswordDialog = () => {
    setCurrentPassword("");
    setNewPassword("");
    setPasswordErrors({});
    setPasswordChanged(false);
    setPasswordDialogOpen(true);
  };

  const submitPassword = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const errors: Record<string, string> = {};
    if (!currentPassword) errors["dialog-current-password"] = "请输入当前密码。";
    if (newPassword.length < 12) errors["dialog-new-password"] = "新密码至少需要 12 个字符。";
    setPasswordErrors(errors);

    const firstInvalid = Object.keys(errors)[0];
    if (firstInvalid) {
      requestAnimationFrame(() => document.getElementById(firstInvalid)?.focus());
      return;
    }

    setPasswordDialogOpen(false);
    setPasswordChanged(true);
  };

  const renderBody = () => {
    if (state === "loading") {
      return <StateBlock kind="loading" title="正在加载设置…" />;
    }
    if (state === "error") {
      return (
        <StateBlock
          kind="error"
          title="设置加载失败"
          description="读取账户设置时出现异常，请重试。"
          actionLabel="重试"
          onAction={() => setState("recovery")}
        />
      );
    }
    if (state === "permission") {
      return (
        <StateBlock
          kind="permission"
          title="需要登录"
          description="设置属于你的个人账户，登录后才能查看和修改。"
          actionLabel="前往登录"
          onAction={() => {
            window.location.href = "/templates/login";
          }}
        />
      );
    }
    if (state === "empty") {
      return (
        <StateBlock
          kind="empty"
          title="暂无可配置项"
          description="当前账户还没有产生可配置的设置项，完成一次对话后再来看看。"
        />
      );
    }
    if (state === "success") {
      return (
        <StateBlock
          kind="success"
          title="设置已保存"
          description="个人资料和主题已写入浏览器本地存储。"
          actionLabel="返回设置"
          onAction={() => setState("normal")}
        />
      );
    }
    if (state === "recovery") {
      return (
        <StateBlock
          kind="recovery"
          title="设置已恢复"
          description="加载错误已清除，可以重新修改设置。"
          actionLabel="返回设置"
          onAction={() => setState("normal")}
        />
      );
    }
    return (
      <>
        <section aria-labelledby="profile-heading" className="sc-card">
          <h2 id="profile-heading" className="sc-section-title">
            个人资料
          </h2>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              window.localStorage.setItem(
                PROFILE_STORAGE_KEY,
                JSON.stringify({ username: username.trim(), qqEmail: qqEmail.trim() }),
              );
              setState("success");
            }}
            style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)", maxWidth: "24rem" }}
          >
            <FormField id="settings-username" label="用户名" value={username} onChange={setUsername} hint="可随时修改，大小写不敏感唯一。" required />
            <FormField id="settings-qq" label="QQ 邮箱" type="email" value={qqEmail} onChange={setQqEmail} hint="用于登录与接收任务提醒。" required />
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
              <Button type="submit" variant="primary" aria-label="保存个人资料">
                保存
              </Button>
            </div>
          </form>
        </section>

        <section aria-labelledby="appearance-heading" className="sc-card">
          <h2 id="appearance-heading" className="sc-section-title">
            外观
          </h2>
          <fieldset style={{ border: "none", display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
            <legend style={{ fontSize: "var(--text-sm)", color: "var(--color-text-secondary)", marginBottom: "var(--space-2)" }}>
              主题（立即生效并记住选择）
            </legend>
            {(
              [
                { value: "light", label: "浅色 — 温暖纸面" },
                { value: "dark", label: "深色 — 暖墨夜色" },
              ] as { value: Theme; label: string }[]
            ).map((option) => (
              <label
                key={option.value}
                style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", minHeight: "2.5rem", cursor: "pointer" }}
              >
                <input
                  type="radio"
                  name="theme"
                  value={option.value}
                  checked={theme === option.value}
                  onChange={() => applyTheme(option.value)}
                  style={{ width: "1.125rem", height: "1.125rem" }}
                />
                {option.label}
              </label>
            ))}
          </fieldset>
        </section>

        <section aria-labelledby="security-heading" className="sc-card">
          <h2 id="security-heading" className="sc-section-title">
            账户与安全
          </h2>
          <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)", marginBottom: "var(--space-4)" }}>
            此开发模板用于验证修改密码所需的字段校验、错误定位与键盘焦点流程。
          </p>
          <Button variant="secondary" aria-label="验证密码表单" onClick={openPasswordDialog}>
            验证密码表单
          </Button>
          {passwordChanged && (
            <span
              role="status"
              style={{ marginLeft: "var(--space-3)", color: "var(--color-status-success)", fontSize: "var(--text-sm)" }}
            >
              密码表单校验通过；未调用账户服务。
            </span>
          )}
        </section>

        <section
          aria-labelledby="danger-heading"
          className="sc-card"
          style={{ borderColor: "var(--color-status-error)" }}
        >
          <h2 id="danger-heading" className="sc-section-title" style={{ color: "var(--color-status-error)" }}>
            危险区
          </h2>
          <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)", marginBottom: "var(--space-4)" }}>
            清除该开发模板写入浏览器的个人资料与主题，不会触碰真实账户数据。
          </p>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
            <Button variant="danger" aria-label="清除本机模板数据" onClick={() => setDeleteDialogOpen(true)}>
              <Icon name="alert" size={16} aria-hidden />
              清除本机模板数据
            </Button>
            {deleted && (
              <span role="status" style={{ color: "var(--color-status-error)", fontSize: "var(--text-sm)" }}>
                已清除本机模板数据
              </span>
            )}
          </div>
        </section>

        <Dialog
          open={passwordDialogOpen}
          onClose={() => setPasswordDialogOpen(false)}
          title="验证密码表单"
          description="检查密码表单的填写、错误定位与键盘焦点行为；此开发模板不调用账户服务。"
        >
          <form
            onSubmit={submitPassword}
            style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}
          >
            <FormField
              id="dialog-current-password"
              label="当前密码"
              type="password"
              value={currentPassword}
              onChange={setCurrentPassword}
              error={passwordErrors["dialog-current-password"]}
              autoComplete="current-password"
            />
            <FormField
              id="dialog-new-password"
              label="新密码"
              type="password"
              value={newPassword}
              onChange={setNewPassword}
              error={passwordErrors["dialog-new-password"]}
              autoComplete="new-password"
              hint="至少 12 个字符。"
            />
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)" }}>
              <Button variant="ghost" aria-label="取消验证密码表单" onClick={() => setPasswordDialogOpen(false)}>
                取消
              </Button>
              <Button type="submit" variant="primary" aria-label="验证密码表单">
                验证表单
              </Button>
            </div>
          </form>
        </Dialog>

        <Dialog
          open={deleteDialogOpen}
          onClose={() => setDeleteDialogOpen(false)}
          title="确认清除本机模板数据？"
          description="该操作只会删除此开发模板写入浏览器的个人资料与主题，并恢复默认外观。"
        >
          <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)" }}>
            <Button variant="ghost" aria-label="取消清除模板数据" onClick={() => setDeleteDialogOpen(false)}>
              取消
            </Button>
            <Button
              variant="danger"
              aria-label="确认清除本机模板数据"
              onClick={() => {
                window.localStorage.removeItem(PROFILE_STORAGE_KEY);
                window.localStorage.removeItem(THEME_STORAGE_KEY);
                setUsername(DEFAULT_USERNAME);
                setQqEmail(DEFAULT_QQ_EMAIL);
                setTheme("light");
                document.documentElement.dataset.theme = "light";
                setDeleteDialogOpen(false);
                setDeleted(true);
              }}
            >
              确认清除
            </Button>
          </div>
        </Dialog>
      </>
    );
  };

  return (
    <TemplateShell>
      <div
        style={{
          width: "100%",
          maxWidth: "var(--chat-column-width)",
          margin: "0 auto",
          padding: "var(--space-6)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-4)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: "var(--space-3)",
            flexWrap: "wrap",
          }}
        >
          <div>
            <h1 style={{ fontSize: "var(--text-2xl)" }}>设置</h1>
            <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
              个人资料、外观、账户安全与数据管理。
            </p>
          </div>
          <StateSwitcher value={state} onChange={setState} />
        </div>

        {renderBody()}
      </div>
    </TemplateShell>
  );
}
