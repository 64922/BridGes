"use client";

import { useEffect, useState } from "react";

import {
  fetchProfileControls,
  updateProfileControls,
  type ProfileAccountControlsProjection,
} from "@/lib/api";

import styles from "./ProfileControlsCard.module.css";

type ControlKey = "recording_enabled" | "usage_enabled";

/**
 * 改进工单 07：记录与使用是两个互相独立的控制，文案分别解释各自的含义，
 * 不合并成一个「画像总开关」：
 * - 关闭自动记录只停止自动新增/更新，主动记住、修改、忘掉、删除始终可用；
 * - 关闭长期画像使用不删除任何信息，回答不再读取长期画像正文，
 *   当前对话与任务材料照常使用。
 */
const CONTROLS: Record<
  ControlKey,
  { title: string; description: string; offNote: string }
> = {
  recording_enabled: {
    title: "自动记录",
    description:
      "开启时，系统会在回答完成后自动从对话里整理明确、可复用的信息。" +
      "关闭只停止自动新增和更新；你仍然可以手动记住、修改、忘掉和删除。",
    offNote: "已停止自动记录：不再自动新增或更新信息。",
  },
  usage_enabled: {
    title: "回答使用长期信息",
    description:
      "开启时，回答可以在相关任务中参考已记录的长期信息。" +
      "关闭后回答不再读取这些内容，但信息全部保留，随时可以重新开启。",
    offNote: "已关闭使用：回答不读取已记录内容，信息仍保留在列表里。",
  },
};

export function ProfileControlsCard() {
  const [controls, setControls] =
    useState<ProfileAccountControlsProjection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<ControlKey | null>(null);

  const load = async () => {
    try {
      setControls(await fetchProfileControls());
      setError(null);
    } catch (reason) {
      setControls(null);
      setError(
        reason instanceof Error ? reason.message : "控制状态暂时无法加载，请重试。"
      );
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const toggle = async (key: ControlKey, next: boolean) => {
    try {
      setBusyKey(key);
      setError(null);
      setControls(await updateProfileControls({ [key]: next }));
    } catch (reason) {
      // 先重拉当前真实状态，再如实报错；不假装切换成功。
      await load();
      setError(
        reason instanceof Error ? reason.message : "设置未能保存，请重试。"
      );
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <section className={styles.card} aria-labelledby="profile-controls-title">
      <h2 id="profile-controls-title" className={styles.title}>
        记录与使用控制
      </h2>
      <p className={styles.lead}>
        自动记录与回答使用长期信息分开控制：停止记录不妨碍你主动管理，
        关闭使用不会删除任何信息。
      </p>

      {error ? (
        <div className={styles.error} role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => void load()}>
            重试
          </button>
        </div>
      ) : null}

      {controls === null && !error ? (
        <p className={styles.state}>正在加载控制状态…</p>
      ) : null}

      {controls !== null
        ? (Object.keys(CONTROLS) as ControlKey[]).map((key) => {
            const meta = CONTROLS[key];
            const enabled = controls[key];
            return (
              <div className={styles.row} key={key}>
                <div className={styles.copy}>
                  <p className={styles.name}>{meta.title}</p>
                  <p className={styles.description}>
                    {enabled ? meta.description : `${meta.description}${meta.offNote}`}
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={enabled}
                  aria-label={meta.title}
                  className={styles.switch}
                  data-state={enabled ? "on" : "off"}
                  disabled={busyKey !== null}
                  onClick={() => void toggle(key, !enabled)}
                >
                  <span className={styles.thumb} aria-hidden="true" />
                  <span className={styles.switchLabel}>
                    {enabled ? "已开启" : "已关闭"}
                  </span>
                </button>
              </div>
            );
          })
        : null}
    </section>
  );
}
