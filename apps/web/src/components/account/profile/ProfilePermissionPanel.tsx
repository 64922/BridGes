"use client";

import { useCallback, useEffect, useState } from "react";

import { StateBlock } from "@/components/bridges/StateBlock";
import {
  listProfilePermissions,
  updateProfilePermission,
  type ProfileDimension,
  type ProfilePermission,
} from "@/lib/api";

import styles from "./ProfileCenter.module.css";

// 低风险自动更新类别（Issue 26，ADR-0002）。必须与后端
// contracts/profiles.py 的 AUTO_WRITABLE_DIMENSIONS 保持一致：后端新增
// 类别时此处需同步，否则该类别不显示许可开关（服务端仍会拒绝其许可）。
const AUTO_WRITABLE: { dimension: ProfileDimension; label: string; description: string }[] = [
  {
    dimension: "stage_goal",
    label: "阶段目标",
    description: "聊天中提到目标（如“我的目标是…”）时自动记录，并通知你",
  },
  {
    dimension: "interest_preference",
    label: "兴趣偏好",
    description: "聊天中提到偏好（如“我喜欢…”）时自动记录，并通知你",
  },
  {
    dimension: "expression_habit",
    label: "表达习惯",
    description: "聊天中表达风格与称呼（如“请叫我…”）自动记录，并通知你",
  },
];

const SCENES: { value: string; label: string; description: string }[] = [
  { value: "companion", label: "日常陪伴", description: "普通新聊天的对话" },
  { value: "study", label: "学习模式", description: "学习项目中的对话" },
];

/** 许可开关的键：类别 + 场景。 */
function permissionKey(dimension: string, scene: string): string {
  return `${dimension}:${scene}`;
}

/**
 * 低风险自动更新许可面板（Issue 26，ADR-0002）。
 *
 * 每个低风险类别 × 适用场景一个开关，默认关闭；授权范围只能由你设置，
 * 智能体不会代开。开启后聊天中匹配的观察会自动写入画像，每次写入都有
 * 中文通知与一键撤回。切换写入失败时行内报错并可重试，不显示假成功。
 */
export function ProfilePermissionPanel() {
  const [permissions, setPermissions] = useState<ProfilePermission[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setPermissions(await listProfilePermissions());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "许可加载失败，请稍后重试。");
      setPermissions(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const enabledMap = new Map<string, boolean>();
  for (const permission of permissions || []) {
    enabledMap.set(permissionKey(permission.dimension, permission.scene), permission.enabled);
  }

  const applyLocal = (key: string, dimension: ProfileDimension, scene: string, enabled: boolean) => {
    setPermissions((current) => {
      const next = [...(current || [])];
      const index = next.findIndex(
        (permission) => permissionKey(permission.dimension, permission.scene) === key
      );
      const permission: ProfilePermission = {
        account_id: next[index]?.account_id ?? "",
        dimension,
        scene,
        enabled,
        updated_at: new Date().toISOString(),
      };
      if (index >= 0) next[index] = permission;
      else next.push(permission);
      return next;
    });
  };

  const toggle = async (dimension: ProfileDimension, scene: string, enabled: boolean) => {
    const key = permissionKey(dimension, scene);
    setBusyKey(key);
    setNotice(null);
    setError(null);
    // 乐观更新：开关立即反映目标状态，失败时回滚并显示错误，不显示假成功。
    applyLocal(key, dimension, scene, enabled);
    try {
      await updateProfilePermission({ dimension, scene, enabled });
      setNotice(
        enabled
          ? "已开启：符合该类别与场景的低风险信息会自动记录并通知你。"
          : "已关闭：该类别与场景不再自动记录，既有记录保留。"
      );
    } catch (cause) {
      applyLocal(key, dimension, scene, !enabled);
      setError(
        cause instanceof Error ? cause.message : "许可更新失败，请稍后重试。"
      );
    } finally {
      setBusyKey(null);
    }
  };

  if (permissions === null && !error) {
    return (
      <StateBlock
        kind="loading"
        title="正在加载自动更新许可…"
        description="读取低风险画像类别的自动记录开关。"
      />
    );
  }

  if (permissions === null && error) {
    return (
      <StateBlock
        kind="error"
        title="许可加载失败"
        description={error}
        actionLabel="重新加载"
        onAction={() => void load()}
      />
    );
  }

  return (
    <div className={styles.permissionPanel} data-testid="profile-permissions">
      <p className={styles.permissionNote}>
        开启后，聊天中出现的低风险目标、兴趣偏好与表达习惯会自动写入画像；
        每次写入都会有中文通知、来源与一键撤回。默认关闭，授权范围只能由你
        设置，智能体不会代替你开启。
      </p>
      {error && (
        <p role="alert" className={styles.errorText}>
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className={styles.permissionNotice}>
          {notice}
        </p>
      )}
      <div className={styles.permissionGrid}>
        {AUTO_WRITABLE.map((category) => (
          <div key={category.dimension} className={styles.permissionRowGroup}>
            <div className={styles.permissionGroupHeader}>
              <h3 className={styles.permissionGroupTitle}>{category.label}</h3>
              <p className={styles.permissionGroupDescription}>{category.description}</p>
            </div>
            {SCENES.map((scene) => {
              const key = permissionKey(category.dimension, scene.value);
              const enabled = enabledMap.get(key) ?? false;
              const busy = busyKey === key;
              return (
                <label key={scene.value} className={styles.permissionToggleRow}>
                  <span className={styles.permissionToggleText}>
                    <span className={styles.permissionToggleScene}>{scene.label}</span>
                    <span className={styles.permissionToggleHint}>{scene.description}</span>
                  </span>
                  <span className={styles.switch} data-on={enabled || undefined}>
                    <input
                      type="checkbox"
                      checked={enabled}
                      disabled={busyKey !== null}
                      aria-label={`${category.label}·${scene.label}自动更新`}
                      onChange={(event) =>
                        void toggle(category.dimension, scene.value, event.target.checked)
                      }
                    />
                    <span className={styles.switchTrack} aria-hidden="true">
                      <span className={styles.switchThumb} />
                    </span>
                  </span>
                  {busy && (
                    <span role="status" className={styles.permissionBusy}>
                      保存中…
                    </span>
                  )}
                </label>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
