"use client";

import { useEffect, useRef } from "react";

import { Icon } from "@/components/design-system/Icon";
import type { PaperSearchProjection } from "@/lib/api";

import styles from "./PaperDetailsPanel.module.css";
import {
  PaperDetailView,
  PaperSearchOverview,
  paperKey,
} from "./PaperDetailsSections";

export interface PaperPanelSelection {
  messageId: string;
  search: PaperSearchProjection;
  paperId: string | null;
}

function selectedPaperOf(selection: PaperPanelSelection) {
  if (!selection.paperId) return null;
  return (
    (selection.search.papers ?? []).find(
      (paper) => paperKey(paper) === selection.paperId
    ) ?? null
  );
}

export function PaperDetailsPanel({
  selection,
  onClose,
  onSelectPaper,
}: {
  selection: PaperPanelSelection | null;
  onClose: () => void;
  onSelectPaper: (paperId: string | null) => void;
}) {
  if (!selection) return null;
  return (
    <PaperDetailsContent
      selection={selection}
      onClose={onClose}
      onSelectPaper={onSelectPaper}
    />
  );
}

function PaperDetailsContent({
  selection,
  onClose,
  onSelectPaper,
}: {
  selection: PaperPanelSelection;
  onClose: () => void;
  onSelectPaper: (paperId: string | null) => void;
}) {
  const panelRef = useRef<HTMLElement>(null);
  const paper = selectedPaperOf(selection);

  useEffect(() => {
    panelRef.current?.focus();
  }, []);

  useEffect(() => {
    panelRef.current?.focus();
  }, [selection.paperId]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.isComposing) return;
      event.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const title = paper ? paper.title : "论文检索与依据";

  return (
    <aside
      ref={panelRef}
      id="paper-details-panel"
      role="dialog"
      aria-modal="false"
      tabIndex={-1}
      aria-labelledby="paper-details-title"
      className={styles.panel}
      data-testid="paper-details-panel"
    >
      <header className={styles.header}>
        <div className={styles.titleGroup}>
          <span className={styles.kicker}>论文资料</span>
          <h2
            id="paper-details-title"
            className={styles.title}
            title={title}
            data-testid="paper-details-title"
          >
            {title}
          </h2>
        </div>
        <button
          type="button"
          aria-label="关闭论文资料面板"
          className={styles.closeButton}
          data-testid="paper-details-close"
          onClick={onClose}
        >
          <Icon name="close" size={18} aria-hidden />
        </button>
      </header>

      <p className={styles.meta}>
        {paper
          ? "本轮真实检索记录中的一篇推荐；读取范围与链接不会被后续候选替换。"
          : "本轮实际查询、筛选条件、候选与引用关系。"}
      </p>

      <div className={styles.body}>
        {paper ? (
          <PaperDetailView
            paper={paper}
            search={selection.search}
            onBack={() => onSelectPaper(null)}
          />
        ) : (
          <PaperSearchOverview
            search={selection.search}
            onSelectPaper={onSelectPaper}
          />
        )}
      </div>
    </aside>
  );
}
