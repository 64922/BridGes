"use client";

import { CareerPlanningProcessCard } from "@/components/bridges/CareerPlanningProcessCard";
import { CareerPlanningResultCard } from "@/components/bridges/CareerPlanningResultCard";
import { ContextNoteCard } from "@/components/bridges/ContextNoteCard";
import { HumanizerProcessCard } from "@/components/bridges/HumanizerProcessCard";
import { HumanizerResultCard } from "@/components/bridges/HumanizerResultCard";
import { TeachingCard } from "@/components/bridges/TeachingCard";
import { WebSearchCard } from "@/components/bridges/WebSearchCard";
import { ArxivPaperSearchCard } from "@/components/bridges/ArxivPaperSearchCard";
import { CareerPlanCard } from "@/components/bridges/chat/CareerPlanCard";
import { CommuteRouteCard } from "@/components/bridges/chat/CommuteRouteCard";
import { GithubProjectsCard } from "@/components/bridges/chat/GithubProjectsCard";
import { ImageTaskCard } from "@/components/bridges/chat/ImageTaskCard";
import { LearningResourcesCard } from "@/components/bridges/chat/LearningResourcesCard";
import { ModuleSuggestionCard } from "@/components/bridges/chat/ModuleSuggestionCard";
import { PaperSearchCard } from "@/components/bridges/chat/PaperSearchCard";
import { TiebaResearchCard } from "@/components/bridges/chat/TiebaResearchCard";
import { VideoTaskCard } from "@/components/bridges/chat/VideoTaskCard";
import { CAREER_INTENT_KEYWORDS, CAREER_INTENT_PREFIXES } from "@/lib/chat-tools";
import type { ChatModuleId } from "@/lib/api";

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
      {/* 本次上下文说明（画像切片/材料类别/用途披露）。
          生成中显示 loading 态；终态且无披露（历史消息/画像服务未挂载）
          不渲染卡片，避免永久 loading。 */}
      {(message.contextNote != null || streaming) && (
        <ContextNoteCard
          note={message.contextNote ?? null}
          streaming={streaming}
          conversationId={conversationId}
          messageId={message.id}
        />
      )}

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

      <PaperSearchCard
        search={message.paperSearch ?? null}
        streaming={streaming}
        onRetry={() => onRetry?.(message.id)}
      />

      <TiebaResearchCard
        research={message.tiebaResearch ?? null}
        streaming={streaming}
        onRetry={() => onRetry?.(message.id)}
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

      <CommuteRouteCard
        route={message.commuteRoute ?? null}
        streaming={streaming}
        onRetry={() => onRetry?.(message.id)}
        returnTo={`/chat/${conversationId}`}
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
}: {
  message: ChatMessageView;
  conversationId?: string;
  onRefreshMessages?: () => void;
}) {
  if (!conversationId || message.status === "streaming") return null;
  return (
    <>
      {message.image && (
        <ImageTaskCard
          conversationId={conversationId}
          task={message.image}
          onSucceeded={onRefreshMessages}
        />
      )}
      {message.video && (
        <VideoTaskCard
          conversationId={conversationId}
          task={message.video}
          onSucceeded={onRefreshMessages}
        />
      )}
    </>
  );
}
