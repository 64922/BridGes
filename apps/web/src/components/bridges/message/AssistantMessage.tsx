"use client";

import { BrandLogo } from "@/components/bridges/BrandLogo";
import type { ChatModuleId } from "@/lib/api";

import { AssistantActions } from "./AssistantActions";
import { AttemptHistory } from "./AttemptHistory";
import { DeliveryStatus } from "./DeliveryStatus";
import { AssistantBody } from "./MessageBody";
import { MediaTaskCards, SourceProjectionCards } from "./ProjectionCards";
import { GenerationStageLine, ThinkingSummary } from "./PublicProcess";
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
}: {
  message: ChatMessageView;
  conversationId?: string;
  onRetry?: (id: string) => void;
  onStop?: () => void;
  onTeachingSkip?: (messageId: string) => void;
  onTeachingBeginnerStart?: (messageId: string) => void;
  onUseModuleSuggestion?: (messageId: string, moduleId: ChatModuleId) => void;
  onRefreshMessages?: () => void;
}) {
  const streaming = message.status === "streaming";

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

        {message.thinking && (
          <ThinkingSummary thinking={message.thinking} streaming={streaming} />
        )}

        <AssistantBody message={message} conversationId={conversationId} />

        <DeliveryStatus message={message} onRetry={onRetry} />

        <GenerationStageLine message={message} />

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
