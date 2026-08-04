"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "@/context/AuthContext";
import {
  listLearningProjects,
  updateChatConversationProject,
  type LearningProjectSummary,
} from "@/lib/api";
import { CHAT_LIST_CHANGED_EVENT } from "@/lib/recent-conversations";

/** 学习项目列表变更事件名：新建/改名/删除后派发，侧栏与选择器据此刷新。 */
export const LEARNING_PROJECTS_CHANGED_EVENT = "bridges:learning-projects-changed";

/**
 * 变更对话的学习项目归属并广播会话列表刷新（侧栏与对话页共用入口）。
 *
 * 传入项目标识移入项目，显式传 null 移出项目；成功时派发
 * `CHAT_LIST_CHANGED_EVENT`。失败时错误原样抛出，由各调用点按既有
 * 错误展示约定处理（侧栏恢复列表、对话页错误横幅）。
 */
export async function changeConversationLearningProject(
  conversationId: string,
  projectId: string | null
): Promise<void> {
  await updateChatConversationProject(conversationId, projectId);
  window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
}

/**
 * 学习项目列表数据（真实 API）。
 *
 * 数据来自 `listLearningProjects()`；账户切换时先清空旧快照，再按账户修订号
 * 重新拉取，不读取其他账户的项目。列表随 `bridges:learning-projects-changed`
 * 事件刷新（新建/改名/删除学习项目后由发起方派发）。
 */
export function useLearningProjects() {
  const { accountRevision, authState } = useAuth();
  const [projects, setProjects] = useState<LearningProjectSummary[]>([]);
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);
  const [viewRevision, setViewRevision] = useState<number | null>(null);
  const requestIdRef = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    const requestedRevision = accountRevision;
    setLoading(true);
    setLoadError("");
    setViewRevision(requestedRevision);
    try {
      const list = await listLearningProjects();
      if (requestId === requestIdRef.current) {
        setProjects(list);
      }
    } catch (error) {
      if (requestId === requestIdRef.current) {
        setLoadError(error instanceof Error ? error.message : "学习项目列表加载失败。");
      }
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  }, [accountRevision]);

  useEffect(() => {
    // 账户切换与退出登录时先清空旧账户快照，避免异步请求返回后短暂泄漏
    // 上一个账户的列表；新账户认证完成后再加载其真实投影。
    setProjects([]);
    setViewRevision(null);
    if (authState === "authenticated") void load();
    else if (authState === "unauthenticated" || authState === "error") setLoading(false);
    window.addEventListener(LEARNING_PROJECTS_CHANGED_EVENT, load);
    return () => window.removeEventListener(LEARNING_PROJECTS_CHANGED_EVENT, load);
  }, [accountRevision, authState, load]);

  const hasCurrentAccountView =
    authState === "authenticated" && viewRevision === accountRevision;
  return {
    projects: hasCurrentAccountView ? projects : [],
    loading: loading || (authState === "authenticated" && !hasCurrentAccountView),
    loadError: hasCurrentAccountView ? loadError : "",
    reload: load,
  };
}
