"use client";

import { Icon } from "@/components/design-system/Icon";
import {
  TiebaAnswer,
  TiebaCompositeEvidence,
} from "@/components/bridges/chat/TiebaAnswer";
import type {
  TiebaPostProjection,
  TiebaResearchProjection,
  TiebaSummaryCitation,
  TurnResultProjection,
} from "@/lib/api";
import type { ChatMessageView } from "@/lib/chat-message-view";
import { deliveredHumanizerText } from "@/lib/historical-results";

import { MarkdownBody } from "./MarkdownBody";

/** 只有纯贴吧回合才用贴吧总结替换服务端正文；复合回合必须保留综合回答。 */
export function isTiebaOnlyMessage(message: ChatMessageView): boolean {
  const result: TurnResultProjection | null | undefined = message.turnResult;
  if (!result) {
    // 流式早期还没有回合结果：其他模块投影已经到达就说明不是纯贴吧回合。
    return !(
      message.paperSearch ||
      message.learningResources ||
      message.githubProjects ||
      message.careerPlan ||
      message.commuteRoute ||
      message.webSearch ||
      message.arxivSearch ||
      message.retrieval ||
      message.teaching ||
      message.humanizer ||
      message.careerPlanning ||
      message.image ||
      message.video
    );
  }
  const moduleIds = new Set<string>();
  for (const moduleId of [
    result.requested_module_id,
    result.actual_module_id,
    ...(result.capability_list ?? []),
    ...(result.delivered ?? []).map((block) => block.module_id),
    ...(result.blocked ?? []).map((block) => block.module_id),
  ]) {
    if (moduleId) moduleIds.add(moduleId);
  }
  return moduleIds.size === 0 || (moduleIds.size === 1 && moduleIds.has("tieba"));
}

function PlainTextBody({ text }: { text: string }) {
  return (
    <p style={{ whiteSpace: "pre-wrap", overflowWrap: "break-word" }}>
      {text}
    </p>
  );
}

export function UserMessageBody({ message }: { message: ChatMessageView }) {
  if (message.body !== undefined) return <>{message.body}</>;
  return <PlainTextBody text={message.plainText} />;
}

export function MessageErrorAlert({
  text,
  testId,
}: {
  text: string;
  testId?: string;
}) {
  return (
    <div
      role="alert"
      data-testid={testId}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: "var(--space-2)",
        padding: "var(--space-3)",
        borderRadius: "var(--radius-md)",
        border: "1px solid var(--color-status-error)",
        backgroundColor: "var(--color-status-error-bg)",
        color: "var(--color-status-error)",
        overflowWrap: "break-word",
      }}
    >
      <Icon name="alert" size={18} aria-hidden />
      <span style={{ fontSize: "var(--text-sm)" }}>{text}</span>
    </div>
  );
}

export function AssistantBody({
  message,
  onOpenTiebaEvidence,
  onRetry,
}: {
  message: ChatMessageView;
  onOpenTiebaEvidence?: (
    citation: TiebaSummaryCitation,
    post: TiebaPostProjection | null,
    trigger: HTMLButtonElement,
    research: TiebaResearchProjection
  ) => void;
  onRetry?: (id: string) => void;
}) {
  const deliveredHumanizer = message.humanizer
    ? deliveredHumanizerText(message.humanizer)
    : null;
  const body =
    message.body !== undefined ? (
      <>{message.body}</>
    ) : (
      <MarkdownBody
        text={deliveredHumanizer ?? message.plainText}
        streaming={message.status === "streaming"}
      />
    );

  if (message.tiebaResearch) {
    // 纯贴吧回合：正文直接从已核验投影重建，服务端同一份纯文本总结
    // 不再重复渲染；复合回合保留服务端综合回答，只追加真实引用与依据。
    const tiebaOnly = isTiebaOnlyMessage(message);
    const genericError =
      message.status === "error" &&
      message.skill == null &&
      Boolean(message.errorText) &&
      (!tiebaOnly || !message.tiebaResearch.error_message);
    const errorAlert = genericError ? (
      <MessageErrorAlert text={message.errorText ?? ""} />
    ) : null;
    if (tiebaOnly) {
      return (
        <>
          <TiebaAnswer
            research={message.tiebaResearch}
            streaming={message.status === "streaming"}
            fallbackText={message.plainText}
            onOpenCitation={onOpenTiebaEvidence}
            onRetry={onRetry ? () => onRetry(message.id) : undefined}
            showRetry={message.turnResult == null}
          />
          {errorAlert}
        </>
      );
    }
    return (
      <>
        {body}
        <TiebaCompositeEvidence
          research={message.tiebaResearch}
          onOpenCitation={onOpenTiebaEvidence}
        />
        {errorAlert}
      </>
    );
  }

  if (message.status === "error" && message.skill == null) {
    return (
      <>
        {/* 部分交付的正文已经过提交门，例如总结失败前的判定反馈。 */}
        {message.turnResult?.outcome === "partial" && body}
        <MessageErrorAlert text={message.errorText ?? ""} />
      </>
    );
  }

  if (message.humanizer && deliveredHumanizer === null) {
    return (
      <MessageErrorAlert
        testId="humanizer-not-delivered"
        text={message.humanizer.error_message ?? "正文未交付"}
      />
    );
  }

  return body;
}
