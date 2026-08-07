"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { Menu } from "@/components/bridges/Menu";
import type { User } from "@/context/AuthContext";
import { useAuth } from "@/context/AuthContext";

import { AccountAvatar } from "./AccountAvatar";
import { AccountSwitcher } from "./AccountSwitcher";

function maskQqEmail(email: string): string {
  const [local = "", domain = "qq.com"] = email.split("@", 2);
  // 4 位及更短的 QQ 号只保留首位，避免"前 2 + 后 2"拼接还原完整号码。
  if (local.length <= 4) return `${local.slice(0, 1)}***@${domain}`;
  return `${local.slice(0, 2)}***${local.slice(-2)}@${domain}`;
}

interface AccountMenuProps {
  user: User;
  collapsed?: boolean;
  onNavigate?: () => void;
}

/** Production account menu with the exact Issue 08 action order. */
export function AccountMenu({ user, collapsed = false, onNavigate }: AccountMenuProps) {
  const router = useRouter();
  const { logout } = useAuth();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const closeSwitcher = useCallback(() => setSwitcherOpen(false), []);
  const maskedEmail = maskQqEmail(user.qq_email);

  const go = (href: string) => {
    setError(null);
    onNavigate?.();
    router.push(href);
  };

  const exitSession = async () => {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await logout();
      window.location.replace("/login?from=logout");
    } catch (cause) {
      setPending(false);
      setError(
        cause instanceof Error
          ? `无法退出当前账户：${cause.message}`
          : "无法退出当前账户，请检查连接后重试。"
      );
    }
  };

  return (
    <div data-testid="account-menu-region">
      <Menu
        ariaLabel={`账户菜单：${user.username}，${maskedEmail}，当前账户`}
        openUp
        trigger={
          <>
            <AccountAvatar account={user} size={36} />
            {!collapsed && (
              <span
                style={{
                  flex: 1,
                  minWidth: 0,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "flex-start",
                  lineHeight: 1.3,
                }}
              >
                <span
                  style={{
                    width: "100%",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    color: "var(--color-text-primary)",
                    fontWeight: 600,
                  }}
                >
                  {pending ? "正在退出…" : user.username}
                </span>
                <span
                  style={{
                    width: "100%",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    color: "var(--color-text-tertiary)",
                    fontSize: "var(--text-xs)",
                  }}
                >
                  {maskedEmail}
                </span>
              </span>
            )}
          </>
        }
        triggerStyle={{
          minHeight: collapsed ? "var(--target-size)" : "3.5rem",
          justifyContent: collapsed ? "center" : "flex-start",
          padding: collapsed ? "var(--space-1)" : "var(--space-2)",
          color: "var(--color-text-primary)",
        }}
        items={[
          {
            label: "切换账号",
            icon: "account",
            onSelect: () => {
              onNavigate?.();
              setSwitcherOpen(true);
            },
          },
          {
            label: "个人资料",
            icon: "profile",
            onSelect: () => go("/account/settings/profile"),
          },
          {
            label: "退出登录",
            icon: "close",
            danger: true,
            onSelect: () => void exitSession(),
          },
        ]}
      />
      {error && (
        <p
          role="alert"
          style={{
            marginTop: "var(--space-2)",
            color: "var(--color-status-error)",
            fontSize: "var(--text-xs)",
            lineHeight: "var(--line-height-normal)",
          }}
        >
          {error}
        </p>
      )}
      <AccountSwitcher open={switcherOpen} onClose={closeSwitcher} />
    </div>
  );
}
