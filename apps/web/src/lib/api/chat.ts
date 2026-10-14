import type { AnswerFeedback, ChatAttachmentDraftProjection, ChatConversationListProjection, ChatConversationProjection, ChatFirstTurnInput, ChatFirstTurnResponse, ChatModuleId, ChatRunStartedResponse, ChatStopResponse, ChatStreamEvent, CitationDetailProjection, DocumentIngestionProjection, FeedbackKind, IndexStatusProjection, StudyRestartOptionsProjection, StudyRestartRequest } from "./types";
import { API_BASE, parseApiError } from "./core";

// ---------------------------------------------------------------------------
// Issue 11：持久化流式聊天
// ---------------------------------------------------------------------------

/**
 * 判别式类型守卫：从事件载荷的 kind 字段收窄流事件类型。
 * 事件名枚举与载荷形状均来自 OpenAPI 契约（ChatStreamEvent），
 * 不再与后端生成器手写镜像。
 */
export function isChatStreamEventOf<K extends ChatStreamEvent["data"]["kind"]>(
  event: ChatStreamEvent,
  kind: K
): event is ChatStreamEvent & {
  data: Extract<ChatStreamEvent["data"], { kind: K }>;
} {
  return event.data.kind === kind;
}

/**
 * 消费一次 SSE 流式响应，把每个事件回调给调用方。
 * 不在此处抛出网络错误以外的异常——服务端错误事件通过 error 事件回调。
 */
async function readSseStream(
  response: Response,
  onEvent: (event: ChatStreamEvent) => void
): Promise<void> {
  if (!response.body) {
    throw new Error("流式响应没有可读内容。");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let separator = buffer.indexOf("\n\n");
    while (separator !== -1) {
      const block = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      let eventName = "message";
      let dataText = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) {
          eventName = line.slice("event:".length).trim();
        } else if (line.startsWith("data:")) {
          dataText += line.slice("data:".length).trim();
        }
      }
      if (dataText) {
        onEvent({ event: eventName, data: JSON.parse(dataText) } as ChatStreamEvent);
      }
      separator = buffer.indexOf("\n\n");
    }
  }
}

export async function listChatConversations(): Promise<ChatConversationListProjection> {
  const res = await fetch(`${API_BASE}/chat/conversations`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function updateChatConversation(
  conversationId: string,
  update: { title?: string; pinned?: boolean }
): Promise<ChatConversationProjection> {
  const body: Record<string, unknown> = {};
  if (update.title !== undefined) body.title = update.title;
  if (update.pinned !== undefined) body.pinned = update.pinned;
  const res = await fetch(`${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function deleteChatConversation(conversationId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}`, {
    method: "DELETE",
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
}

export async function getChatConversation(
  conversationId: string
): Promise<ChatConversationProjection> {
  const res = await fetch(`${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 读取附件摄取的完整详情（状态、失败阶段、页码/章节、向量可用性）。 */
export async function getAttachmentIngestion(
  conversationId: string,
  objectId: string
): Promise<DocumentIngestionProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/attachments/${encodeURIComponent(objectId)}/ingestion`,
    { credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 返回当前账户的索引状态（向量可用性、活跃版本与可回滚版本链）。 */
export async function getIngestionIndexStatus(): Promise<IndexStatusProjection> {
  const res = await fetch(`${API_BASE}/chat/ingestion/index`, {
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 发送消息：同事务创建用户消息、助手占位与 queued 生成运行，立即返回。
 *
 * Issue 02：HTTP 不再持有生成生命周期——生成由后台执行器领取执行，
 * 事件持久化到运行游标；随后以返回的 ``run_id``/``cursor`` 订阅
 * ``subscribeChatRunEvents`` 恢复进度。断开/刷新/切换会话都不改变运行。
 */
export async function createChatRun(
  conversationId: string,
  content: string,
  idempotencyKey?: string,
  attachmentIds: string[] = [],
  moduleId?: ChatModuleId
): Promise<ChatRunStartedResponse> {
  const res = await fetch(`${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    // V2 Issue 02：携带幂等键——网络重试复用同一运行，不重复写消息。
    // Issue 05：照片草稿与文字同请求原子绑定（服务端保持顺序）。
    // V2 Issue 11：显式模块随消息持久化；未选择时不提交该字段。
    body: JSON.stringify({
      content,
      ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
      ...(attachmentIds.length > 0 ? { attachment_ids: attachmentIds } : {}),
      ...(moduleId ? { module_id: moduleId } : {}),
    }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function startFirstTurn(
  input: ChatFirstTurnInput
): Promise<ChatFirstTurnResponse> {
  const attachmentIds = input.attachmentIds ?? [];
  const body = {
    content: input.content,
    idempotency_key: input.idempotencyKey,
    ...(input.conversationId !== undefined
      ? { conversation_id: input.conversationId }
      : {}),
    ...(attachmentIds.length > 0 ? { attachment_ids: attachmentIds } : {}),
    ...(input.moduleId ? { module_id: input.moduleId } : {}),
    mode: input.mode ?? "companion",
  };
  const res = await fetch(`${API_BASE}/chat/first-turn`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

// ---------------------------------------------------------------------------
// Issue 05：聊天照片附件草稿（账户域，发送时随消息原子绑定）
// ---------------------------------------------------------------------------

/**
 * 上传一张照片草稿（幂等：同 upload_id 重放返回既有草稿）。
 *
 * 原始字节直传，文件名经 ``X-Bridges-Filename`` 头传递（URL 编码，
 * 服务端解码）；类型与体积由服务端嗅探校验，中文原因在 ``detail.message``。
 */
export async function uploadChatAttachmentDraft(
  content: Blob,
  filename: string,
  uploadId: string,
  signal?: AbortSignal
): Promise<ChatAttachmentDraftProjection> {
  const res = await fetch(`${API_BASE}/chat/attachment-drafts`, {
    method: "POST",
    headers: {
      "Content-Type": content.type || "application/octet-stream",
      "X-Bridges-Filename": encodeURIComponent(filename),
      "X-Bridges-Upload-Id": uploadId,
    },
    credentials: "same-origin",
    body: content,
    signal,
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 列出当前账户全部未发送照片草稿（刷新/重开后恢复草稿列表）。 */
export async function listChatAttachmentDrafts(): Promise<
  ChatAttachmentDraftProjection[]
> {
  const res = await fetch(`${API_BASE}/chat/attachment-drafts`, {
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 草稿图片的同源预览地址（经账户授权后返回；跨账户 404 不泄漏）。 */
export function chatAttachmentDraftContentUrl(objectId: string): string {
  return `${API_BASE}/chat/attachment-drafts/${encodeURIComponent(objectId)}/content`;
}

/** 已绑定附件的同源下载地址（经账户与会话授权；用于消息内缩略图）。 */
export function chatAttachmentContentUrl(
  conversationId: string,
  objectId: string
): string {
  return `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/attachments/${encodeURIComponent(objectId)}/download`;
}

/** 移除一条草稿（幂等删除；发送成功后草稿随绑定自动消失）。 */
export async function removeChatAttachmentDraft(objectId: string): Promise<void> {
  const res = await fetch(
    `${API_BASE}/chat/attachment-drafts/${encodeURIComponent(objectId)}`,
    { method: "DELETE", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
}

/**
 * 订阅生成运行的持久化事件（游标续读，断线由调用方重连）。
 *
 * Issue 02：事件由后台执行器持久化，本订阅只回放与等待——客户端断开
 * 只移除订阅者，不改变运行状态。运行终态时全部事件（含终态事件）已
 * 可读，回放完即结束；进行中由心跳保持连接。``signal`` 用于停止/卸载
 * 时中断订阅（不影响运行）。
 */
export async function subscribeChatRunEvents(
  conversationId: string,
  messageId: string,
  cursor: number,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/events?cursor=${cursor}`,
    {
      credentials: "same-origin",
      cache: "no-store",
      signal,
    }
  );
  if (!res.ok) throw await parseApiError(res);
  await readSseStream(res, onEvent);
}

/** 提交一条回答反馈（Issue 27）：回答不合适或画像有误（幂等，不丢反馈）。
 *  Issue 29：``career_item_ref`` 把反馈定位到生涯规划结果的具体条目。 */
export async function submitAnswerFeedback(
  conversationId: string,
  messageId: string,
  request: {
    kind: FeedbackKind;
    feedback_text: string;
    preference?: string | null;
    assertion_id?: string | null;
    career_item_ref?: string | null;
  }
): Promise<AnswerFeedback> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/feedback`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(request),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 列出对话内反馈（最新在前，供前端恢复与闭环查看）。 */
export async function listConversationFeedback(
  conversationId: string
): Promise<AnswerFeedback[]> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/feedback`,
    { credentials: "same-origin", cache: "no-store" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 把一条反馈标记为已处理并记录修正说明（幂等）。 */
export async function resolveAnswerFeedback(
  conversationId: string,
  feedbackId: string,
  resolutionNote: string
): Promise<AnswerFeedback> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/feedback/${encodeURIComponent(feedbackId)}/resolve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ resolution_note: resolutionNote }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 重试失败的助手消息：创建新尝试与 queued 运行（与发送同一外壳）。
 *
 * V2 Issue 11：``moduleId`` 用于「点击建议一键以原文启动模块」——服务端
 * 只在该轮用户消息本身没有模块时生效，绝不改写历史的逐消息标识。
 */
export async function retryChatRun(
  conversationId: string,
  messageId: string,
  idempotencyKey?: string,
  moduleId?: ChatModuleId
): Promise<ChatRunStartedResponse> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/retry`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      // V2 Issue 02：携带幂等键——网络重试复用同一运行，不重复创建尝试。
      body: JSON.stringify({
        ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
        ...(moduleId ? { module_id: moduleId } : {}),
      }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function stopChatMessage(
  conversationId: string,
  messageId: string
): Promise<ChatStopResponse> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/stop`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * Issue 05：针对失败的后台诊断任务手动重试（同一任务、同一幂等单元）。
 * 返回更新后的任务投影；队列再次领取后由轮询刷新核验状态与档案。
 */
export async function retryStudyDiagnosis(
  conversationId: string,
  taskId: string
): Promise<NonNullable<ChatConversationProjection["study_diagnosis_tasks"]>[number]> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/study/diagnosis/${encodeURIComponent(taskId)}/retry`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * Issue 06：保存用户对一条学习记录的主动纠正（用户自评，未经核验）。
 * 携带编辑时看到的记录版本做并发校验：409 时服务端拒绝覆盖，前端保留
 * 当前输入并刷新最新状态；纠正保留被取代结论与操作版本。
 */
export async function correctStudyRecord(
  conversationId: string,
  recordId: string,
  request: { note: string; expected_version: number }
): Promise<NonNullable<ChatConversationProjection["study_learning_record"]>[number]> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/study/records/${encodeURIComponent(recordId)}/corrections`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(request),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * Issue 06：删除一条学习记录并保留撤回标记；迟到诊断不会恢复该判断。
 * 按既有用户管理规范先确认，版本冲突（409）时如实提示并刷新。
 */
export async function deleteStudyRecord(
  conversationId: string,
  recordId: string,
  expectedVersion: number
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/study/records/${encodeURIComponent(recordId)}`,
    {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ expected_version: expectedVersion }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
}

/**
 * 工单 12：旧学习会话的主动续学选项（本人目标候选与仍可访问旧材料）。
 *
 * 服务端只对旧版学习会话返回选项（409 表示无需续学）；材料逐项如实标注
 * 来源身份，两者都为空时 ``can_restart`` 为 false，界面不提供续学动作。
 */
export async function getStudyRestartOptions(
  conversationId: string
): Promise<StudyRestartOptionsProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/study/restart-options`,
    { credentials: "same-origin", cache: "no-store" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 工单 12：由用户主动发起的续学——以所选目标/材料原子创建新的学习模式
 * 会话；重复请求携带同一幂等键，服务端复用既有新会话（不产生新数据）。
 */
export async function restartStudyConversation(
  conversationId: string,
  request: StudyRestartRequest
): Promise<ChatFirstTurnResponse> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/study/restart`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(request),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 单条引用的证据详情（Issue 20）：点击引用时实时校验授权并给出
 * 打开原文入口；原文已删除或权限变化时返回安全中文状态。
 */
export async function getCitationDetail(
  conversationId: string,
  messageId: string,
  citationId: string
): Promise<CitationDetailProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/citations/${encodeURIComponent(citationId)}`,
    { credentials: "same-origin", cache: "no-store" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}
