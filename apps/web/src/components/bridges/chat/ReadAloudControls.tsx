"use client";

/**
 * 单条助手回答的朗读控制（Issue 30）。
 *
 * 入口「朗读」按钮位于消息操作栏（AssistantActions，设计基线契约）；
 * 本组件负责入口以外的全部状态：生成中过程态、可播播放条（播放/暂停/
 * 继续/停止/进度/截断披露）、失败原因与同条重试。页面同时只有一个
 * 活动播放会话：全局 readAloudSession 负责单会话与切换停止；本组件
 * 只订阅并渲染状态。刷新后不伪造播放状态——可播状态来自服务端消息
 * 快照，重新可请求播放。模板页（无 conversationId）不挂载本组件，
 * 工具栏按钮为静态展示。
 */

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

import {
  deleteReadAloud,
  generateReadAloud,
  readAloudAudioUrl,
  type ReadAloudProjection,
  type ReadAloudState,
} from "@/lib/api";
import { readAloudSession } from "@/lib/read-aloud";
import { Icon } from "@/components/design-system/Icon";

import styles from "./chat.module.css";

export interface CapabilityAvailability {
  available: boolean;
  /** 能力不可用时的中文原因（用于禁用入口说明）。 */
  reason?: string;
}

interface ReadAloudControlsProps {
  conversationId: string;
  messageId: string;
  /** 消息朗读状态快照（来自服务端消息投影，刷新后一致）。 */
  projection: ReadAloudProjection | null;
  /** TTS 能力可用性（来自账户级探测快照；不可用时禁用入口并说明原因）。 */
  tts: CapabilityAvailability;
  /** 活动状态变化回调（驱动工具栏按钮 pressed 态）。 */
  onActivityChange?: (active: boolean) => void;
}

/** 供消息操作栏「朗读」入口按钮桥接的句柄。 */
export interface ReadAloudControlsHandle {
  generate: () => void;
  isActive: () => boolean;
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0:00";
  const total = Math.floor(seconds);
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

export const ReadAloudControls = forwardRef<
  ReadAloudControlsHandle,
  ReadAloudControlsProps
>(function ReadAloudControls(
  { conversationId, messageId, projection, tts, onActivityChange },
  ref,
) {
  const [local, setLocal] = useState<ReadAloudProjection | null>(projection);
  const [generating, setGenerating] = useState(false);
  const [actionError, setActionError] = useState("");
  const [playback, setPlayback] = useState({
    playing: false,
    currentTime: 0,
    duration: 0,
  });
  const generatingRef = useRef(false);

  // 刷新/消息重载后以服务端快照为准（本地生成中状态不可跨越刷新伪造）。
  useEffect(() => {
    setLocal(projection);
  }, [projection]);

  useEffect(
    () =>
      readAloudSession.subscribe((state) => {
        if (state.messageId === messageId) {
          setPlayback({
            playing: state.playing,
            currentTime: state.currentTime,
            duration: state.duration,
          });
        } else if (!readAloudSession.isActive(messageId)) {
          // 会话已切换到别处或停止：本消息不再是活动播放会话，复位按钮态
          //（单活动会话，旧播放状态安全停止且 UI 同步复位，不串号）。
          setPlayback({ playing: false, currentTime: 0, duration: 0 });
        }
      }),
    [messageId],
  );

  // 组件卸载（离开对话/切换账户）时若正播放本消息则安全停止。
  useEffect(
    () => () => {
      if (readAloudSession.isActive(messageId)) {
        readAloudSession.stop();
      }
    },
    [messageId],
  );

  // 生成中/播放中 → 工具栏入口按钮 pressed 态。
  useEffect(() => {
    onActivityChange?.(generating || playback.playing);
  }, [generating, playback.playing, onActivityChange]);

  const generate = useCallback(async () => {
    if (generatingRef.current || !tts.available) return;
    generatingRef.current = true;
    setGenerating(true);
    setActionError("");
    try {
      const result = await generateReadAloud(conversationId, messageId);
      setLocal(result);
      if (result.state === "ready") {
        // 生成完成即尝试播放（用户手势延续；被浏览器拦截则停在可播态）。
        readAloudSession.play(
          readAloudAudioUrl(conversationId, messageId),
          messageId,
        );
      }
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "朗读生成失败，请重试。",
      );
    } finally {
      generatingRef.current = false;
      setGenerating(false);
    }
  }, [conversationId, messageId, tts.available]);

  useImperativeHandle(
    ref,
    () => ({
      generate,
      isActive: () =>
        generatingRef.current || readAloudSession.isActive(messageId),
    }),
    [generate, messageId],
  );

  const stopAndDelete = useCallback(async () => {
    readAloudSession.stop();
    try {
      const reset = await deleteReadAloud(conversationId, messageId);
      setLocal(reset);
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "朗读清理失败，请重试。",
      );
    }
  }, [conversationId, messageId]);

  // 能力不可用：入口已禁用（工具栏按钮），下方说明原因。
  if (!tts.available) {
    return (
      <div className={styles.readAloudRow} role="note">
        <Icon name="alert" size={14} aria-hidden />
        <span className={styles.readAloudDisabledNote}>
          {tts.reason ?? "语音朗读能力不可用"}
        </span>
      </div>
    );
  }

  const state: ReadAloudState = local?.state ?? "not_generated";

  if (generating) {
    return (
      <div className={styles.readAloudRow} role="status" aria-live="polite">
        <span className={styles.readAloudGenerating}>
          <span className={styles.readAloudSpinner} aria-hidden="true" />
          正在生成朗读…
        </span>
      </div>
    );
  }

  if (state === "ready" && local?.audio_ref) {
    const playing = playback.playing;
    return (
      <div className={styles.readAloudRow} role="group" aria-label="朗读播放控制">
        <button
          type="button"
          className={styles.readAloudPlayButton}
          aria-label={
            playing
              ? "暂停朗读"
              : playback.currentTime > 0
                ? "继续朗读"
                : "播放朗读"
          }
          onClick={() => {
            if (playing) {
              readAloudSession.pause();
            } else if (
              playback.currentTime > 0 &&
              readAloudSession.isActive(messageId)
            ) {
              readAloudSession.resume();
            } else {
              readAloudSession.play(
                readAloudAudioUrl(conversationId, messageId),
                messageId,
              );
            }
          }}
        >
          <Icon name={playing ? "pause" : "play"} size={16} />
        </button>
        <button
          type="button"
          className={styles.readAloudStopButton}
          aria-label="停止朗读"
          onClick={() => readAloudSession.stop()}
        >
          <Icon name="stopSquare" size={16} />
        </button>
        <div className={styles.readAloudProgressTrack} aria-hidden="true">
          <div
            className={styles.readAloudProgressFill}
            style={{
              width:
                playback.duration > 0
                  ? `${Math.min(100, (playback.currentTime / playback.duration) * 100)}%`
                  : "0%",
            }}
          />
        </div>
        <span className={styles.readAloudTime}>
          {formatTime(playback.currentTime)} / {formatTime(playback.duration)}
        </span>
        {local.truncated && (
          <span
            className={styles.readAloudTruncatedNote}
            title="回答超过朗读长度限制，已截断"
          >
            已朗读前 {local.char_count ?? ""} 字
          </span>
        )}
        <button
          type="button"
          className={styles.readAloudClearButton}
          aria-label="停止并删除朗读"
          onClick={stopAndDelete}
        >
          <Icon name="trash" size={16} />
        </button>
      </div>
    );
  }

  // 失败态：原因 + 同条重试（入口按钮在消息操作栏）。
  if (state === "failed" || actionError) {
    return (
      <div className={styles.readAloudRow} role="group" aria-label="朗读">
        <span className={styles.readAloudErrorNote} role="alert">
          {actionError || local?.error_message || "朗读生成失败"}
        </span>
        <button
          type="button"
          className={styles.readAloudButton}
          onClick={generate}
          aria-label="重新生成朗读"
        >
          <Icon name="retry" size={16} />
          重试
        </button>
      </div>
    );
  }

  // 未生成：入口按钮在消息操作栏，这里不渲染（模板页静态基线）。
  return null;
});
