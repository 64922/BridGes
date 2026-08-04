"use client";

import { useEffect, useState } from "react";

import { Dialog } from "@/components/bridges/Dialog";
import { Button } from "@/components/design-system/Button";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { formatAbsoluteTime } from "@/lib/format";
import {
  getProfileAssertionHistory,
  type ProfileAssertion,
  type ProfileAssertionVersion,
} from "@/lib/api";

import styles from "./ProfileCenter.module.css";

const statusLabels: Record<string, string> = {
  active: "活跃",
  frozen: "冻结",
  withdrawn: "已撤回",
  deleted: "已删除",
  stale: "过期",
};

function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  return formatAbsoluteTime(value);
}

function actorLabel(version: ProfileAssertionVersion): string {
  // 所有版本快照都由用户操作产生（changed_by="user"）；智能体候选触发的
  // 变更将在 Issue 26 自动写入落地时写入 "candidate"。
  return version.changed_by === "user" ? "用户操作" : "智能体候选";
}

interface ProfileHistoryDialogProps {
  open: boolean;
  record: ProfileAssertion | null;
  onRollback: (toVersion: number) => Promise<void>;
  onClose: () => void;
}

/**
 * 画像记录版本历史抽屉。
 *
 * 列出每个版本的快照（内容、场景、状态、时间、变更者、原因），可比较版本；
 * 活跃记录可回滚到历史版本。旧值不会被覆盖抹除。
 */
export function ProfileHistoryDialog({
  open,
  record,
  onRollback,
  onClose,
}: ProfileHistoryDialogProps) {
  const [versions, setVersions] = useState<ProfileAssertionVersion[]>([]);
  const [currentVersion, setCurrentVersion] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rollingBack, setRollingBack] = useState<number | null>(null);

  useEffect(() => {
    if (!open || !record) return;
    setLoading(true);
    setError(null);
    setVersions([]);
    getProfileAssertionHistory(record.assertion_id)
      .then((history) => {
        setVersions(history.versions || []);
        setCurrentVersion(history.current_version);
      })
      .catch((cause) => {
        setError(cause instanceof Error ? cause.message : "历史加载失败，请稍后重试。");
      })
      .finally(() => setLoading(false));
  }, [open, record]);

  const rollback = async (toVersion: number) => {
    if (!record) return;
    setRollingBack(toVersion);
    setError(null);
    try {
      await onRollback(toVersion);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "回滚失败，请稍后重试。");
    } finally {
      setRollingBack(null);
    }
  };

  const canRollback = record?.status === "active";

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="版本历史"
      description={
        record
          ? `记录 ${record.value_or_rule} 的历史快照；旧值保留，覆盖不会抹除来源。`
          : "画像记录的历史快照。"
      }
    >
      {loading ? (
        <LoadingStatus message="正在加载版本历史…" />
      ) : error ? (
        <p role="alert" className={styles.errorText}>
          {error}
        </p>
      ) : versions.length === 0 ? (
        <p>还没有版本快照。</p>
      ) : (
        <div className={styles.historyList}>
          {[...versions].reverse().map((version) => (
            <div key={version.version_id} className={styles.versionItem}>
              <div className={styles.versionHeader}>
                <span className={styles.versionNumber}>v{version.version}</span>
                <span className={styles.chip}>{statusLabels[version.status] || version.status}</span>
                <span className={styles.chip}>{actorLabel(version)}</span>
              </div>
              <p className={styles.recordValue}>{version.value_or_rule}</p>
              {version.applicable_scenes && version.applicable_scenes.length > 0 && (
                <p className={styles.metaValue}>
                  适用场景：{version.applicable_scenes.join("，")}
                </p>
              )}
              <p className={styles.metaValue}>变更时间：{formatTime(version.changed_at)}</p>
              <p className={styles.versionReason}>{version.change_reason}</p>
              {canRollback && version.version !== currentVersion && (
                <div style={{ marginTop: "var(--space-2)" }}>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => rollback(version.version)}
                    isLoading={rollingBack === version.version}
                  >
                    回滚到此版本
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Dialog>
  );
}
