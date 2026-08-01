"use client";

import { useEffect, useId, useRef, useState } from "react";

import { Icon, type IconName } from "@/components/design-system/Icon";

export interface MenuItem {
  label: string;
  icon?: IconName;
  onSelect?: () => void;
  danger?: boolean;
}

interface MenuProps {
  trigger: React.ReactNode;
  ariaLabel: string;
  items: MenuItem[];
  openUp?: boolean;
  triggerStyle?: React.CSSProperties;
}

/**
 * 无障碍弹出菜单（WAI-ARIA menu 模式）。
 *
 * - 触发按钮：aria-haspopup="menu"，Enter / Space / ↓ 打开并聚焦首项，↑ 聚焦末项
 * - 菜单内：↑/↓ 循环、Home/End 跳首尾、Esc 关闭并把焦点还给触发按钮、Tab 关闭
 * - 点击菜单外区域关闭；激活菜单项后关闭并归还焦点
 */
export function Menu({ trigger, ariaLabel, items, openUp = false, triggerStyle }: MenuProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const close = (returnFocus: boolean) => {
    setOpen(false);
    setActiveIndex(-1);
    if (returnFocus) {
      triggerRef.current?.focus();
    }
  };

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !triggerRef.current?.contains(target)) {
        close(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  useEffect(() => {
    if (!open || activeIndex < 0) return;
    const elements = menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]');
    elements?.[activeIndex]?.focus();
  }, [open, activeIndex]);

  const onTriggerKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex(0);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex(items.length - 1);
    }
  };

  const onMenuKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((i) => (i + 1) % items.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((i) => (i - 1 + items.length) % items.length);
    } else if (event.key === "Home") {
      event.preventDefault();
      setActiveIndex(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActiveIndex(items.length - 1);
    } else if (event.key === "Tab") {
      close(false);
    }
  };

  return (
    <div style={{ position: "relative" }}>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={ariaLabel}
        onClick={() => {
          if (open) {
            close(true);
          } else {
            setOpen(true);
            setActiveIndex(0);
          }
        }}
        onKeyDown={onTriggerKeyDown}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          width: "100%",
          minHeight: "var(--target-size)",
          padding: "var(--space-2) var(--space-3)",
          border: "1px solid transparent",
          borderRadius: "var(--radius-md)",
          backgroundColor: "transparent",
          color: "var(--color-text-secondary)",
          fontSize: "var(--text-sm)",
          cursor: "pointer",
          ...triggerStyle,
        }}
      >
        {trigger}
      </button>

      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={ariaLabel}
          onKeyDown={onMenuKeyDown}
          style={{
            position: "absolute",
            left: 0,
            minWidth: "12rem",
            zIndex: 40,
            ...(openUp
              ? { bottom: "calc(100% + var(--space-1))" }
              : { top: "calc(100% + var(--space-1))" }),
            backgroundColor: "var(--color-surface)",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-lg)",
            boxShadow: "var(--shadow-lg)",
            padding: "var(--space-1)",
          }}
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              tabIndex={-1}
              onClick={() => {
                item.onSelect?.();
                close(true);
              }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-2)",
                width: "100%",
                minHeight: "var(--target-size)",
                padding: "var(--space-2) var(--space-3)",
                border: "none",
                borderRadius: "var(--radius-md)",
                backgroundColor: "transparent",
                color: item.danger ? "var(--color-status-error)" : "var(--color-text-primary)",
                fontSize: "var(--text-sm)",
                textAlign: "left",
                cursor: "pointer",
              }}
            >
              {item.icon && <Icon name={item.icon} size={18} aria-hidden />}
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
