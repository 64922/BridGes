"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { TiebaEvidenceSelection } from "@/components/bridges/chat/TiebaEvidencePanel";
import { MAIN_CONTENT_ID } from "@/components/layout/MainContent";
import type {
  ChatAttachmentProjection,
  CitationProjection,
  CommuteRouteProjection,
  PaperSearchProjection,
  TiebaPostProjection,
  TiebaSummaryCitation,
} from "@/lib/api";
import type { ChatMediaSelection, ChatMediaTarget } from "@/lib/chat-media";

interface CitationSelection {
  conversationId: string;
  messageId: string;
  citation: CitationProjection;
}

interface AttachmentSelection {
  conversationId: string;
  messageId: string;
  attachment: ChatAttachmentProjection;
}

interface PaperSelection {
  conversationId: string;
  messageId: string;
  search: PaperSearchProjection;
  paperId: string | null;
}

interface CommuteDetailSelection {
  conversationId: string;
  messageId: string;
  route: CommuteRouteProjection;
}

type PanelKey =
  | "citation"
  | "attachment"
  | "paper"
  | "commute"
  | "tieba"
  | "media"
  | "study";

/**
 * 对话页右侧资料/媒体面板的选择与焦点生命周期。
 *
 * 身份固定到当前会话，切换会话立即清空；同一时间只打开一个面板。
 * 关闭后按“触发按钮 → 输入框 → 主内容区”恢复焦点。账户变化由
 * AppShell 的 key 重挂载整个页面，因此面板卸载即释放请求与资源。
 */
export function useChatDetailPanels(conversationId: string) {
  const [citationSelection, setCitationSelection] = useState<CitationSelection | null>(null);
  const [attachmentSelection, setAttachmentSelection] =
    useState<AttachmentSelection | null>(null);
  const [paperSelection, setPaperSelection] = useState<PaperSelection | null>(null);
  const [commuteSelection, setCommuteSelection] =
    useState<CommuteDetailSelection | null>(null);
  const [tiebaSelection, setTiebaSelection] = useState<TiebaEvidenceSelection | null>(null);
  const [mediaSelection, setMediaSelection] = useState<ChatMediaSelection | null>(null);
  const [studyDetailsOpen, setStudyDetailsOpen] = useState(false);

  const triggerRefs = useRef<Record<PanelKey, HTMLButtonElement | null>>({
    citation: null,
    attachment: null,
    paper: null,
    commute: null,
    tieba: null,
    media: null,
    study: null,
  });

  useEffect(() => {
    for (const key of Object.keys(triggerRefs.current) as PanelKey[]) {
      triggerRefs.current[key] = null;
    }
    setCitationSelection(null);
    setAttachmentSelection(null);
    setPaperSelection(null);
    setCommuteSelection(null);
    setTiebaSelection(null);
    setMediaSelection(null);
    setStudyDetailsOpen(false);
  }, [conversationId]);

  const restoreTrigger = useCallback((trigger: HTMLButtonElement | null) => {
    window.requestAnimationFrame(() => {
      if (trigger?.isConnected) {
        trigger.focus({ preventScroll: true });
        return;
      }
      const composer = document.getElementById("composer-input");
      if (composer instanceof HTMLElement) {
        composer.focus({ preventScroll: true });
        return;
      }
      document.getElementById(MAIN_CONTENT_ID)?.focus({ preventScroll: true });
    });
  }, []);

  const clearOthers = useCallback((keep: PanelKey) => {
    for (const key of Object.keys(triggerRefs.current) as PanelKey[]) {
      if (key !== keep) triggerRefs.current[key] = null;
    }
    if (keep !== "citation") setCitationSelection(null);
    if (keep !== "attachment") setAttachmentSelection(null);
    if (keep !== "paper") setPaperSelection(null);
    if (keep !== "commute") setCommuteSelection(null);
    if (keep !== "tieba") setTiebaSelection(null);
    if (keep !== "media") setMediaSelection(null);
    if (keep !== "study") setStudyDetailsOpen(false);
  }, []);

  const closePanel = useCallback(
    (key: PanelKey, clear: () => void) => {
      clear();
      const trigger = triggerRefs.current[key];
      triggerRefs.current[key] = null;
      restoreTrigger(trigger);
    },
    [restoreTrigger]
  );

  const openCitation = useCallback(
    (messageId: string, citation: CitationProjection, trigger: HTMLButtonElement) => {
      clearOthers("citation");
      triggerRefs.current.citation = trigger;
      setCitationSelection({ conversationId, messageId, citation });
    },
    [clearOthers, conversationId]
  );

  const openAttachment = useCallback(
    (messageId: string, attachment: ChatAttachmentProjection, trigger: HTMLButtonElement) => {
      clearOthers("attachment");
      triggerRefs.current.attachment = trigger;
      setAttachmentSelection({ conversationId, messageId, attachment });
    },
    [clearOthers, conversationId]
  );

  const openPaper = useCallback(
    (
      messageId: string,
      search: PaperSearchProjection,
      trigger: HTMLButtonElement
    ) => {
      clearOthers("paper");
      triggerRefs.current.paper = trigger;
      setPaperSelection({ conversationId, messageId, search, paperId: null });
    },
    [clearOthers, conversationId]
  );

  const openCommuteDetails = useCallback(
    (messageId: string, route: CommuteRouteProjection, trigger: HTMLButtonElement) => {
      clearOthers("commute");
      triggerRefs.current.commute = trigger;
      setCommuteSelection({ conversationId, messageId, route });
    },
    [clearOthers, conversationId]
  );

  const openTiebaEvidence = useCallback(
    (
      messageId: string,
      citation: TiebaSummaryCitation,
      post: TiebaPostProjection | null,
      trigger: HTMLButtonElement
    ) => {
      clearOthers("tieba");
      triggerRefs.current.tieba = trigger;
      setTiebaSelection({ conversationId, messageId, citation, post });
    },
    [clearOthers, conversationId]
  );

  const openMedia = useCallback(
    (messageId: string, target: ChatMediaTarget, trigger: HTMLButtonElement) => {
      clearOthers("media");
      triggerRefs.current.media = trigger;
      setMediaSelection({ conversationId, messageId, ...target });
    },
    [clearOthers, conversationId]
  );

  const openStudyDetails = useCallback(
    (trigger: HTMLButtonElement) => {
      clearOthers("study");
      triggerRefs.current.study = trigger;
      setStudyDetailsOpen(true);
    },
    [clearOthers]
  );

  const selectPaper = useCallback((paperId: string | null) => {
    setPaperSelection((current) => (current ? { ...current, paperId } : current));
  }, []);

  const closeCitation = useCallback(
    () => closePanel("citation", () => setCitationSelection(null)),
    [closePanel]
  );
  const closeAttachment = useCallback(
    () => closePanel("attachment", () => setAttachmentSelection(null)),
    [closePanel]
  );
  const closePaper = useCallback(
    () => closePanel("paper", () => setPaperSelection(null)),
    [closePanel]
  );
  const closeCommuteDetails = useCallback(
    () => closePanel("commute", () => setCommuteSelection(null)),
    [closePanel]
  );
  const closeTiebaEvidence = useCallback(
    () => closePanel("tieba", () => setTiebaSelection(null)),
    [closePanel]
  );
  const closeMedia = useCallback(
    () => closePanel("media", () => setMediaSelection(null)),
    [closePanel]
  );
  const closeStudyDetails = useCallback(
    () => closePanel("study", () => setStudyDetailsOpen(false)),
    [closePanel]
  );

  return {
    activeCitation:
      citationSelection?.conversationId === conversationId ? citationSelection : null,
    openCitation,
    closeCitation,
    activeAttachment:
      attachmentSelection?.conversationId === conversationId
        ? attachmentSelection
        : null,
    openAttachment,
    closeAttachment,
    activePaper: paperSelection?.conversationId === conversationId ? paperSelection : null,
    openPaper,
    selectPaper,
    closePaper,
    activeCommute:
      commuteSelection?.conversationId === conversationId ? commuteSelection : null,
    openCommuteDetails,
    closeCommuteDetails,
    activeTieba: tiebaSelection?.conversationId === conversationId ? tiebaSelection : null,
    openTiebaEvidence,
    closeTiebaEvidence,
    activeMedia: mediaSelection?.conversationId === conversationId ? mediaSelection : null,
    openMedia,
    closeMedia,
    studyDetailsOpen,
    openStudyDetails,
    closeStudyDetails,
  };
}
