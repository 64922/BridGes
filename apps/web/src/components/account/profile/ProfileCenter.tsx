"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { ProfileAvatarCard } from "@/components/account/profile/ProfileAvatarCard";
import { ProfileHistoryDialog } from "@/components/account/profile/ProfileHistoryDialog";
import { ProfileRecordCard } from "@/components/account/profile/ProfileRecordCard";
import { ReasonConfirmDialog } from "@/components/account/profile/ReasonConfirmDialog";
import { RecordFormDialog } from "@/components/account/profile/RecordFormDialog";
import { Button } from "@/components/design-system/Button";
import { ButtonLink } from "@/components/design-system/ButtonLink";
import { StateBlock } from "@/components/bridges/StateBlock";
import {
  classifyApiError,
  createManualAssertion,
  decideCandidate,
  deleteProfileAssertion,
  exportProfile,
  freezeProfileAssertion,
  listProfileAssertions,
  listProfileCandidates,
  listProfileObservations,
  modifyProfileAssertion,
  rollbackProfileAssertion,
  unfreezeProfileAssertion,
  withdrawProfileAssertion,
  type ManualAssertionCreateRequest,
  type ProfileAssertion,
  type ProfileCandidate,
  type ProfileDimension,
} from "@/lib/api";

import styles from "./ProfileCenter.module.css";

const DIMENSIONS: { value: ProfileDimension; label: string; description: string }[] = [
  { value: "basic_information", label: "基本情况", description: "身份与背景的稳定事实" },
  { value: "stage_goal", label: "阶段目标", description: "当前阶段的学习与成长目标" },
  { value: "interest_preference", label: "兴趣偏好", description: "长期兴趣与内容偏好" },
  { value: "expression_habit", label: "表达习惯", description: "沟通与回答的风格习惯" },
  { value: "knowledge_state", label: "知识状态", description: "已具备与待补强的知识" },
  { value: "emotion_trend", label: "情绪变化趋势", description: "经确认的长期情绪走向" },
  { value: "important_experience", label: "重要经历", description: "经确认的关键经历" },
  { value: "current_problem", label: "正在面对的问题", description: "当前需要解决的实际问题" },
  { value: "authorization_scope", label: "授权范围", description: "允许画像参与回答的范围" },
];

const CONFIRMED_DIMENSIONS = new Set([
  "emotion_trend",
  "important_experience",
  "current_problem",
]);

type ConfirmAction = "withdraw" | "freeze" | "unfreeze" | "delete" | "reject-candidate";

const confirmConfig: Record<
  ConfirmAction,
  { title: string; description: string; confirmLabel: string; danger: boolean }
> = {
  withdraw: {
    title: "撤回这条画像记录？",
    description: "撤回后记录不再用于后续回答，但保留可审计的历史；可随时解冻恢复。",
    confirmLabel: "确认撤回",
    danger: true,
  },
  freeze: {
    title: "冻结这条画像记录？",
    description: "冻结期间记录不再自动更新，也暂停用于新的回答；可随时解冻。",
    confirmLabel: "确认冻结",
    danger: false,
  },
  unfreeze: {
    title: "解冻这条画像记录？",
    description: "解冻后记录恢复活跃，可再次用于回答，并接收你主动的后续更新。",
    confirmLabel: "确认解冻",
    danger: false,
  },
  delete: {
    title: "删除这条画像记录？",
    description: "删除将按确认合同清理当前记录并写入墓碑，删除的内容不再保留正文。",
    confirmLabel: "确认删除",
    danger: true,
  },
  "reject-candidate": {
    title: "拒绝这条候选画像？",
    description: "拒绝后该候选不会成为稳定画像，也不会用于跨会话回答。",
    confirmLabel: "确认拒绝",
    danger: true,
  },
};

interface ConfirmState {
  action: ConfirmAction;
  target: ProfileAssertion | ProfileCandidate;
}

/** 数字分身画像中心：九类画像记录、候选确认、历史、导出与静态头像。 */
export function ProfileCenter() {
  const [assertions, setAssertions] = useState<ProfileAssertion[] | null>(null);
  const [candidates, setCandidates] = useState<ProfileCandidate[]>([]);
  const [observationSources, setObservationSources] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<ProfileDimension>("basic_information");
  const [formState, setFormState] = useState<
    { record: ProfileAssertion | null; dimension: ProfileDimension } | null
  >(null);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [historyRecord, setHistoryRecord] = useState<ProfileAssertion | null>(null);
  const [exporting, setExporting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [assertionsResult, candidatesResult] = await Promise.all([
        listProfileAssertions(),
        listProfileCandidates(),
      ]);
      setAssertions(assertionsResult);
      setCandidates(
        candidatesResult.filter((candidate) => candidate.review_status === "proposed")
      );
      // 观察来源用于展示每条记录的来源证据（手动新增的观察以用户填写的
      // 来源说明为 source_ref）。
      const observations = await listProfileObservations().catch(() => []);
      setObservationSources(
        Object.fromEntries(
          observations.map((observation) => [
            observation.observation_id,
            observation.source_ref,
          ])
        )
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "画像加载失败，请稍后重试。");
      setAssertions(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const recordsByDimension = useMemo(() => {
    const map = new Map<ProfileDimension, ProfileAssertion[]>();
    for (const dimension of DIMENSIONS) {
      map.set(dimension.value, []);
    }
    for (const assertion of assertions || []) {
      const list = map.get(assertion.canonical_dimension as ProfileDimension);
      if (list) list.push(assertion);
    }
    for (const list of Array.from(map.values())) {
      list.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    }
    return map;
  }, [assertions]);

  const refresh = async () => {
    await load();
  };

  const handleCreate = async (payload: ManualAssertionCreateRequest, reason: string) => {
    await createManualAssertion(payload);
    void reason;
    await refresh();
  };

  const handleEdit = async (payload: ManualAssertionCreateRequest, reason: string) => {
    if (!formState?.record) return;
    await modifyProfileAssertion(formState.record.assertion_id, {
      value_or_rule: payload.value_or_rule,
      applicable_scenes: payload.applicable_scenes,
      reason,
    });
    await refresh();
  };

  const handleConfirm = async (reason: string) => {
    if (!confirmState) return;
    const { action, target } = confirmState;
    if ("assertion_id" in target) {
      const id = target.assertion_id;
      if (action === "withdraw") await withdrawProfileAssertion(id, reason);
      else if (action === "freeze") await freezeProfileAssertion(id, reason);
      else if (action === "unfreeze") await unfreezeProfileAssertion(id, reason);
      else if (action === "delete") await deleteProfileAssertion(id, reason);
    } else if (action === "reject-candidate") {
      await decideCandidate(target.candidate_id, "reject", reason);
    }
    await refresh();
  };

  const handleRollback = async (toVersion: number) => {
    if (!historyRecord) return;
    await rollbackProfileAssertion(
      historyRecord.assertion_id,
      toVersion,
      "回滚到历史版本"
    );
    await refresh();
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const exported = await exportProfile();
      const blob = new Blob([JSON.stringify(exported, null, 2)], {
        type: "application/json;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `bridges-profile-export-${new Date().toISOString().slice(0, 10)}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "导出失败，请稍后重试。");
    } finally {
      setExporting(false);
    }
  };

  if (loading && !assertions) {
    return (
      <StateBlock kind="loading" title="正在加载画像中心…" description="正在读取九类画像记录与候选。"
      />
    );
  }

  if (error && !assertions) {
    const kind = error ? (classifyApiError(error) === "session" ? "permission" : "error") : "error";
    return (
      <StateBlock
        kind={kind}
        title={kind === "permission" ? "登录状态已失效" : "画像加载失败"}
        description={error}
        actionLabel="重新加载"
        onAction={refresh}
      />
    );
  }

  const currentRecords = recordsByDimension.get(activeTab) || [];

  const evidenceSource = (record: ProfileAssertion): string => {
    const observationId = (record.supporting_observation_ids || [])[0];
    if (observationId && observationSources[observationId]) {
      return observationSources[observationId];
    }
    if (record.promoted_from_candidate_id) {
      return `候选 ${record.promoted_from_candidate_id.slice(0, 8)} 确认晋升（v${record.version}）`;
    }
    return "用户手动声明";
  };

  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <p className={styles.eyebrow}>画像与记忆中心</p>
        <h1 className={styles.title}>数字分身画像</h1>
        <p className={styles.lead}>
          九类画像信息以可独立治理的记录保存：每条记录都有来源证据、适用范围、
          敏感级别、记录与更新时间、最近使用时间和版本历史。你可以新增、确认、
          编辑、撤回、冻结、解冻、删除与导出；写入失败会回滚界面，不显示假成功。
        </p>
        <div className={styles.headerActions}>
          <ButtonLink href="/" variant="secondary" size="md">
            返回新聊天
          </ButtonLink>
          <Button variant="primary" size="md" onClick={handleExport} isLoading={exporting}>
            导出画像与授权历史
          </Button>
        </div>

        <div className={styles.stack}>
          <ProfileAvatarCard />

          {candidates.length > 0 && (
            <section className={styles.card} aria-labelledby="profile-candidates-title">
              <div className={styles.cardHeader}>
                <div>
                  <h2 id="profile-candidates-title" className={styles.cardTitle}>
                    待确认候选
                  </h2>
                  <p className={styles.cardDescription}>
                    智能体提出的画像候选需要你确认后才能跨会话使用；情绪趋势、
                    重要经历与当前问题等敏感候选未经确认不会生效。
                  </p>
                </div>
              </div>
              <div className={styles.candidateList}>
                {candidates.map((candidate) => (
                  <div key={candidate.candidate_id} className={styles.candidateItem}>
                    <p className={styles.candidateValue}>{candidate.value_or_rule}</p>
                    <div className={styles.candidateMeta}>
                      <span>
                        类别：{DIMENSIONS.find((d) => d.value === candidate.canonical_dimension)?.label || candidate.canonical_dimension}
                      </span>
                      <span>
                        证据：{(candidate.supporting_observation_ids || []).length} 条观察
                      </span>
                      <span>授权范围：{candidate.authorization_scope}</span>
                    </div>
                    <div className={styles.candidateActions}>
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={async () => {
                          await decideCandidate(candidate.candidate_id, "accept", "用户确认候选画像");
                          await refresh();
                        }}
                      >
                        确认
                      </Button>
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() =>
                          setConfirmState({ action: "reject-candidate", target: candidate })
                        }
                      >
                        拒绝
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {error && (
            <p role="alert" className={styles.errorText}>
              {error}
            </p>
          )}

          {
            <>
              <div
                role="tablist"
                aria-label="画像类别"
                className={styles.tabList}
                onKeyDown={(event) => {
                  const index = DIMENSIONS.findIndex((d) => d.value === activeTab);
                  if (event.key === "ArrowRight") {
                    event.preventDefault();
                    setActiveTab(DIMENSIONS[(index + 1) % DIMENSIONS.length].value);
                  } else if (event.key === "ArrowLeft") {
                    event.preventDefault();
                    setActiveTab(
                      DIMENSIONS[(index - 1 + DIMENSIONS.length) % DIMENSIONS.length].value
                    );
                  } else if (event.key === "Home") {
                    event.preventDefault();
                    setActiveTab(DIMENSIONS[0].value);
                  } else if (event.key === "End") {
                    event.preventDefault();
                    setActiveTab(DIMENSIONS[DIMENSIONS.length - 1].value);
                  }
                }}
              >
                {DIMENSIONS.map((dimension) => {
                  const count = recordsByDimension.get(dimension.value)?.length || 0;
                  return (
                    <button
                      key={dimension.value}
                      type="button"
                      role="tab"
                      id={`tab-${dimension.value}`}
                      aria-selected={activeTab === dimension.value}
                      aria-controls={`panel-${dimension.value}`}
                      tabIndex={activeTab === dimension.value ? 0 : -1}
                      className={`${styles.tab} ${
                        activeTab === dimension.value ? styles.tabActive : ""
                      }`}
                      onClick={() => setActiveTab(dimension.value)}
                    >
                      {dimension.label}
                      {count > 0 && <span className={styles.tabCount}>{count}</span>}
                    </button>
                  );
                })}
              </div>

              <section
                id={`panel-${activeTab}`}
                role="tabpanel"
                aria-labelledby={`tab-${activeTab}`}
                className={styles.tabPanel}
              >
                <div className={styles.panelHeader}>
                  <p className={styles.cardDescription}>
                    {DIMENSIONS.find((d) => d.value === activeTab)?.description}
                    {CONFIRMED_DIMENSIONS.has(activeTab) &&
                      "（该类别内容需你主动声明或确认）"}
                  </p>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setFormState({ record: null, dimension: activeTab })}
                  >
                    新增记录
                  </Button>
                </div>

                {currentRecords.length === 0 ? (
                  <StateBlock
                    kind="empty"
                    title="该类别还没有记录"
                    description="新增一条记录后，它会作为独立条目展示来源、时间、范围与历史。"
                  />
                ) : (
                  <div className={styles.recordList}>
                    {currentRecords.map((record) => (
                      <ProfileRecordCard
                        key={record.assertion_id}
                        record={record}
                        evidenceSourceLabel={evidenceSource(record)}
                        onEdit={(target) =>
                          setFormState({ record: target, dimension: target.canonical_dimension as ProfileDimension })
                        }
                        onWithdraw={(target) =>
                          setConfirmState({ action: "withdraw", target })
                        }
                        onFreeze={(target) =>
                          setConfirmState({ action: "freeze", target })
                        }
                        onUnfreeze={(target) =>
                          setConfirmState({ action: "unfreeze", target })
                        }
                        onDelete={(target) =>
                          setConfirmState({ action: "delete", target })
                        }
                        onHistory={setHistoryRecord}
                      />
                    ))}
                  </div>
                )}
              </section>
            </>
          }
        </div>
      </div>

      <RecordFormDialog
        open={formState !== null}
        title={formState?.record ? "编辑画像记录" : "新增画像记录"}
        description={
          formState?.record
            ? "修改内容与适用范围将创建新版本，旧值保留在历史中。"
            : "填写记录内容、适用范围、敏感级别与授权范围；记录由你主动声明。"
        }
        record={formState?.record ?? null}
        dimension={formState?.dimension ?? "basic_information"}
        onSubmit={formState?.record ? handleEdit : handleCreate}
        onClose={() => setFormState(null)}
      />

      <ReasonConfirmDialog
        open={confirmState !== null}
        title={confirmState ? confirmConfig[confirmState.action].title : ""}
        description={confirmState ? confirmConfig[confirmState.action].description : ""}
        confirmLabel={confirmState ? confirmConfig[confirmState.action].confirmLabel : ""}
        danger={confirmState ? confirmConfig[confirmState.action].danger : false}
        onSubmit={handleConfirm}
        onClose={() => setConfirmState(null)}
      />

      <ProfileHistoryDialog
        open={historyRecord !== null}
        record={historyRecord}
        onRollback={handleRollback}
        onClose={() => setHistoryRecord(null)}
      />
    </div>
  );
}
