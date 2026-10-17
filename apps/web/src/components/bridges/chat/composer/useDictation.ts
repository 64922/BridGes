"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { transcribeDictation } from "@/lib/api";
import type { CapabilityAvailability } from "../capability-availability";

// Issue 30：录音硬上限（服务端 ASR 同款 300 秒限制，客户端提前自动停止）。
const MAX_RECORDING_SECONDS = 299;

export type DictationPhase = "idle" | "recording" | "transcribing" | "error";

interface UseDictationOptions {
  conversationId?: string;
  variant: "conversation" | "new-chat";
  asr: CapabilityAvailability;
  /** 转写成功：把可编辑文本回填输入框（绝不自动发送）。 */
  onTranscribed: (transcript: string) => void;
  /** 用户显式重录：清掉上一段转写文本，不遗留待发送内容。 */
  onRetake: () => void;
}

export function formatDictationTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

/**
 * 输入区听写状态机（idle → recording → transcribing → idle/error）。
 *
 * 停止录音后才提交完整音频到固定 ASR 快照；转写结果可编辑回填，绝不
 * 自动发送；取消/重录不遗留待发送文本、跨账户临时音频或后台孤儿任务。
 * 只在用户点击入口后申请麦克风，不新增持续监听。
 */
export function useDictation({
  conversationId,
  variant,
  asr,
  onTranscribed,
  onRetake,
}: UseDictationOptions) {
  const [dictationPhase, setDictationPhase] = useState<DictationPhase>("idle");
  const [dictationError, setDictationError] = useState("");
  const [dictationSeconds, setDictationSeconds] = useState(0);
  const [dictationTranscribed, setDictationTranscribed] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const dictationTimerRef = useRef<number | null>(null);
  const dictationSecondsRef = useRef(0);
  const dictationAbortRef = useRef<AbortController | null>(null);
  const pendingAudioRef = useRef<Blob | null>(null);
  const onTranscribedRef = useRef(onTranscribed);
  const onRetakeRef = useRef(onRetake);
  onTranscribedRef.current = onTranscribed;
  onRetakeRef.current = onRetake;

  const clearDictationTimer = useCallback(() => {
    if (dictationTimerRef.current !== null) {
      window.clearInterval(dictationTimerRef.current);
      dictationTimerRef.current = null;
    }
  }, []);

  const stopAudioTracks = useCallback(() => {
    const stream = streamRef.current;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
  }, []);

  /** 取消录音/转写：停止音轨、丢弃片段与在途请求，不遗留任何状态。 */
  const cancelRecording = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.onstop = null;
      try {
        recorder.stop();
      } catch {
        // 已停止/不活动时忽略
      }
    }
    mediaRecorderRef.current = null;
    stopAudioTracks();
    chunksRef.current = [];
    pendingAudioRef.current = null;
    dictationAbortRef.current?.abort();
    dictationAbortRef.current = null;
    clearDictationTimer();
    setDictationSeconds(0);
    setDictationTranscribed(false);
    setDictationError("");
    setDictationPhase("idle");
  }, [clearDictationTimer, stopAudioTracks]);

  /** 把已录片段提交到固定 ASR 快照（停止录音后才提交；失败保留片段可重试）。 */
  const transcribePendingAudio = useCallback(async () => {
    const audio = pendingAudioRef.current;
    if (!audio || audio.size === 0) {
      setDictationError("录音为空，请重新录制。");
      setDictationPhase("error");
      return;
    }
    if (!conversationId) {
      setDictationError("发送首条消息后可使用听写。");
      setDictationPhase("error");
      return;
    }
    setDictationPhase("transcribing");
    setDictationError("");
    const controller = new AbortController();
    dictationAbortRef.current = controller;
    try {
      const projection = await transcribeDictation(
        conversationId,
        audio,
        dictationSecondsRef.current,
        controller.signal
      );
      dictationAbortRef.current = null;
      if (projection.status === "success" && projection.transcript) {
        // 转写结果进入输入框可自由编辑；不自动产生用户消息。
        pendingAudioRef.current = null;
        onTranscribedRef.current(projection.transcript);
        setDictationTranscribed(true);
        setDictationPhase("idle");
      } else {
        // 失败保留录音片段：同一音频可原样重试（受控重试，不换快照）。
        setDictationError(projection.error_message || "转写失败，请重试或重新录制。");
        setDictationPhase("error");
      }
    } catch (error) {
      dictationAbortRef.current = null;
      if (error instanceof DOMException && error.name === "AbortError") {
        // 用户取消转写：不遗留待发送文本与后台请求。
        pendingAudioRef.current = null;
        setDictationPhase("idle");
        return;
      }
      if (error instanceof TypeError) {
        // fetch 网络层失败（断网/服务不可达）映射为可操作中文原因。
        setDictationError("网络异常，转写失败，请检查网络后重试。");
      } else {
        setDictationError(error instanceof Error ? error.message : "转写失败，请重试或重新录制。");
      }
      setDictationPhase("error");
    }
  }, [conversationId]);

  /** 停止录音并提交完整音频（不超过硬上限，到达上限自动停止提交）。 */
  const stopRecordingAndTranscribe = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === "inactive") {
      cancelRecording();
      return;
    }
    clearDictationTimer();
    recorder.onstop = () => {
      // 先在 onstop 里取完数据再停音轨：先停轨会截断 MediaRecorder 数据。
      const blob = new Blob(chunksRef.current, {
        type: recorder.mimeType || "audio/webm",
      });
      chunksRef.current = [];
      stopAudioTracks();
      mediaRecorderRef.current = null;
      pendingAudioRef.current = blob.size > 0 ? blob : null;
      void transcribePendingAudio();
    };
    try {
      recorder.stop();
    } catch {
      cancelRecording();
    }
  }, [cancelRecording, clearDictationTimer, stopAudioTracks, transcribePendingAudio]);

  /** 开始录音：申请麦克风权限（拒绝/无设备/被占用分别给出中文原因）。 */
  const startRecording = useCallback(async () => {
    if (dictationPhase === "recording" || dictationPhase === "transcribing") return;
    if (variant === "new-chat" && !conversationId) {
      setDictationError("发送首条消息后可使用听写。");
      setDictationPhase("error");
      return;
    }
    if (!asr.available) {
      // GQ-03：听写由全局运行凭据驱动，入口恒可用；该分支仅防御未来
      // 调用方传入不可用状态，不再引导前往密钥设置页。
      setDictationError(asr.reason ?? "语音转写能力暂不可用，请稍后重试。");
      setDictationPhase("error");
      return;
    }
    // 重新录制：先清掉上一段转写文本（用户显式选择重录，不留待发送文本）。
    if (dictationTranscribed) {
      onRetakeRef.current();
      setDictationTranscribed(false);
    }
    setDictationError("");
    if (!("MediaRecorder" in window)) {
      setDictationError("当前浏览器不支持录音，请改用键盘输入。");
      setDictationPhase("error");
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      const name = error instanceof DOMException ? error.name : "";
      if (name === "NotAllowedError" || name === "SecurityError") {
        setDictationError("麦克风权限被拒绝，请在浏览器设置中允许麦克风后重试。");
      } else if (name === "NotFoundError" || name === "OverconstrainedError") {
        setDictationError("未检测到可用麦克风设备，请检查设备连接后重试。");
      } else if (name === "NotReadableError") {
        setDictationError("麦克风设备不可用或被其他应用占用，请检查后重试。");
      } else {
        setDictationError("无法启动麦克风，请检查设备与浏览器权限后重试。");
      }
      setDictationPhase("error");
      return;
    }
    streamRef.current = stream;
    chunksRef.current = [];
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream);
    } catch {
      stopAudioTracks();
      setDictationError("当前浏览器不支持录音，请改用键盘输入。");
      setDictationPhase("error");
      return;
    }
    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) chunksRef.current.push(event.data);
    };
    try {
      recorder.start();
    } catch {
      stopAudioTracks();
      setDictationError("无法启动录音，请检查麦克风后重试。");
      setDictationPhase("error");
      return;
    }
    mediaRecorderRef.current = recorder;
    dictationSecondsRef.current = 0;
    setDictationSeconds(0);
    setDictationPhase("recording");
    // 时长计时；到达硬上限自动停止并提交（服务端同款 300 秒上限）。
    const startedAt = Date.now();
    dictationTimerRef.current = window.setInterval(() => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000);
      dictationSecondsRef.current = seconds;
      setDictationSeconds(seconds);
      if (seconds >= MAX_RECORDING_SECONDS) {
        stopRecordingAndTranscribe();
      }
    }, 250);
  }, [
    asr.available,
    asr.reason,
    conversationId,
    dictationPhase,
    dictationTranscribed,
    stopAudioTracks,
    stopRecordingAndTranscribe,
    variant,
  ]);

  useEffect(
    () => () => {
      cancelRecording();
    },
    [cancelRecording, conversationId]
  );

  return {
    phase: dictationPhase,
    error: dictationError,
    seconds: dictationSeconds,
    transcribed: dictationTranscribed,
    canRetryTranscription: pendingAudioRef.current !== null,
    startRecording,
    stopRecordingAndTranscribe,
    cancelRecording,
    retryTranscription: transcribePendingAudio,
  };
}
