"use client";

import { BrandLogo } from "@/components/bridges/BrandLogo";
import { RetrievalCard } from "@/components/bridges/RetrievalCard";
import type {
  ChatModuleId,
  CitationProjection,
  TiebaPostProjection,
  TiebaSummaryCitation,
} from "@/lib/api";

import { AssistantActions } from "./AssistantActions";
import { AttemptHistory } from "./AttemptHistory";
import { DeliveryStatus } from "./DeliveryStatus";
import { AssistantBody } from "./MessageBody";
import { MediaTaskCards, SourceProjectionCards } from "./ProjectionCards";
import { PublicProcess } from "./PublicProcess";
import type { ChatMessageView } from "@/lib/chat-message-view";

export function AssistantMessage({
  message,
  conversationId,
  onRetry,
  onStop,
  onTeachingSkip,
  onTeachingBeginnerStart,
  onUseModuleSuggestion,
  onRefreshMessages,
  onOpenCitation,
  onOpenTiebaEvidence,
}: {
  message: ChatMessageView;
  conversationId?: string;
  onRetry?: (id: string) => void;
  onStop?: () => void;
  onTeachingSkip?: (messageId: string) => void;
  onTeachingBeginnerStart?: (messageId: string) => void;
  onUseModuleSuggestion?: (messageId: string, moduleId: ChatModuleId) => void;
  onRefreshMessages?: () => void;
  onOpenCitation?: (
    messageId: string,
    citation: CitationProjection,
    trigger: HTMLButtonElement
  ) => void;
  /** 打开贴吧总结引用的右侧帖子资料面板 */
  onOpenTiebaEvidence?: (
    messageId: string,
    citation: TiebaSummaryCitation,
    post: TiebaPostProjection | null,
    trigger: HTMLButtonElement
  ) => void;
}) {
  return (
    <article
      data-testid="assistant-message"
      style={{ display: "flex", gap: "var(--space-3)", minWidth: 0 }}
    >
      <span style={{ flexShrink: 0, paddingTop: "var(--space-1)" }} aria-hidden="true">
        <BrandLogo variant="icon" width={24} />
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <p
          style={{
            fontWeight: 600,
            fontSize: "var(--text-sm)",
            color: "var(--color-text-secondary)",
            marginBottom: "var(--space-1)",
          }}
        >
          BridGes
          {message.attemptNumber != null && message.attemptNumber > 1 && (
            <span
              data-testid="assistant-attempt-number"
              style={{
                marginLeft: "var(--space-2)",
                fontWeight: 400,
                color: "var(--color-text-tertiary)",
              }}
            >
              第 {message.attemptNumber} 次尝试
            </span>
          )}
        </p>

        {message.previousAttempts && message.previousAttempts.length > 0 && (
          <AttemptHistory attempts={message.previousAttempts} />
        )}

        <SourceProjectionCards
          message={message}
          conversationId={conversationId}
          onRetry={onRetry}
          onStop={onStop}
          onTeachingSkip={onTeachingSkip}
          onTeachingBeginnerStart={onTeachingBeginnerStart}
          onUseModuleSuggestion={onUseModuleSuggestion}
        />

        <PublicProcess message={message} conversationId={conversationId} />

        <AssistantBody
          message={message}
          onRetry={onRetry}
          onOpenTiebaEvidence={
            onOpenTiebaEvidence
              ? (citation, post, trigger) =>
                  onOpenTiebaEvidence(message.id, citation, post, trigger)
              : undefined
          }
        />

        <RetrievalCard
          retrieval={message.retrieval ?? null}
          onOpenCitation={(citation, trigger) =>
            onOpenCitation?.(message.id, citation, trigger)
          }
        />

        <DeliveryStatus message={message} onRetry={onRetry} />

        <AssistantActions message={message} onRetry={onRetry} />

        <MediaTaskCards
          message={message}
          conversationId={conversationId}
          onRefreshMessages={onRefreshMessages}
        />
      </div>
    </article>
  );
}
