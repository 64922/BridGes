import { useState } from "react";

import { Button } from "@/components/design-system/Button";
import type { ChatConversationProjection } from "@/lib/api";

import styles from "./chat.module.css";

const STAGES = [
  { id: "recognizing", label: "书页识别" },
  { id: "preview", label: "辅助预习" },
  { id: "tutoring", label: "辅导" },
  { id: "review", label: "复盘" },
  { id: "summary", label: "总结" },
] as const;

const JUDGEMENT_LABEL: Record<string, string> = {
  correct: "正确",
  incomplete: "不完整",
  incorrect: "错误",
};

// 三段兜底文案与消息内总结一致：空段只陈述实际判定，不额外宣称掌握。
const SUMMARY_SECTIONS = [
  { kind: "learned", label: "学到了什么", empty: "本节没有可依据书页归纳的内容。" },
  { kind: "mastered", label: "复盘已掌握", empty: "本次复盘没有判定为正确的题目。" },
  { kind: "gap", label: "还需补的点", empty: "本次复盘的题目全部答对，暂无待补的理解点。" },
] as const;

function evidenceLabels(state: NonNullable<ChatConversationProjection["study"]>) {
  const labels = new Map<string, string>();
  (state.review?.questions ?? []).forEach((item, index) => {
    labels.set(
      item.question_id,
      `第${index + 1}题「${item.question}」`
        + (item.judgement ? `判定为${JUDGEMENT_LABEL[item.judgement]}` : "未作答"),
    );
  });
  for (const page of state.pages ?? []) {
    for (const fragment of page.fragments) {
      labels.set(
        fragment.fragment_id,
        `上传第${page.ordinal}页`
          + (page.page_number != null ? `（书上第${page.page_number}页）` : "")
          + ` · ${fragment.position}`
          + (fragment.source === "user" ? " · 用户补录" : ""),
      );
    }
  }
  return labels;
}

export function StudyProgress({ study: state, busy = false, onAction }: {
  study?: ChatConversationProjection["study"];
  busy?: boolean;
  onAction?: (text: string) => Promise<boolean>;
}) {
  const [sending, setSending] = useState(false);
  const current = state?.stage === "awaiting_pages" ? "recognizing" : state?.stage ?? "recognizing";
  const action = state?.stage === "review" ? "暂停复盘回辅导"
    : state?.review ? "继续复盘" : "开始复盘";
  async function act() {
    if (!onAction || sending || busy) return;
    setSending(true);
    try { await onAction(action); } finally { setSending(false); }
  }
  const summary = state?.summary;
  const labels = summary && state ? evidenceLabels(state) : null;
  return (
    <section className={styles.studyProgress} aria-label="学习阶段">
      <ol className={styles.studyStages}>
        {STAGES.map((stage) => (
          <li
            key={stage.id}
            aria-current={stage.id === current ? "step" : undefined}
            className={stage.id === current ? styles.studyStageCurrent : undefined}
          >
            {stage.label}
          </li>
        ))}
      </ol>
      {summary && labels && (
        <div className={styles.studySummary}>
          {SUMMARY_SECTIONS.map((section) => {
            const points = summary.points.filter((point) => point.kind === section.kind);
            return (
              <div key={section.kind} className={styles.studySummarySection}>
                <h3>{section.label}</h3>
                {points.length === 0 ? (
                  <p>{section.empty}</p>
                ) : (
                  <ul>
                    {points.map((point, index) => {
                      const refs = [
                        ...(point.question_ids ?? []),
                        ...(point.fragment_ids ?? []),
                      ].map((ref) => labels.get(ref)).filter((label): label is string => !!label);
                      return (
                        <li key={index}>
                          {point.text}
                          {refs.length > 0 && (
                            <span className={styles.studySummaryEvidence}>
                              依据：{refs.join("；")}
                            </span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            );
          })}
          <p className={styles.studySummaryNote}>
            总结依据本节书页与已判定题；可以继续追问本节内容，学新小节请新建学习对话。
          </p>
        </div>
      )}
      {onAction && !state?.page_update && (state?.stage === "tutoring" || state?.stage === "review") && (
        <div className={styles.studyReviewActions}>
          {(!state.review?.complete || state.stage === "review") && (
            <Button variant="secondary" size="sm" disabled={busy || sending}
              onClick={() => void act()}>{action}</Button>
          )}
          <span role="status">{state.review?.complete
            ? "本节复盘已结束，作答与判定已保存。"
            : state.stage === "review"
              ? "请在下方回答当前一题；不知道也可以直说。"
              : state.review
                ? "复盘已暂停，继续时从未问题开始；未作答题不计为已掌握。"
                : "学完本节后，可以开始逐题复盘。"}</span>
        </div>
      )}
      {state?.wait_reason && <p role="status">{state.wait_reason === "page_order"
        ? "书上页码与上传顺序不一致，请查看证据并按下方消息调整页序。"
        : "书页有待补拍的位置，请查看下方消息。"}</p>}
      {state?.page_update && (
        <details open>
          <summary>追加书页待确认（尚未更新本节范围）</summary>
          <p role="status">{state.page_update.wait_reason === "page_order"
            ? "请按下方消息调整追加后的页序。"
            : state.page_update.wait_reason === "recognition_failed"
              ? "追加书页识别未完成，请重试原失败消息或补拍。"
              : "请查看下方消息，补充不清晰内容或确认同节归属。"}</p>
          <ul>
            {state.page_update.pages.map((page) => (
              <li key={page.object_id}>
                上传第{page.ordinal}页
                {page.page_number != null && `（书上第${page.page_number}页）`}
                {!page.same_section && " · 同节归属待确认"}
                {page.unclear?.map((issue) => ` · ${issue.position}：${issue.reason}`).join("")}
              </li>
            ))}
          </ul>
        </details>
      )}
      {state?.pages && state.pages.length > 0 && (
        <details>
          <summary>已识别书页与证据（{state.pages.length} 页）</summary>
          <ol className={styles.studyPages}>
            {state.pages.map((page) => (
              <li key={page.object_id}>
                <strong>第{page.ordinal}页</strong>
                {page.page_number != null && `（书上第${page.page_number}页）`}
                {page.replaced_object_ids && page.replaced_object_ids.length > 0 && "（已补拍更新）"}
                <ul>
                  {page.fragments.map((fragment, index) => (
                    <li key={index}>
                      {fragment.position}：{fragment.text}
                      {fragment.source === "user" && "（用户补录）"}
                    </li>
                  ))}
                  {page.unclear?.map((issue, index) => (
                    <li key={index}>待补拍 {issue.position}：{issue.reason}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}
