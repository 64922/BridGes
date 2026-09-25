"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { Icon, type IconName } from "@/components/design-system/Icon";

export interface MenuItem {
  label: string;
  icon?: IconName;
  /** 可选的中文补充说明（第二行小字；仍属菜单项名称的一部分）。 */
  description?: string;
  onSelect?: () => void;
  danger?: boolean;
  returnFocus?: boolean;
}

interface MenuProps {
  trigger: React.ReactNode;
  ariaLabel: string;
  items: MenuItem[];
  openUp?: boolean;
  triggerStyle?: React.CSSProperties;
}

const VIEWPORT_MARGIN = 8;
const MENU_GAP = 8;

interface MenuPosition {
  top: number;
  left: number;
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
  const [positioned, setPositioned] = useState(false);
  const [position, setPosition] = useState<MenuPosition>({ top: 0, left: 0 });
  const [portalRoot, setPortalRoot] = useState<HTMLElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const getMenuItems = useCallback(
    () => menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]'),
    []
  );

  useEffect(() => {
    setPortalRoot(document.body);
  }, []);

  const close = useCallback((returnFocus: boolean) => {
    setOpen(false);
    setActiveIndex(-1);
    setPositioned(false);
    if (returnFocus && typeof window !== "undefined") {
      const restoreFocus = () => triggerRef.current?.focus();
      if (typeof window.requestAnimationFrame === "function") {
        window.requestAnimationFrame(restoreFocus);
      } else {
        restoreFocus();
      }
    }
  }, []);

  const positionMenu = useCallback(() => {
    const triggerElement = triggerRef.current;
    const menuElement = menuRef.current;
    if (!open || !triggerElement || !menuElement) return;

    const triggerRect = triggerElement.getBoundingClientRect();
    const menuRect = menuElement.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    let left = triggerRect.left;
    if (left + menuRect.width > viewportWidth - VIEWPORT_MARGIN) {
      left = triggerRect.right - menuRect.width;
    }
    const maxLeft = Math.max(VIEWPORT_MARGIN, viewportWidth - menuRect.width - VIEWPORT_MARGIN);
    left = Math.min(Math.max(left, VIEWPORT_MARGIN), maxLeft);

    const preferredTop = openUp
      ? triggerRect.top - menuRect.height - MENU_GAP
      : triggerRect.bottom + MENU_GAP;
    const preferredFits = openUp
      ? preferredTop >= VIEWPORT_MARGIN
      : preferredTop + menuRect.height <= viewportHeight - VIEWPORT_MARGIN;
    const fallbackTop = openUp
      ? triggerRect.bottom + MENU_GAP
      : triggerRect.top - menuRect.height - MENU_GAP;
    const top = preferredFits ? preferredTop : fallbackTop;
    const maxTop = Math.max(VIEWPORT_MARGIN, viewportHeight - menuRect.height - VIEWPORT_MARGIN);

    setPosition({
      left,
      top: Math.min(Math.max(top, VIEWPORT_MARGIN), maxTop),
    });
    setPositioned(true);
  }, [open, openUp]);

  const openMenu = (nextIndex: number) => {
    setPositioned(false);
    setOpen(true);
    setActiveIndex(nextIndex);
  };

  useEffect(() => {
    if (!open || !portalRoot) return;

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !triggerRef.current?.contains(target)) {
        close(true);
      }
    };

    const updatePosition = () => positionMenu();
    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("scroll", updatePosition, true);
    window.addEventListener("resize", updatePosition);
    window.visualViewport?.addEventListener("scroll", updatePosition);
    window.visualViewport?.addEventListener("resize", updatePosition);

    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updatePosition);
    resizeObserver?.observe(triggerRef.current as Element);
    resizeObserver?.observe(menuRef.current as Element);
    updatePosition();

    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("scroll", updatePosition, true);
      window.removeEventListener("resize", updatePosition);
      window.visualViewport?.removeEventListener("scroll", updatePosition);
      window.visualViewport?.removeEventListener("resize", updatePosition);
      resizeObserver?.disconnect();
    };
  }, [close, open, portalRoot, positionMenu]);

  useEffect(() => {
    if (!open || !portalRoot || activeIndex < 0) return;
    const elements = getMenuItems();
    elements?.[activeIndex]?.focus();
  }, [activeIndex, getMenuItems, open, portalRoot, positioned]);

  const onTriggerKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openMenu(0);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      openMenu(items.length - 1);
    }
  };

  const onMenuKeyDown = (event: React.KeyboardEvent) => {
    if (items.length === 0) return;
    const focusItem = (index: number) => {
      setActiveIndex(index);
      getMenuItems()?.[index]?.focus();
    };

    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      focusItem((activeIndex + 1) % items.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      focusItem((activeIndex - 1 + items.length) % items.length);
    } else if (event.key === "Home") {
      event.preventDefault();
      focusItem(0);
    } else if (event.key === "End") {
      event.preventDefault();
      focusItem(items.length - 1);
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
            openMenu(0);
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

      {open &&
        portalRoot &&
        createPortal(
          <div
            ref={menuRef}
            id={menuId}
            role="menu"
            aria-label={ariaLabel}
            onKeyDown={onMenuKeyDown}
            style={{
              position: "fixed",
              top: position.top,
              left: position.left,
              visibility: "visible",
              opacity: positioned ? 1 : 0,
              pointerEvents: positioned ? "auto" : "none",
              minWidth: "12rem",
              maxWidth: "calc(100vw - 1rem)",
              maxHeight: "calc(100vh - 1rem)",
              zIndex: 60,
              backgroundColor: "var(--color-surface)",
              border: "1px solid var(--color-border)",
              borderRadius: "var(--radius-lg)",
              boxShadow: "var(--shadow-lg)",
              padding: "var(--space-1)",
              overflowX: "hidden",
              overflowY: "auto",
              boxSizing: "border-box",
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
                  if (item.returnFocus === false) {
                    // returnFocus:false 通常表示「随后打开模态框」：菜单面板
                    // 卸载会把焦点丢到 body，Dialog 打开时捕获的「归还目标」
                    // 就会变成 body。这里同步把焦点还给触发按钮（触发按钮
                    // 不在面板内、不会卸载），Dialog 因此捕获到正确的 Escape
                    // 归还目标（Issue 38 AC5）。对不打开对话框的项（如 Composer
                    // 工具前缀插入），焦点落到触发按钮也是合理兜底——比丢到
                    // body 更好，且调用方可用默认 returnFocus 保持既有行为。
                    triggerRef.current?.focus();
                  }
                  close(item.returnFocus !== false);
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
                  transition:
                    "background-color var(--motion-duration-fast) var(--motion-easing)",
                }}
              >
                {item.icon && <Icon name={item.icon} size={18} aria-hidden />}
                {item.description ? (
                  <span style={{ display: "grid", gap: "0.0625rem" }}>
                    <span>{item.label}</span>
                    <small
                      style={{
                        color: "var(--color-text-tertiary)",
                        fontSize: "var(--text-xs)",
                        fontWeight: 400,
                      }}
                    >
                      {item.description}
                    </small>
                  </span>
                ) : (
                  item.label
                )}
              </button>
            ))}
          </div>,
          portalRoot
        )}
    </div>
  );
}
