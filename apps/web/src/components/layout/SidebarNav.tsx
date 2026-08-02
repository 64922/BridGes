"use client";

import { AccountMenu } from "@/components/account/AccountMenu";
import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { NavLink } from "@/components/design-system/NavLink";
import { useAuth } from "@/context/AuthContext";

import styles from "./SidebarNav.module.css";

interface SidebarNavProps {
  mode: "account" | "project";
  projectId?: string;
  open: boolean;
  onClose: () => void;
}

interface NavGroup {
  title: string;
  links: { href: string; label: string; icon?: React.ComponentProps<typeof Icon>["name"] }[];
}

export function SidebarNav({ mode, projectId, open, onClose }: SidebarNavProps) {
  const { user, authState, refreshSession } = useAuth();
  const projectPrefix = projectId ? `/projects/${projectId}` : "";

  const accountGroups: NavGroup[] = [
    {
      title: "伙伴与项目",
      links: [
        { href: "/account", label: "全局科学伙伴", icon: "user" },
        { href: "/account/projects", label: "科学项目空间", icon: "project" },
      ],
    },
    {
      title: "个人与系统",
      links: [
        { href: "/account/profile", label: "画像与记忆中心", icon: "user" },
        { href: "/account/eval", label: "评测与运行中心", icon: "settings" },
        { href: "/account/domain-packs", label: "领域包专家工作台", icon: "settings" },
        { href: "/account/settings", label: "设置、设备与同步", icon: "settings" },
      ],
    },
  ];

  const projectGroups: NavGroup[] = [
    {
      title: "项目工作台",
      links: [
        { href: projectPrefix || "#", label: "项目总览", icon: "project" },
        { href: `${projectPrefix}/learning`, label: "学习实验室", icon: "user" },
        { href: `${projectPrefix}/expression`, label: "科学表达工坊", icon: "project" },
        { href: `${projectPrefix}/multimedia`, label: "多模态科学实验室", icon: "project" },
        { href: `${projectPrefix}/evidence`, label: "证据与校验台", icon: "settings" },
      ],
    },
    {
      title: "项目治理",
      links: [
        { href: `${projectPrefix}/materials`, label: "项目材料与产物", icon: "project" },
        { href: `${projectPrefix}/settings`, label: "项目设置", icon: "settings" },
      ],
    },
  ];

  const groups = mode === "project" ? projectGroups : accountGroups;

  return (
    <>
      <div
        className={`${styles.backdrop} ${open ? styles.backdropVisible : ""}`}
        aria-hidden="true"
        onClick={onClose}
        data-testid="sidebar-backdrop"
      />
      <nav
        id="primary-navigation"
        aria-label="主导航"
        className={`${styles.nav} ${open ? styles.open : ""}`}
      >
        <div className={styles.closeButton}>
          <Button
            variant="ghost"
            size="sm"
            aria-label="关闭导航"
            onClick={onClose}
            data-testid="sidebar-close"
          >
            <Icon name="close" size={20} ariaLabel="关闭" />
          </Button>
        </div>

        {groups.map((group) => (
          <section key={group.title} className={styles.group}>
            <h2 className={styles.groupTitle}>{group.title}</h2>
            <ul role="list" style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
              {group.links.map((link) => (
                <li key={link.href}>
                  <NavLink href={link.href} onNavigate={onClose}>
                    {link.icon && <Icon name={link.icon} size={18} aria-hidden />}
                    {link.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </section>
        ))}

        <div className={styles.accountFooter}>
          {authState === "authenticated" && user ? (
            <AccountMenu user={user} onNavigate={onClose} />
          ) : authState === "loading" ? (
            <div className={styles.accountStatus} role="status" aria-live="polite">
              <span className={styles.accountSkeleton} aria-hidden="true" />
              <span>正在读取账户…</span>
            </div>
          ) : authState === "error" ? (
            <div className={styles.accountStatus}>
              <span>账户信息读取失败</span>
              <Button variant="ghost" size="sm" onClick={() => void refreshSession()}>
                重试
              </Button>
            </div>
          ) : (
            <div className={styles.accountStatus}>
              <span>需要重新登录</span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => window.location.replace("/login")}
              >
                去登录
              </Button>
            </div>
          )}
        </div>
      </nav>
    </>
  );
}
