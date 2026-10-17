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
  type TurnResultProjection,
  type WebSearchProjection,
} from "@/lib/api";

export type ActiveRunKind = "send" | "retry" | "resume";

export type ActiveRunStatus = "streaming" | "error" | "done";

export interface PendingUserMessage {
  id: string;
  text: string;
  attachments?: ChatAttachmentProjection[];
}

export interface ActiveRun {
  turnResult?: TurnResultProjection | null;
  messageId: string;
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
  return {
    messageId: assistant.message_id,
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
    stage:
      assistant.status === "streaming"
        ? { kind: "stage", message_id: assistant.message_id, stage: "queued", status: "active" }
        : null,
    node: null,
    humanizerProcess: null,
    careerProcess: null,
    imageProcess: null,
    videoProcess: null,
  };
}

export function activeRunFromStarted(
  started: ChatStreamStartedData,
  kind: ActiveRunKind
): ActiveRun {
  return {
    messageId: started.message_id,
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
    stage: { kind: "stage", message_id: started.message_id, stage: "queued", status: "active" },
    node: null,
    humanizerProcess: null,
    careerProcess: null,
    imageProcess: null,
    videoProcess: null,
  };
}

export function activeRunFromError(
  current: ActiveRun,
  error: ChatStreamErrorData,
  kind: ActiveRunKind
): ActiveRun {
  return {
    messageId: error.message_id,
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
