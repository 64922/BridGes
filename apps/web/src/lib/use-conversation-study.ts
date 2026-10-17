"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  correctStudyRecord,
  deleteStudyRecord,
  getStudyRestartOptions,
  restartStudyConversation,
  retryStudyDiagnosis,
  type ChatConversationProjection,
  type StudyRestartOptionsProjection,
} from "@/lib/api";
import { CHAT_LIST_CHANGED_EVENT } from "@/lib/recent-conversations";

interface Router {
  push(href: string): void;
}

interface UseConversationStudyOptions {
  conversationId: string;
  load: (keepContent?: boolean) => Promise<ChatConversationProjection | null>;
  reportError: (message: string | null) => void;
  announce: (message: string | null) => void;
  router: Router;
}

/**
 * 学习模式的诊断重试、记录纠正/删除与旧会话续学操作。
 *
 * 这些操作沿用既有服务与幂等键，不参与对话运行的流式状态机。
 */
export function useConversationStudy({
  conversationId,
  load,
  reportError,
  announce,
  router,
}: UseConversationStudyOptions) {
  const [restartOptions, setRestartOptions] =
    useState<StudyRestartOptionsProjection | null>(null);
  const [restartOptionsLoading, setRestartOptionsLoading] = useState(false);
  const [restartOptionsError, setRestartOptionsError] = useState<string | null>(null);
  const [restartSubmitting, setRestartSubmitting] = useState(false);
  const [restartError, setRestartError] = useState<string | null>(null);
  const [restartRemovedNotes, setRestartRemovedNotes] = useState<string[]>([]);
  const restartIdempotencyRef = useRef<{ signature: string; key: string } | null>(null);

  useEffect(() => {
    setRestartOptions(null);
    setRestartOptionsError(null);
    setRestartError(null);
    setRestartRemovedNotes([]);
    restartIdempotencyRef.current = null;
  }, [conversationId]);

  const retryDiagnosis = useCallback(
    async (taskId: string) => {
      try {
        await retryStudyDiagnosis(conversationId, taskId);
        await load(true);
        announce("已重新排队核验");
      } catch (error) {
        reportError(error instanceof Error ? error.message : "重试核验失败。");
      }
    },
    [announce, conversationId, load, reportError]
  );

  const correctRecord = useCallback(
    async (recordId: string, note: string, expectedVersion: number) => {
      reportError(null);
      try {
        await correctStudyRecord(conversationId, recordId, {
          note,
          expected_version: expectedVersion,
        });
        await load(true);
        announce("已保存纠正，原核验结论不再沿用");
        return true;
      } catch (error) {
        reportError(
          error instanceof Error ? error.message : "保存纠正失败，请稍后再试。"
        );
        await load(true);
        return false;
      }
    },
    [announce, conversationId, load, reportError]
  );

  const deleteRecord = useCallback(
    async (recordId: string, expectedVersion: number) => {
      reportError(null);
      try {
        await deleteStudyRecord(conversationId, recordId, expectedVersion);
        await load(true);
        announce("已删除学习记录");
      } catch (error) {
        reportError(
          error instanceof Error ? error.message : "删除记录失败，请稍后再试。"
        );
        await load(true);
      }
    },
    [announce, conversationId, load, reportError]
  );

  const loadRestartOptions = useCallback(async () => {
    setRestartOptionsLoading(true);
    setRestartOptionsError(null);
    try {
      const options = await getStudyRestartOptions(conversationId);
      setRestartOptions(options);
      return options;
    } catch (error) {
      setRestartOptionsError(
        error instanceof Error ? error.message : "读取续学选项失败，请稍后重试。"
      );
      return null;
    } finally {
      setRestartOptionsLoading(false);
    }
  }, [conversationId]);

  const restartStudy = useCallback(
    async (selection: {
      goalId: string | null;
      objectIds: string[];
      message: string;
    }) => {
      setRestartSubmitting(true);
      setRestartError(null);
      setRestartRemovedNotes([]);
      const signature = JSON.stringify([
        selection.goalId,
        [...selection.objectIds].sort(),
        selection.message,
      ]);
      if (restartIdempotencyRef.current?.signature !== signature) {
        restartIdempotencyRef.current = {
          signature,
          key: crypto.randomUUID(),
        };
      }
      try {
        const firstTurn = await restartStudyConversation(conversationId, {
          idempotency_key: restartIdempotencyRef.current.key,
          goal_id: selection.goalId,
          object_ids: selection.objectIds,
          message: selection.message,
        });
        restartIdempotencyRef.current = null;
        setRestartOptions(null);
        window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
        router.push(`/chat/${firstTurn.conversation.conversation_id}`);
        return true;
      } catch (error) {
        const apiError = error instanceof ApiError ? error : null;
        setRestartError(
          error instanceof Error ? error.message : "创建新学习对话失败，请稍后重试。"
        );
        if (
          apiError?.code === "study_restart_goal_unavailable" ||
          apiError?.code === "attachment_not_found"
        ) {
          const previousGoalId = selection.goalId;
          const previousGoals = restartOptions?.goals ?? [];
          const previousMaterials = restartOptions?.materials ?? [];
          const refreshed = await loadRestartOptions();
          if (refreshed) {
            const notes: string[] = [];
            if (
              previousGoalId &&
              !(refreshed.goals ?? []).some((goal) => goal.goal_id === previousGoalId)
            ) {
              const statement = previousGoals.find(
                (goal) => goal.goal_id === previousGoalId
              )?.statement;
              notes.push(
                statement
                  ? `学习目标「${statement}」已不可用，已从选择中移除。`
                  : "所选学习目标已不可用，已从选择中移除。"
              );
            }
            const available = new Set(
              (refreshed.materials ?? []).map((material) => material.object_id)
            );
            for (const objectId of selection.objectIds) {
              if (available.has(objectId)) continue;
              const filename = previousMaterials.find(
                (material) => material.object_id === objectId
              )?.original_filename;
              notes.push(
                filename
                  ? `材料「${filename}」已不可访问，已从选择中移除。`
                  : "一份所选材料已不可访问，已从选择中移除。"
              );
            }
            setRestartRemovedNotes(notes);
          }
        }
        return false;
      } finally {
        setRestartSubmitting(false);
      }
    },
    [conversationId, loadRestartOptions, restartOptions, router]
  );

  return {
    restart: {
      options: restartOptions,
      optionsLoading: restartOptionsLoading,
      optionsError: restartOptionsError,
      submitting: restartSubmitting,
      submitError: restartError,
      removedNotes: restartRemovedNotes,
      onLoadOptions: () => void loadRestartOptions(),
      onSubmit: restartStudy,
    },
    retryDiagnosis,
    correctRecord,
    deleteRecord,
  };
}
