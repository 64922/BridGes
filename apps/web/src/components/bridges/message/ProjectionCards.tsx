"use client";

import { CareerPlanningProcessCard } from "@/components/bridges/CareerPlanningProcessCard";
import { CareerPlanningResultCard } from "@/components/bridges/CareerPlanningResultCard";
import { HumanizerProcessCard } from "@/components/bridges/HumanizerProcessCard";
import { HumanizerResultCard } from "@/components/bridges/HumanizerResultCard";
import { TeachingCard } from "@/components/bridges/TeachingCard";
import { WebSearchCard } from "@/components/bridges/WebSearchCard";
import { ArxivPaperSearchCard } from "@/components/bridges/ArxivPaperSearchCard";
import { CareerPlanCard } from "@/components/bridges/chat/CareerPlanCard";
import { GithubProjectsCard } from "@/components/bridges/chat/GithubProjectsCard";
import { ImageMedia } from "@/components/bridges/chat/ImageMedia";
import { LearningResourcesCard } from "@/components/bridges/chat/LearningResourcesCard";
import { ModuleSuggestionCard } from "@/components/bridges/chat/ModuleSuggestionCard";
import { VideoMedia } from "@/components/bridges/chat/VideoMedia";
import { CAREER_INTENT_KEYWORDS, CAREER_INTENT_PREFIXES } from "@/lib/chat-tools";
import type { ChatModuleId } from "@/lib/api";
import type { ChatMediaTarget } from "@/lib/chat-media";

import type { ChatMessageView } from "@/lib/chat-message-view";

function isCareerMessage(message: ChatMessageView): boolean {
  return (
    CAREER_INTENT_PREFIXES.some((prefix) => message.plainText.startsWith(prefix)) ||
    CAREER_INTENT_KEYWORDS.some((keyword) => message.plainText.includes(keyword))
  );
}

/**
 * 回答正文上方的来源与过程投影。每个条件都是显式类型分支，
 * 不按任意字符串动态挂载组件；新增投影必须在这里登记职责。
 */
export function SourceProjectionCards({
  message,
  conversationId,
  onRetry,
  onStop,
  onTeachingSkip,
  onTeachingBeginnerStart,
  onUseModuleSuggestion,
}: {
  message: ChatMessageView;
  conversationId?: string;
  onRetry?: (id: string) => void;
  onStop?: () => void;
  onTeachingSkip?: (messageId: string) => void;
  onTeachingBeginnerStart?: (messageId: string) => void;
  onUseModuleSuggestion?: (messageId: string, moduleId: ChatModuleId) => void;
}) {
  if (!conversationId) return null;
  const streaming = message.status === "streaming";

  return (
    <>
      {message.teaching && (
        <TeachingCard
          teaching={message.teaching}
          onSkip={() => onTeachingSkip?.(message.id)}
          onRetry={() => onRetry?.(message.id)}
          onBeginnerStart={() => onTeachingBeginnerStart?.(message.id)}
        />
      )}

      <ArxivPaperSearchCard
        search={message.arxivSearch ?? null}
        streaming={streaming}
        onRetry={() => onRetry?.(message.id)}
        onCancel={onStop}
      />

      <CareerPlanCard
        plan={message.careerPlan ?? null}
        streaming={streaming}
        onRetry={() => onRetry?.(message.id)}
      />

      <GithubProjectsCard
        projects={message.githubProjects ?? null}
        streaming={streaming}
        onRetry={() => onRetry?.(message.id)}
      />

      <LearningResourcesCard
        resources={message.learningResources ?? null}
        streaming={streaming}
        onRetry={() => onRetry?.(message.id)}
      />

      {!streaming && (
        <ModuleSuggestionCard
          suggestion={message.moduleSuggestion ?? null}
          onUse={(suggestion) =>
            onUseModuleSuggestion?.(message.id, suggestion.module_id)
          }
        />
      )}

      <WebSearchCard
        search={message.webSearch ?? null}
        streaming={streaming}
        onRetry={() => onRetry?.(message.id)}
        onCancel={onStop}
      />

      {(message.humanizerProcess != null ||
        (message.humanizer == null &&
          streaming &&
          message.skill != null)) && (
        <HumanizerProcessCard
          data={message.humanizerProcess ?? null}
          streaming={streaming}
        />
      )}

      {message.humanizer != null && (
        <HumanizerResultCard result={message.humanizer} />
      )}

      {(message.careerProcess != null ||
        (message.careerPlanning == null &&
          streaming &&
          isCareerMessage(message))) && (
        <CareerPlanningProcessCard
          data={message.careerProcess ?? null}
          streaming={streaming}
          onRetry={() => onRetry?.(message.id)}
        />
      )}

      {message.careerPlanning != null && (
        <CareerPlanningResultCard
          result={message.careerPlanning}
          conversationId={conversationId}
          messageId={message.id}
          onRetry={() => onRetry?.(message.id)}
        />
      )}
    </>
  );
}

export function MediaTaskCards({
  message,
  conversationId,
  onRefreshMessages,
  onOpenMedia,
}: {
  message: ChatMessageView;
  conversationId?: string;
  onRefreshMessages?: () => void;
  onOpenMedia?: (
    messageId: string,
    target: ChatMediaTarget,
    trigger: HTMLButtonElement
  ) => void;
}) {
  if (!conversationId || message.status === "streaming") return null;
  return (
    <>
      {message.image && (
        <ImageMedia
          conversationId={conversationId}
          task={message.image}
          onRefreshMessages={onRefreshMessages}
          onOpenPreview={
            onOpenMedia && message.image.asset_id
              ? (trigger) =>
                  onOpenMedia(
                    message.id,
                    {
                      kind: "image",
                      taskId: message.image!.task_id,
                      assetId: message.image!.asset_id as string,
                      resultVersionId: message.image!.result_version_id ?? null,
                    },
                    trigger
                  )
              : undefined
          }
        />
      )}
      {message.video && (
        <VideoMedia
          conversationId={conversationId}
          task={message.video}
          onRefreshMessages={onRefreshMessages}
          onOpenPreview={
            onOpenMedia && message.video.asset_id
              ? (trigger) =>
                  onOpenMedia(
                    message.id,
                    {
                      kind: "video",
                      taskId: message.video!.task_id,
                      assetId: message.video!.asset_id as string,
                      resultVersionId: null,
                    },
                    trigger
                  )
              : undefined
          }
        />
      )}
    </>
  );
}
