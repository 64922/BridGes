"use client";

import { Button } from "@/components/design-system/Button";
import { StatusBadge } from "@/components/design-system/StatusBadge";
import type { ProfileAssertion } from "@/lib/api";
import { formatAbsoluteTime } from "@/lib/format";

import styles from "./ProfileCenter.module.css";

const statusBadge: Record<
  string,
  { status: "pass" | "fail" | "unknown" | "waiting" | "blocked"; label: string }
> = {
  active: { status: "pass", label: "活跃" },
  frozen: { status: "waiting", label: "已冻结" },
  withdrawn: { status: "blocked", label: "已撤回" },
  deleted: { status: "fail", label: "已删除" },
  stale: { status: "unknown", label: "过期" },
};

const sensitivityLabels: Record<string, string> = {
  public: "公开",
  preference: "偏好",
  learning: "学习",
  sensitive: "敏感",
  prohibited: "禁止",
};

const sensitivityChip: Record<string, boolean> = {
  sensitive: true,
};

function formatTime(value: string | null | undefined): string {
  if (!value) return "从未";
  return formatAbsoluteTime(value);
}

interface ProfileRecordCardProps {
  record: ProfileAssertion;
  evidenceSourceLabel: string;
  onEdit: (record: ProfileAssertion) => void;
  onWithdraw: (record: ProfileAssertion) => void;
  onFreeze: (record: ProfileAssertion) => void;
  onUnfreeze: (record: ProfileAssertion) => void;
  onDelete: (record: ProfileAssertion) => void;
  onHistory: (record: ProfileAssertion) => void;
}

/** 一条画像记录的治理卡片：值、来源、时间、范围、敏感级别、状态与操作。 */
export function ProfileRecordCard({
  record,
  evidenceSourceLabel,
  onEdit,
  onWithdraw,
  onFreeze,
  onUnfreeze,
  onDelete,
  onHistory,
}: ProfileRecordCardProps) {
  const badge = statusBadge[record.status] || statusBadge.stale;
  const sensitivity = sensitivityLabels[record.sensitivity_class] || record.sensitivity_class;
  const deleted = record.status === "deleted";

  return (
    <article className={`${styles.recordCard} ${deleted ? styles.recordDeleted : ""}`}>
      <div className={styles.recordMain}>
        <p className={styles.recordValue}>{record.value_or_rule}</p>
        <div className={styles.recordBadges}>
          <StatusBadge status={badge.status} label={badge.label} />
          <span
            className={`${styles.chip} ${
              sensitivityChip[record.sensitivity_class] ? styles.chipSensitive : ""
            }`}
          >
            敏感级别：{sensitivity}
          </span>
        </div>
      </div>

      <dl className={styles.recordMeta}>
        <div className={styles.metaItem}>
          <dt className={styles.metaLabel}>来源证据</dt>
          <dd className={styles.metaValue}>{evidenceSourceLabel}</dd>
        </div>
        <div className={styles.metaItem}>
          <dt className={styles.metaLabel}>创建时间</dt>
          <dd className={styles.metaValue}>{formatTime(record.created_at)}</dd>
        </div>
        <div className={styles.metaItem}>
          <dt className={styles.metaLabel}>更新时间</dt>
          <dd className={styles.metaValue}>{formatTime(record.updated_at)}</dd>
        </div>
        <div className={styles.metaItem}>
          <dt className={styles.metaLabel}>最近用于回答</dt>
          <dd className={styles.metaValue}>{formatTime(record.last_used_at)}</dd>
        </div>
        <div className={styles.metaItem}>
          <dt className={styles.metaLabel}>适用场景</dt>
          <dd className={styles.metaValue}>
            {record.applicable_scenes && record.applicable_scenes.length > 0
              ? record.applicable_scenes.join("，")
              : "通用"}
          </dd>
        </div>
        <div className={styles.metaItem}>
          <dt className={styles.metaLabel}>授权范围</dt>
          <dd className={styles.metaValue}>{record.authorization_scope}</dd>
        </div>
      </dl>

      <div className={styles.recordActions}>
        {record.status === "active" && (
          <>
            <Button variant="ghost" size="sm" onClick={() => onEdit(record)}>
              编辑
            </Button>
            <Button variant="ghost" size="sm" onClick={() => onWithdraw(record)}>
              撤回
            </Button>
            <Button variant="ghost" size="sm" onClick={() => onFreeze(record)}>
              冻结
            </Button>
          </>
        )}
        {(record.status === "frozen" || record.status === "withdrawn") && (
          <Button variant="ghost" size="sm" onClick={() => onUnfreeze(record)}>
            解冻
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={() => onHistory(record)}>
          版本历史
        </Button>
        {record.status !== "deleted" && (
          <Button variant="danger" size="sm" onClick={() => onDelete(record)}>
            删除
          </Button>
        )}
      </div>
    </article>
  );
}
