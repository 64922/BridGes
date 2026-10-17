"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  listChatAttachmentDrafts,
  removeChatAttachmentDraft,
  uploadChatAttachmentDraft,
} from "@/lib/api";
import type { ChatAttachmentDraftProjection } from "@/lib/api";
import {
  CHAT_ATTACHMENT_MAX_BYTES,
  CHAT_ATTACHMENT_MAX_COUNT,
  CHAT_DUPLICATE_MESSAGE,
  CHAT_TOO_LARGE_MESSAGE,
  CHAT_TOO_MANY_MESSAGE,
  CHAT_UNSUPPORTED_TYPE_MESSAGE,
  isIngestionSettled,
  isPhotoAttachment,
  isPickedFileAcceptable,
} from "@/lib/chat-attachments";

// Issue 05/06：附件客户端约束（与服务端同款限制，提前拦截减少无效上传）。
// 照片（PNG/JPEG/GIF/WebP）本轮多模态直读；PDF/DOCX/TXT/Markdown 本轮经
// 解析、分块与检索引用，解析状态在草稿与消息里都可见。
const DRAFT_STATUS_POLL_MS = 3000;

/** 单条附件提示（类型/体积/数量等被拒绝时的中文原因，可逐条关闭）。 */
export interface ComposerDraftError {
  id: number;
  filename?: string;
  message: string;
}

/**
 * 输入区附件草稿的唯一所有者。
 *
 * 草稿是账户级、发送前隔离的服务端记录；本 hook 只负责读取、上传、排序、
 * 移除与解析状态轮询。发送成功后必须调用 ``clearAfterSend``：它递增代际，
 * 使仍在途的恢复/轮询响应无法把已绑定附件重新写回下一轮。
 */
export function useComposerAttachments() {
  const [drafts, setDrafts] = useState<ChatAttachmentDraftProjection[]>([]);
  const [draftErrors, setDraftErrors] = useState<ComposerDraftError[]>([]);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  // 预览加载失败的草稿：在其缩略图位置显示中文原因（AC2「读取失败」）。
  const [brokenPreviews, setBrokenPreviews] = useState<Set<string>>(new Set());
  const fileInputRef = useRef<HTMLInputElement>(null);
  const draftErrorSeqRef = useRef(0);
  // 发送成功会清空草稿；代际变化后忽略此前发出的读取响应。
  const epochRef = useRef(0);

  const addDraftError = useCallback((message: string, filename?: string) => {
    draftErrorSeqRef.current += 1;
    const id = draftErrorSeqRef.current;
    setDraftErrors((current) => [...current, { id, filename, message }]);
  }, []);

  const dismissDraftError = useCallback((id: number) => {
    setDraftErrors((current) => current.filter((error) => error.id !== id));
  }, []);

  /** 按服务端同款规则预校验后上传为草稿；单张失败不影响其他草稿与正文。 */
  const uploadDrafts = useCallback(
    async (files: File[]) => {
      const epoch = epochRef.current;
      for (const file of files) {
        try {
          const projection = await uploadChatAttachmentDraft(
            file,
            file.name,
            crypto.randomUUID()
          );
          if (epoch !== epochRef.current) continue;
          setDrafts((current) =>
            current.some((draft) => draft.object_id === projection.object_id)
              ? current
              : [...current, projection]
          );
        } catch (error) {
          if (epoch !== epochRef.current) continue;
          addDraftError(
            error instanceof Error ? error.message : "上传失败，请检查网络后重试。",
            file.name
          );
        } finally {
          setUploadingCount((count) => Math.max(0, count - 1));
        }
      }
    },
    [addDraftError]
  );

  const addFiles = useCallback(
    (files: File[]) => {
      if (files.length === 0) return;
      const seenNames = new Set(
        drafts.map((draft) => `${draft.original_filename}:${draft.content_length}`)
      );
      let capacity = CHAT_ATTACHMENT_MAX_COUNT - drafts.length;
      const accepted: File[] = [];
      for (const file of files) {
        if (!isPickedFileAcceptable(file.name)) {
          addDraftError(CHAT_UNSUPPORTED_TYPE_MESSAGE, file.name);
        } else if (file.size > CHAT_ATTACHMENT_MAX_BYTES) {
          addDraftError(CHAT_TOO_LARGE_MESSAGE, file.name);
        } else if (seenNames.has(`${file.name}:${file.size}`)) {
          addDraftError(CHAT_DUPLICATE_MESSAGE, file.name);
        } else if (capacity <= 0) {
          addDraftError(CHAT_TOO_MANY_MESSAGE, file.name);
        } else {
          seenNames.add(`${file.name}:${file.size}`);
          accepted.push(file);
          capacity -= 1;
        }
      }
      if (accepted.length === 0) return;
      setUploadingCount((count) => count + accepted.length);
      void uploadDrafts(accepted);
    },
    [addDraftError, drafts, uploadDrafts]
  );

  const onFileInputChange = useCallback(() => {
    const input = fileInputRef.current;
    if (!input) return;
    addFiles(Array.from(input.files ?? []));
    input.value = "";
  }, [addFiles]);

  /** 调整页序（上移/下移按钮均为可聚焦元素，键盘即可完成排序）。 */
  const moveDraft = useCallback((objectId: string, offset: -1 | 1) => {
    setDrafts((current) => {
      const index = current.findIndex((draft) => draft.object_id === objectId);
      const target = index + offset;
      if (index < 0 || target < 0 || target >= current.length) return current;
      const next = [...current];
      const [item] = next.splice(index, 1);
      next.splice(target, 0, item);
      return next;
    });
  }, []);

  const removeDraft = useCallback((objectId: string) => {
    setDrafts((current) => current.filter((draft) => draft.object_id !== objectId));
    void removeChatAttachmentDraft(objectId).catch(() => {
      // 删除请求失败不回滚本地列表：服务端草稿由过期清理兜底。
    });
  }, []);

  /** 预览 /content 读取失败：在对应附件旁显示中文原因（不静默破图）。 */
  const markPreviewBroken = useCallback((objectId: string) => {
    setBrokenPreviews((current) => {
      if (current.has(objectId)) return current;
      const next = new Set(current);
      next.add(objectId);
      return next;
    });
  }, []);

  /** 发送成功：草稿已在服务端随消息绑定，本地清空并阻止旧响应复活。 */
  const clearAfterSend = useCallback(() => {
    epochRef.current += 1;
    setDrafts([]);
    setDraftErrors([]);
  }, []);

  // 页面重开后恢复账户既有草稿（草稿账户隔离，刷新/重登不丢失）。
  useEffect(() => {
    const epoch = epochRef.current;
    let cancelled = false;
    listChatAttachmentDrafts()
      .then((rows) => {
        if (!cancelled && epoch === epochRef.current) setDrafts(rows);
      })
      .catch(() => {
        // 恢复失败不打断输入：草稿仍在服务端，可稍后重试或重新添加。
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // V2 Issue 06：文件草稿在发送前就排队解析，未到终态时轮询刷新草稿投影
  // （排队 → 解析中 → 已解析/失败/无法识别）。照片不参与文档解析，也从不
  // 触发轮询；合并时保留本地页序与本地新增草稿。
  const pendingParseCount = drafts.filter(
    (draft) =>
      !isPhotoAttachment(draft.media_type) &&
      !isIngestionSettled(draft.ingestion_status)
  ).length;
  useEffect(() => {
    if (pendingParseCount === 0) return;
    const timer = window.setInterval(() => {
      const epoch = epochRef.current;
      listChatAttachmentDrafts()
        .then((rows) => {
          if (epoch !== epochRef.current) return;
          setDrafts((current) => {
            const byId = new Map(rows.map((row) => [row.object_id, row]));
            const merged = current.map((draft) => byId.get(draft.object_id) ?? draft);
            const known = new Set(merged.map((draft) => draft.object_id));
            return [...merged, ...rows.filter((row) => !known.has(row.object_id))];
          });
        })
        .catch(() => {
          // 轮询失败保留上一次状态，下一次定时器继续（不清空草稿）。
        });
    }, DRAFT_STATUS_POLL_MS);
    return () => window.clearInterval(timer);
  }, [pendingParseCount]);

  return {
    drafts,
    draftErrors,
    uploadingCount,
    dragOver,
    brokenPreviews,
    fileInputRef,
    addFiles,
    onFileInputChange,
    dismissDraftError,
    moveDraft,
    removeDraft,
    markPreviewBroken,
    clearAfterSend,
    setDragOver,
  };
}
