import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/design-system/Button";
import type {
  ChatAttachmentProjection,
  StudyRestartOptionsProjection,
} from "@/lib/api";

import styles from "./chat.module.css";

export interface StudyRestartSelection {
  goalId: string | null;
  objectIds: string[];
  message: string;
}

export interface StudyRestartPanelProps {
  /** 旧会话可续学选项；null 表示尚未加载。 */
  options: StudyRestartOptionsProjection | null;
  optionsLoading: boolean;
  optionsError: string | null;
  submitting: boolean;
  submitError: string | null;
  /** 失败后确认已不可访问并从选择中移除的目标/材料说明（逐项如实标注）。 */
  removedNotes: string[];
  onLoadOptions: () => void;
  onSubmit: (selection: StudyRestartSelection) => Promise<boolean>;
}

function materialLabel(material: ChatAttachmentProjection): string {
  const kind = material.media_type.startsWith("image/") ? "照片" : "文件";
  const size = Math.max(1, Math.round(material.content_length / 1024));
  return `${material.original_filename}（${kind}，${size} KB）`;
}

/**
 * 工单 12：旧学习会话的只读历史与主动续学的分界。
 *
 * 历史保持只读渲染；用户明确发起续学后才读取本人目标与仍可访问的旧
 * 材料，选择目标与材料并补充说明，创建一个新的学习模式对话。失败时
 * 保留其余选择，只逐项移除已确认不可访问的目标/材料。
 */
export function StudyRestartPanel({
  options,
  optionsLoading,
  optionsError,
  submitting,
  submitError,
  removedNotes,
  onLoadOptions,
  onSubmit,
}: StudyRestartPanelProps) {
  const [open, setOpen] = useState(false);
  const [goalId, setGoalId] = useState<string | null>(null);
  const [objectIds, setObjectIds] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const preselected = useRef(false);

  useEffect(() => {
    if (!options) return;
    const goals = options.goals ?? [];
    const materials = options.materials ?? [];
    if (!preselected.current) {
      preselected.current = true;
      if (
        options.linked_goal_id
        && goals.some((goal) => goal.goal_id === options.linked_goal_id)
      ) {
        setGoalId(options.linked_goal_id);
      }
    }
    // 失败后重新读取：只移除已确认不可访问的项，其余选择保留。
    setGoalId((current) =>
      current && goals.some((goal) => goal.goal_id === current) ? current : null
    );
    setObjectIds((current) =>
      current.filter((id) =>
        materials.some((material) => material.object_id === id)
      )
    );
  }, [options]);

  const goals = options?.goals ?? [];
  const materials = options?.materials ?? [];
  const canSubmit =
    !submitting
    && (goalId !== null || objectIds.length > 0)
    && (objectIds.length > 0 || message.trim().length > 0);

  return (
    <section className={styles.studySummary} aria-label="主动续学">
      <div className={styles.studySummarySection}>
        <h3>主动续学</h3>
        <p className={styles.studySummaryNote}>
          这份历史记录保持只读，旧流程不会再执行。主动续学会创建一个新的学习模式对话：
          可以沿用本人学习目标与仍可访问的材料；旧判定与评分只作为历史记录，不会自动迁入长期掌握。
        </p>
        {!open && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setOpen(true);
              if (!options && !optionsLoading) onLoadOptions();
            }}
          >
            主动续学
          </Button>
        )}
        {open && (
          <>
            {optionsLoading && (
              <p role="status">正在读取可续学的目标与材料…</p>
            )}
            {optionsError && (
              <div>
                <p role="alert">{optionsError}</p>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={optionsLoading}
                  onClick={onLoadOptions}
                >
                  重新读取
                </Button>
              </div>
            )}
            {options && !options.can_restart && (
              <p role="status">
                当前没有可沿用的学习目标，也没有仍可访问的旧材料。
                你可以从首页新建学习对话，重新提供材料。
              </p>
            )}
            {options && options.can_restart && (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!canSubmit) return;
                  void onSubmit({
                    goalId,
                    objectIds,
                    message: message.trim(),
                  });
                }}
              >
                {goals.length > 0 && (
                  <fieldset>
                    <legend>沿用学习目标（可选）</legend>
                    <label>
                      <input
                        type="radio"
                        name="restart-goal"
                        checked={goalId === null}
                        onChange={() => setGoalId(null)}
                      />
                      不沿用目标，按补充说明开始
                    </label>
                    {goals.map((goal) => (
                      <label key={goal.goal_id}>
                        <input
                          type="radio"
                          name="restart-goal"
                          value={goal.goal_id}
                          checked={goalId === goal.goal_id}
                          onChange={() => setGoalId(goal.goal_id)}
                        />
                        {goal.statement}
                        {goal.status === "paused" && "（已暂停）"}
                        {goal.current_node_title
                          && ` · 当前知识点：${goal.current_node_title}`}
                      </label>
                    ))}
                  </fieldset>
                )}
                {materials.length > 0 && (
                  <fieldset>
                    <legend>采用旧材料（可选，可多选）</legend>
                    {materials.map((material) => (
                      <label key={material.object_id}>
                        <input
                          type="checkbox"
                          checked={objectIds.includes(material.object_id)}
                          onChange={() =>
                            setObjectIds((current) =>
                              current.includes(material.object_id)
                                ? current.filter((id) => id !== material.object_id)
                                : [...current, material.object_id]
                            )
                          }
                        />
                        {materialLabel(material)}
                      </label>
                    ))}
                  </fieldset>
                )}
                <label>
                  补充说明
                  <textarea
                    aria-label="补充说明"
                    rows={3}
                    maxLength={8000}
                    placeholder={
                      objectIds.length > 0
                        ? "可选：想从上次的哪里继续？"
                        : "必填：想继续学习什么？"
                    }
                    value={message}
                    onChange={(event) => setMessage(event.target.value)}
                  />
                </label>
                {removedNotes.length > 0 && (
                  <ul
                    className={styles.studySummaryNote}
                    aria-label="已移除的失效选择"
                  >
                    {removedNotes.map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                )}
                {submitError && <p role="alert">{submitError}</p>}
                <div className={styles.studyRecordActions}>
                  <Button
                    variant="primary"
                    size="sm"
                    type="submit"
                    disabled={!canSubmit}
                  >
                    {submitting ? "正在创建…" : "创建新学习对话"}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    type="button"
                    disabled={submitting}
                    onClick={() => setOpen(false)}
                  >
                    取消
                  </Button>
                </div>
              </form>
            )}
          </>
        )}
      </div>
    </section>
  );
}
