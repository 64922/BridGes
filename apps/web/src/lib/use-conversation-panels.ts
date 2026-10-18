"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { MAIN_CONTENT_ID } from "@/components/layout/MainContent";
import type { TiebaEvidenceSelection } from "@/components/bridges/chat/TiebaEvidencePanel";
import type {
  ChatAttachmentProjection,
  CitationProjection,
  CommuteRouteProjection,
  PaperSearchProjection,
  TiebaPostProjection,
  TiebaSummaryCitation,
} from "@/lib/api";

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

type DetailPanel = "citation" | "attachment" | "paper" | "commute" | "tieba";

function restorePanelTrigger(trigger: HTMLButtonElement | null) {
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
}

/**
 * 对话页右侧资料/学习面板的唯一状态边界。
 *
 * 同一时刻只允许一个面板打开；打开新面板会关闭旧面板但不把焦点还给
 * 旧触发按钮，关闭当前面板才恢复其触发按钮的焦点。
 */
export function useConversationPanels(conversationId: string) {
  const [citationSelection, setCitationSelection] = useState<CitationSelection | null>(null);
  const citationTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [attachmentSelection, setAttachmentSelection] =
    useState<AttachmentSelection | null>(null);
  const attachmentTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [paperSelection, setPaperSelection] = useState<PaperSelection | null>(null);
  const paperTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [commuteSelection, setCommuteSelection] =
    useState<CommuteDetailSelection | null>(null);
  const commuteTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [tiebaSelection, setTiebaSelection] = useState<TiebaEvidenceSelection | null>(null);
  const tiebaTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [studyDetailsOpen, setStudyDetailsOpen] = useState(false);
  const studyDetailsTriggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    citationTriggerRef.current = null;
    attachmentTriggerRef.current = null;
    paperTriggerRef.current = null;
    commuteTriggerRef.current = null;
    tiebaTriggerRef.current = null;
    studyDetailsTriggerRef.current = null;
    setCitationSelection(null);
    setAttachmentSelection(null);
    setPaperSelection(null);
    setCommuteSelection(null);
    setTiebaSelection(null);
    setStudyDetailsOpen(false);
  }, [conversationId]);

  const closeOthers = useCallback((keep: DetailPanel | "study" | null) => {
    if (keep !== "citation") {
      citationTriggerRef.current = null;
      setCitationSelection(null);
    }
    if (keep !== "attachment") {
      attachmentTriggerRef.current = null;
      setAttachmentSelection(null);
    }
    if (keep !== "paper") {
      paperTriggerRef.current = null;
      setPaperSelection(null);
    }
    if (keep !== "commute") {
      commuteTriggerRef.current = null;
      setCommuteSelection(null);
    }
    if (keep !== "tieba") {
      tiebaTriggerRef.current = null;
      setTiebaSelection(null);
    }
    if (keep !== "study") {
      studyDetailsTriggerRef.current = null;
      setStudyDetailsOpen(false);
    }
  }, []);

  const openCitation = useCallback(
    (messageId: string, citation: CitationProjection, trigger: HTMLButtonElement) => {
      closeOthers("citation");
      citationTriggerRef.current = trigger;
      setCitationSelection({ conversationId, messageId, citation });
    },
    [closeOthers, conversationId]
  );

  const closeCitation = useCallback(() => {
    const trigger = citationTriggerRef.current;
    citationTriggerRef.current = null;
    setCitationSelection(null);
    restorePanelTrigger(trigger);
  }, []);

  const openAttachment = useCallback(
    (
      messageId: string,
      attachment: ChatAttachmentProjection,
      trigger: HTMLButtonElement
    ) => {
      closeOthers("attachment");
      attachmentTriggerRef.current = trigger;
      setAttachmentSelection({ conversationId, messageId, attachment });
    },
    [closeOthers, conversationId]
  );

  const closeAttachment = useCallback(() => {
    const trigger = attachmentTriggerRef.current;
    attachmentTriggerRef.current = null;
    setAttachmentSelection(null);
    restorePanelTrigger(trigger);
  }, []);

  const openTiebaEvidence = useCallback(
    (
      messageId: string,
      citation: TiebaSummaryCitation,
      post: TiebaPostProjection | null,
      trigger: HTMLButtonElement
    ) => {
      closeOthers("tieba");
      tiebaTriggerRef.current = trigger;
      setTiebaSelection({ conversationId, messageId, citation, post });
    },
    [closeOthers, conversationId]
  );

  const closeTiebaEvidence = useCallback(() => {
    const trigger = tiebaTriggerRef.current;
    tiebaTriggerRef.current = null;
    setTiebaSelection(null);
    restorePanelTrigger(trigger);
  }, []);

  const openPaper = useCallback(
    (
      messageId: string,
      search: PaperSearchProjection,
      trigger: HTMLButtonElement
    ) => {
      closeOthers("paper");
      paperTriggerRef.current = trigger;
      setPaperSelection({ conversationId, messageId, search, paperId: null });
    },
    [closeOthers, conversationId]
  );

  const selectPaper = useCallback((paperId: string | null) => {
    setPaperSelection((current) => (current ? { ...current, paperId } : current));
  }, []);

  const closePaper = useCallback(() => {
    const trigger = paperTriggerRef.current;
    paperTriggerRef.current = null;
    setPaperSelection(null);
    restorePanelTrigger(trigger);
  }, []);

  const openCommuteDetails = useCallback(
    (
      messageId: string,
      route: CommuteRouteProjection,
      trigger: HTMLButtonElement
    ) => {
      closeOthers("commute");
      commuteTriggerRef.current = trigger;
      setCommuteSelection({ conversationId, messageId, route });
    },
    [closeOthers, conversationId]
  );

  const closeCommuteDetails = useCallback(() => {
    const trigger = commuteTriggerRef.current;
    commuteTriggerRef.current = null;
    setCommuteSelection(null);
    restorePanelTrigger(trigger);
  }, []);

  const openStudyDetails = useCallback(
    (trigger: HTMLButtonElement) => {
      closeOthers("study");
      studyDetailsTriggerRef.current = trigger;
      setStudyDetailsOpen(true);
    },
    [closeOthers]
  );

  const closeStudyDetails = useCallback(() => {
    const trigger = studyDetailsTriggerRef.current;
    studyDetailsTriggerRef.current = null;
    setStudyDetailsOpen(false);
    restorePanelTrigger(trigger);
  }, []);

  return {
    activeCitationSelection:
      citationSelection?.conversationId === conversationId ? citationSelection : null,
    activeAttachmentSelection:
      attachmentSelection?.conversationId === conversationId ? attachmentSelection : null,
    activePaperSelection:
      paperSelection?.conversationId === conversationId ? paperSelection : null,
    activeCommuteSelection:
      commuteSelection?.conversationId === conversationId ? commuteSelection : null,
    activeTiebaSelection:
      tiebaSelection?.conversationId === conversationId ? tiebaSelection : null,
    studyDetailsOpen,
    openCitation,
    closeCitation,
    openAttachment,
    closeAttachment,
    openTiebaEvidence,
    closeTiebaEvidence,
    openPaper,
    selectPaper,
    closePaper,
    openCommuteDetails,
    closeCommuteDetails,
    openStudyDetails,
    closeStudyDetails,
  };
}
