import type { ChatThinking } from "@/lib/chat-message";
import {
  isChatStreamEventOf,
  type ArxivSearchProjection,
  type ChatAttachmentProjection,
  type ChatMessageProjection,
  type ChatStreamCareerData,
  type ChatStreamErrorData,
  type ChatStreamEvent,
  type ChatStreamHumanizerData,
  type ChatStreamImageData,
  type ChatStreamNodeData,
  type ChatStreamStageData,
  type ChatStreamStartedData,
  type ChatStreamVideoData,
  type TeachingTurnProjection,
  type TiebaResearchProjection,
  type TurnResultProjection,
  type WebSearchProjection,
} from "@/lib/api";

export type ActiveRunKind = "send" | "retry" | "resume";

export type ActiveRunStatus = "streaming" | "error" | "done" | "stopped";

export interface PendingUserMessage {
  id: string;
  text: string;
  attachments?: ChatAttachmentProjection[];
}

export interface ActiveRun {
  turnResult?: TurnResultProjection | null;
  messageId: string;
  /** 服务端持久化的助手尝试序号；缺失时不在进行中视图猜测。 */
  attemptNumber?: number;
  content: string;
  kind: ActiveRunKind;
  thinking: ChatThinking | null;
  webSearch: WebSearchProjection | null;
  arxivSearch: ArxivSearchProjection | null;
  teaching: TeachingTurnProjection | null;
  stage: ChatStreamStageData | null;
  node: ChatStreamNodeData | null;
  humanizerProcess: ChatStreamHumanizerData | null;
  careerProcess: ChatStreamCareerData | null;
  imageProcess: ChatStreamImageData | null;
  videoProcess: ChatStreamVideoData | null;
  /** 恢复运行时保留的贴吧投影：已读范围、降级说明与引用关系不丢失。 */
  tiebaResearch: TiebaResearchProjection | null;
  status: ActiveRunStatus;
  errorText?: string;
}

export function secondsOf(durationMs: number | null | undefined): number | null {
  if (durationMs === null || durationMs === undefined) return null;
  return Math.max(1, Math.round(durationMs / 1000));
}

export function activeRunFromAssistant(
  assistant: ChatMessageProjection,
  kind: ActiveRunKind,
  thinkingSeconds: number | null = null
): ActiveRun {
  // 恢复/重试时只使用服务端已持久化的真实阶段与节点；没有任何事件前
  // 保持通用状态，不合成一个“排队中”阶段。
  const persistedStage = assistant.active_run?.stage
    ? {
        kind: "stage" as const,
        message_id: assistant.message_id,
        stage: assistant.active_run.stage,
        status: "active" as const,
      }
    : null;
  const persistedNode =
    !persistedStage && assistant.active_run?.current_node
      ? {
          kind: "node" as const,
          message_id: assistant.message_id,
          node: assistant.active_run.current_node,
          status: "started" as const,
        }
      : null;
  return {
    messageId: assistant.message_id,
    attemptNumber: assistant.attempt_number,
    content: assistant.content ?? "",
    kind,
    status: "streaming",
    thinking: assistant.thinking
      ? {
          steps: assistant.thinking.steps ?? [],
          evidence: assistant.thinking.evidence ?? [],
          tools: assistant.thinking.tools ?? [],
          quality: assistant.thinking.quality ?? [],
          seconds: thinkingSeconds,
        }
      : null,
    webSearch: assistant.web_search ?? null,
    arxivSearch: assistant.arxiv_search ?? null,
    teaching: assistant.teaching ?? null,
    turnResult: assistant.turn_result ?? null,
    stage: persistedStage,
    node: persistedNode,
    humanizerProcess: null,
    careerProcess: null,
    imageProcess: null,
    videoProcess: null,
    tiebaResearch: assistant.tieba_research ?? null,
  };
}

export function activeRunFromStarted(
  started: ChatStreamStartedData,
  kind: ActiveRunKind
): ActiveRun {
  return {
    messageId: started.message_id,
    attemptNumber: started.attempt_number,
    content: "",
    kind,
    status: "streaming",
    thinking: started.thinking
      ? {
          steps: started.thinking.steps ?? [],
          evidence: started.thinking.evidence ?? [],
          tools: started.thinking.tools ?? [],
          quality: started.thinking.quality ?? [],
          seconds: null,
        }
      : null,
    webSearch: started.web_search ?? null,
    arxivSearch: started.arxiv_search ?? null,
    teaching: started.teaching ?? null,
    stage: null,
    node: null,
    humanizerProcess: null,
    careerProcess: null,
    imageProcess: null,
    videoProcess: null,
    tiebaResearch: null,
  };
}

export function activeRunFromError(
  current: ActiveRun,
  error: ChatStreamErrorData,
  kind: ActiveRunKind
): ActiveRun {
  return {
    messageId: error.message_id,
    attemptNumber: current.attemptNumber,
    content: current.content,
    kind,
    status: "error",
    errorText: error.error.message,
    thinking: error.thinking
      ? {
          steps: error.thinking.steps ?? [],
          evidence: error.thinking.evidence ?? [],
          tools: error.thinking.tools ?? [],
          quality: error.thinking.quality ?? [],
          seconds: secondsOf(error.duration_ms),
        }
      : null,
    webSearch: error.web_search ?? current.webSearch ?? null,
    arxivSearch: error.arxiv_search ?? current.arxivSearch ?? null,
    teaching: error.teaching ?? current.teaching ?? null,
    turnResult: current.turnResult ?? null,
    stage: current.stage ?? null,
    node: current.node ?? null,
    humanizerProcess: current.humanizerProcess ?? null,
    careerProcess: current.careerProcess ?? null,
    imageProcess: current.imageProcess ?? null,
    videoProcess: current.videoProcess ?? null,
    tiebaResearch: current.tiebaResearch ?? null,
  };
}

/** 把持续事件映射到进行中的运行视图；不匹配或已终态时保持原对象。 */
export function applyRunProgress(
  run: ActiveRun | null,
  event: ChatStreamEvent
): ActiveRun | null {
  if (!run || run.status !== "streaming") return run;

  if (isChatStreamEventOf(event, "result")) {
    if (event.data.message_id !== run.messageId) return run;
    return { ...run, turnResult: event.data.result };
  }
  if (isChatStreamEventOf(event, "node")) {
    if (event.data.message_id !== run.messageId) return run;
    const node = event.data.status === "started" ? event.data : null;
    return { ...run, node, ...(node === null ? { stage: null } : {}) };
  }
  if (isChatStreamEventOf(event, "stage")) {
    if (event.data.message_id !== run.messageId) return run;
    return { ...run, stage: event.data, node: null };
  }
  if (isChatStreamEventOf(event, "delta")) {
    if (event.data.message_id !== run.messageId) return run;
    return { ...run, content: run.content + event.data.delta };
  }
  if (isChatStreamEventOf(event, "humanizer")) {
    if (event.data.message_id !== run.messageId) return run;
    return { ...run, humanizerProcess: event.data };
  }
  if (isChatStreamEventOf(event, "career")) {
    if (event.data.message_id !== run.messageId) return run;
    return { ...run, careerProcess: event.data };
  }
  if (isChatStreamEventOf(event, "image")) {
    if (event.data.message_id !== run.messageId) return run;
    return { ...run, imageProcess: event.data };
  }
  if (isChatStreamEventOf(event, "video")) {
    if (event.data.message_id !== run.messageId) return run;
    return { ...run, videoProcess: event.data };
  }
  return run;
}
