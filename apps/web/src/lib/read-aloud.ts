"use client";

/**
 * 全局单活动朗读播放会话（Issue 30）。
 *
 * 页面同时只允许一个活动播放会话：新消息开始播放前先停止旧会话，
 * 切换对话/离开页面/切换账户时由宿主调用 stop() 安全停止。所有状态
 * 变更通过订阅回调通知控件；刷新页面后不伪造任何播放状态（会话是
 * 纯运行时对象，不存在持久化状态）。
 */

export interface ReadAloudPlaybackState {
  messageId: string;
  playing: boolean;
  /** 当前播放位置（秒）。 */
  currentTime: number;
  /** 总时长（秒）；音频元数据未加载完时为 0。 */
  duration: number;
}

export type PlaybackListener = (state: ReadAloudPlaybackState) => void;

const MAX_PLAYBACK_SECONDS = 300;

class ReadAloudSession {
  private audio: HTMLAudioElement | null = null;
  private messageId: string | null = null;
  private listeners = new Set<PlaybackListener>();
  private timer: number | null = null;

  /** 订阅播放状态；返回取消订阅函数。 */
  subscribe(listener: PlaybackListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** 当前是否正在播放指定消息（供按钮显示选中态）。 */
  isActive(messageId: string): boolean {
    return this.messageId === messageId && this.audio !== null;
  }

  /** 开始播放指定消息的朗读音频；自动停止旧会话（单活动会话）。 */
  play(url: string, messageId: string): void {
    if (this.audio === null) {
      this.audio = new Audio();
      this.audio.addEventListener("timeupdate", () => this.emit());
      this.audio.addEventListener("durationchange", () => this.emit());
      this.audio.addEventListener("ended", () => this.emit());
      this.audio.addEventListener("error", () => this.emit());
    }
    // 切换回答：先安全停止旧播放，绝不并行双音频。
    if (this.messageId !== messageId) {
      this.audio.pause();
      this.audio.removeAttribute("src");
      this.audio.load();
      this.messageId = null;
    }
    if (this.audio.src !== url) {
      this.audio.src = url;
    }
    this.messageId = messageId;
    // 播放状态通过订阅回调即时反映，无本地持久化（刷新即重置为未播放）。
    void this.audio.play().catch(() => this.emit());
    this.startTimer();
    this.emit();
  }

  /** 暂停当前播放（可继续）。 */
  pause(): void {
    this.audio?.pause();
    this.emit();
  }

  /** 从暂停位置继续播放。 */
  resume(): void {
    if (this.audio === null || this.audio.src === "") return;
    void this.audio.play().catch(() => this.emit());
    this.startTimer();
    this.emit();
  }

  /** 停止当前播放并复位到开头。 */
  stop(): void {
    if (this.audio !== null) {
      this.audio.pause();
      this.audio.removeAttribute("src");
      this.audio.load();
    }
    this.messageId = null;
    this.clearTimer();
    this.emit();
  }

  /** 停止所有播放并释放音频元素（页面卸载/账户切换时调用）。 */
  dispose(): void {
    this.stop();
    if (this.audio !== null) {
      this.audio.src = "";
      this.audio = null;
    }
    this.listeners.clear();
  }

  private startTimer(): void {
    if (this.timer !== null) return;
    this.timer = window.setInterval(() => {
      if (this.audio === null || this.audio.paused || this.audio.ended) {
        if (this.audio !== null && this.audio.ended) this.stop();
        else this.clearTimer();
        return;
      }
      // 防呆：超过上限视为异常，安全停止。
      if (this.audio.currentTime > MAX_PLAYBACK_SECONDS) {
        this.stop();
        return;
      }
      this.emit();
    }, 500);
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
  }

  private emit(): void {
    const state: ReadAloudPlaybackState = {
      messageId: this.messageId ?? "",
      playing: this.audio !== null && !this.audio.paused && !this.audio.ended,
      currentTime: this.audio?.currentTime ?? 0,
      duration: this.audio?.duration ?? 0,
    };
    this.listeners.forEach((listener) => listener(state));
  }
}

/** 应用级唯一朗读会话（单活动播放）。 */
export const readAloudSession = new ReadAloudSession();
