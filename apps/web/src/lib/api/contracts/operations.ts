import type { components } from "@bridges/contracts";
import type { ChatMode, ChatModuleId } from "./core";
import type { SearchResultItem } from "./modules";

export type AttestationConclusion = "approve" | "reject" | "needs_changes";

export type ConflictDisclosureInput = {
  item_ref: string;
  minority_opinion: string;
  basis?: string[];
  missing_evidence?: string[];
  decision?: string;
};

export type ConflictDisclosure = {
  disclosure_id: string;
  pack_id: string;
  pack_version: string;
  item_ref: string;
  minority_opinion: string;
  basis?: string[];
  missing_evidence?: string[];
  decision?: string;
  created_at: string;
};

/**
 * 原子创建新会话首轮（Issue 03）。
 *
 * 首页发送第一条消息或调用任一功能时使用：服务端在同一事务内创建会话、
 * 用户消息、助手占位与 queued 运行，返回完整投影。客户端收到成功响应后
 * 再导航，``sessionStorage`` 不再承担业务真相。``idempotencyKey`` 抵御
 * 双击与网络重放（同键并发只产生一份数据）；``conversationId`` 可选指定
 * 已存在的空会话，缺省在事务内新建。
 * 同键重放返回 200 与既有数据（``idempotent_replay`` 为 true），调用方
 * 无须区分即可导航到同一会话。
 */
export interface ChatFirstTurnInput {
  content: string;
  idempotencyKey: string;
  conversationId?: string;
  mode?: ChatMode;
  // Issue 05：新聊天页直发照片（账户域草稿，首轮事务内原子绑定）。
  attachmentIds?: string[];
  // V2 Issue 11：首轮显式模块（选中 chip 时随首条用户消息持久化）。
  moduleId?: ChatModuleId;
}

// ---------------------------------------------------------------------------
// Issue 24：跨内容统一桌面搜索
// ---------------------------------------------------------------------------

/** 统一搜索的结果类型筛选值（与契约 SearchResultItem.result_type 一致）。 */
export type SearchResultType = SearchResultItem["result_type"];

export interface UnifiedSearchParams {
  q: string;
  types?: SearchResultType[];
  /** ISO 8601 起止时间（updated_at 过滤）。 */
  from?: string;
  to?: string;
  limit?: number;
  signal?: AbortSignal;
}

// ---------------------------------------------------------------------------
// Issue 37: 数据生命周期（导出/删除/备份/恢复）
// ---------------------------------------------------------------------------

export type ExportCategoryProjection = components["schemas"]["ExportCategoryProjection"];

export type ExportPreviewProjection = components["schemas"]["ExportPreviewProjection"];

export type AccountDeletionProjection = components["schemas"]["AccountDeletionProjection"];

export type RestorePreview = components["schemas"]["RestorePreview"];
