import styles from "./chat.module.css";

interface StudyState {
  stage: "awaiting_pages" | "recognizing" | "preview" | "tutoring";
  wait_reason?: string | null;
  pages: {
    ordinal: number;
    object_id: string;
    replaced_object_ids?: string[];
    fragments: { position: string; text: string; source: "photo" | "user" }[];
    unclear: { position: string; reason: string }[];
  }[];
}

const STAGES = [
  { id: "recognizing", label: "书页识别" },
  { id: "preview", label: "辅助预习" },
  { id: "tutoring", label: "辅导" },
  { id: "review", label: "复盘" },
  { id: "summary", label: "总结" },
] as const;

export function StudyProgress({ study }: { study?: Record<string, unknown> | null }) {
  const state = study as unknown as StudyState | null | undefined;
  const current = state?.stage === "awaiting_pages" ? "recognizing" : state?.stage ?? "recognizing";
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
      {state?.wait_reason && <p role="status">书页有待补拍的位置，请查看下方消息。</p>}
      {state?.pages && state.pages.length > 0 && (
        <details>
          <summary>已识别书页与证据（{state.pages.length} 页）</summary>
          <ol className={styles.studyPages}>
            {state.pages.map((page) => (
              <li key={page.object_id}>
                <strong>第{page.ordinal}页</strong>
                {page.replaced_object_ids && page.replaced_object_ids.length > 0 && "（已补拍更新）"}
                <ul>
                  {page.fragments.map((fragment, index) => (
                    <li key={index}>
                      {fragment.position}：{fragment.text}
                      {fragment.source === "user" && "（用户补录）"}
                    </li>
                  ))}
                  {page.unclear.map((issue, index) => (
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
