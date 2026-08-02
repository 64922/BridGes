"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";

import type { StateKind } from "./StateBlock";

export type TemplateState = "normal" | StateKind;

const TEMPLATE_STATES: readonly TemplateState[] = [
  "normal",
  "loading",
  "empty",
  "error",
  "permission",
  "success",
  "recovery",
];

/**
 * 模板页面状态（仅设计基线模板使用）。
 *
 * 正式页面中状态由系统行为自动转换（打开对话→加载→正常、
 * 失败→重试→恢复、保存→成功），不提供可见的状态切换按钮；
 * 开发验收可通过 URL 参数 `?state=loading|empty|error|permission|success|recovery`
 * 直接落在指定状态。参数缺失或非法时使用 fallback。
 */
export function useTemplateState(fallback: TemplateState = "normal") {
  const searchParams = useSearchParams();
  const raw = searchParams.get("state");
  const initial = TEMPLATE_STATES.includes(raw as TemplateState)
    ? (raw as TemplateState)
    : fallback;
  return useState<TemplateState>(initial);
}
