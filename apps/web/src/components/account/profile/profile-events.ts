"use client";

/**
 * 画像工单 15：画像页内的变更与定位事件（沿用 `lib/recent-conversations.ts`
 * 的 window 事件约定，不引入全局状态库）。
 *
 * - 数据变化：条目新增/修改/删除、事项状态操作或使用开关切换成功后广播，
 *   整体概览据此立即重读——旧概览不再继续显示为当前内容，读取失败如实
 *   报错而不是保留旧内容。
 * - 定位条目：概览陈述被点击时携带条目标识广播，条目列表滚动到该条、
 *   展开依据并聚焦，作为「点击概览定位依据与编辑入口」的接缝。
 */

export const PROFILE_DATA_CHANGED_EVENT = "bridges:profile-data-changed";

export const PROFILE_ITEM_LOCATE_EVENT = "bridges:profile-item-locate";

export function onProfileDataChanged(listener: () => void): () => void {
  const handler = () => listener();
  window.addEventListener(PROFILE_DATA_CHANGED_EVENT, handler);
  return () => window.removeEventListener(PROFILE_DATA_CHANGED_EVENT, handler);
}

export function emitProfileDataChanged(): void {
  window.dispatchEvent(new Event(PROFILE_DATA_CHANGED_EVENT));
}

export function onProfileItemLocate(
  listener: (profileItemId: string) => void
): () => void {
  const handler = (event: Event) => {
    const detail = (event as CustomEvent<string>).detail;
    if (typeof detail === "string" && detail) listener(detail);
  };
  window.addEventListener(PROFILE_ITEM_LOCATE_EVENT, handler);
  return () => window.removeEventListener(PROFILE_ITEM_LOCATE_EVENT, handler);
}

export function emitProfileItemLocate(profileItemId: string): void {
  window.dispatchEvent(
    new CustomEvent<string>(PROFILE_ITEM_LOCATE_EVENT, { detail: profileItemId })
  );
}
