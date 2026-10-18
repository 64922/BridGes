import type { ChatConversationProjection } from "@/lib/api";
import { LEGACY_STAGE_LABEL } from "@/lib/study-presentation";

import { StudyRestartPanel, type StudyRestartPanelProps } from "./StudyRestartPanel";
import styles from "./chat.module.css";

const STAGES = [
  { id: "recognizing" },
  { id: "preview" },
  { id: "tutoring" },
  { id: "review" },
  { id: "summary" },
] as const;

const JUDGEMENT_LABEL: Record<string, string> = {
  correct: "正确",
  incomplete: "不完整",
  incorrect: "错误",
};

// 四段兜底文案与消息内总结一致：空段只陈述实际判定，不额外宣称掌握；
// 未作答/尚未判定单独成段，不与答对或漏洞混排（工单 36）。
const SUMMARY_SECTIONS = [
  { kind: "learned", label: "学到了什么", empty: "本节没有可依据书页归纳的内容。" },
  { kind: "mastered", label: "复盘已掌握", empty: "本次复盘没有判定为正确的题目。" },
  { kind: "gap", label: "还需补的点", empty: "本次没有判定不完整、错误或依据需重新确认的题目。" },
  { kind: "unanswered", label: "未作答或尚未判定", empty: "本次复盘没有未作答或尚未判定的题目。" },
] as const;

function evidenceLabels(
  state: NonNullable<ChatConversationProjection["study"]>,
) {
  const labels = new Map<string, string>();
  const materialStatus = new Map(
    (state.materials ?? []).map((item) => [item.object_id, item.status]),
  );
  (state.review?.questions ?? []).forEach((item, index) => {
    labels.set(
      item.question_id,
      `第${index + 1}题「${item.question}」`
        + (item.judgement
          ? `判定为${JUDGEMENT_LABEL[item.judgement]}`
          : item.answer != null ? "已作答（尚未判定）" : "未作答"),
    );
  });
  for (const page of state.pages ?? []) {
    for (const fragment of page.fragments) {
      const status = materialStatus.get(page.object_id);
      const source = status === "deleted" || status === "pending_cleanup"
        ? "已删除材料的历史记录："
        : status === "unknown" ? "材料状态未知：" : "";
      labels.set(
        fragment.fragment_id,
        `${source}上传第${page.ordinal}页`
          + (page.page_number != null ? `（书上第${page.page_number}页）` : "")
          + ` · ${fragment.position}`
          + (fragment.source === "user" ? " · 用户补录" : ""),
      );
    }
  }
  return labels;
}

export function StudyLegacyHistory({
  study: state,
  historyStatus,
  restart,
}: {
  study?: ChatConversationProjection["study"];
  historyStatus?: ChatConversationProjection["study_history_status"];
  restart?: StudyRestartPanelProps;
}) {
  if (!state && historyStatus) {
    return (
      <section className={styles.studySummary} aria-label="学习历史">
        <p className={styles.studySummaryNote} role="status">{historyStatus.message}</p>
        {restart && <StudyRestartPanel {...restart} />}
      </section>
    );
  }
  if (!state) return null;
  const current = state.stage === "awaiting_pages" ? "recognizing" : state.stage ?? "recognizing";
  const summary = state.summary;
  const labels = summary ? evidenceLabels(state) : null;
  return (
    <section className={styles.studySummary} aria-label="学习阶段">
      {historyStatus && (
        <p className={styles.studySummaryNote} role="status">{historyStatus.message}</p>
      )}
      <p className={styles.studySummaryNote} role="note">
        本节是按原始记录只读展示的历史；旧流程不会再执行。
      </p>
      <ol className={styles.studyStages}>
        {STAGES.map((stage) => (
          <li
            key={stage.id}
            aria-current={stage.id === current ? "step" : undefined}
            className={stage.id === current ? styles.studyStageCurrent : undefined}
          >
            {LEGACY_STAGE_LABEL[stage.id]}
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
            总结依据本节书页与已判定题；本节为只读历史记录，继续学习请使用下方「主动续学」。
          </p>
        </div>
      )}
      {state.wait_reason && <p role="status">{state.wait_reason === "page_order"
        ? "书上页码与上传顺序不一致，请查看证据并按下方消息调整页序。"
        : "书页有待补拍的位置，请查看下方消息。"}</p>}
      {state.page_update && (
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
              {state.materials?.find((item) => item.object_id === page.object_id)?.status === "deleted"
                && " · 原材料已删除，保留的识别内容仍是历史记录"}
              {state.materials?.find((item) => item.object_id === page.object_id)?.status === "pending_cleanup"
                && " · 原材料正在删除，保留的识别内容仍是历史记录"}
              {state.materials?.find((item) => item.object_id === page.object_id)?.status === "unknown"
                && " · 暂时无法核验原材料状态"}
                {page.page_number != null && `（书上第${page.page_number}页）`}
                {!page.same_section && " · 同节归属待确认"}
                {page.unclear?.map((issue) => ` · ${issue.position}：${issue.reason}`).join("")}
              </li>
            ))}
          </ul>
        </details>
      )}
      {state.pages && state.pages.length > 0 && (
        <details>
          <summary>已识别书页与证据（{state.pages.length} 页）</summary>
          <ol className={styles.studyPages}>
            {state.pages.map((page) => (
              <li key={page.object_id}>
                <strong>第{page.ordinal}页</strong>
                {state.materials?.find((item) => item.object_id === page.object_id)?.status === "deleted"
                  && <span> · 原材料已删除，以下内容仅是保留的历史识别记录</span>}
                {state.materials?.find((item) => item.object_id === page.object_id)?.status === "pending_cleanup"
                  && <span> · 原材料正在删除，以下内容仅是保留的历史识别记录</span>}
                {state.materials?.find((item) => item.object_id === page.object_id)?.status === "unknown"
                  && <span> · 暂时无法核验原材料状态</span>}
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
      {restart && <StudyRestartPanel {...restart} />}
    </section>
  );
}
