"use client";

/**
 * 统一搜索页（/search）的焦点归还通道（Issue 24）。
 *
 * 全局 Ctrl/Cmd+K 打开搜索页前记录触发元素；搜索页经 Esc 返回原上下文后
 * 把焦点归还给该元素（仍存在于文档中时）。模块级单例即可——同一时刻
 * 只有一次「打开搜索」的触发点。
 */

let returnFocusElement: HTMLElement | null = null;

export function saveSearchReturnFocus(element: HTMLElement | null): void {
  returnFocusElement = element;
}

export function hasSearchReturnFocus(): boolean {
  return returnFocusElement !== null;
}

export function restoreSearchReturnFocus(): void {
  const element = returnFocusElement;
  returnFocusElement = null;
  if (element && element.isConnected) {
    element.focus();
  }
}
