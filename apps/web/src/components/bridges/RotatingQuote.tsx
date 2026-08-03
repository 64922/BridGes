"use client";

import { useEffect, useState } from "react";

import { LEARNING_QUOTES, QUOTE_ROTATE_MS } from "@/lib/learning-quotes";

/**
 * 空白态输入区顶部的可变文字：恰好五条学习名言按参考网页机制轮换。
 *
 * - 每 QUOTE_ROTATE_MS 切换一条，切换经 opacity 淡入淡出（时长走
 *   --motion-duration-base 令牌）；
 * - 尊重减少动态效果设置：prefers-reduced-motion 时不启动轮换、不做
 *   过渡，静止展示第一条；
 * - 不放入 live region：装饰性轮播不打扰屏幕阅读器，朗读光标经过时
 *   按普通文本朗读当前一条。
 */
export function RotatingQuote() {
  const [index, setIndex] = useState(0);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    let interval: number | null = null;
    let fadeTimer: number | null = null;

    const stop = () => {
      if (interval !== null) {
        window.clearInterval(interval);
        interval = null;
      }
      if (fadeTimer !== null) {
        window.clearTimeout(fadeTimer);
        fadeTimer = null;
      }
    };

    const start = () => {
      stop();
      setIndex(0);
      setVisible(true);
      interval = window.setInterval(() => {
        setVisible(false);
        // 淡出时长与 --motion-duration-base（200ms）一致，令牌调整时需同步
        fadeTimer = window.setTimeout(() => {
          setIndex((current) => (current + 1) % LEARNING_QUOTES.length);
          setVisible(true);
        }, 200);
      }, QUOTE_ROTATE_MS);
    };

    // 尊重减少动态效果设置，并跟随系统设置在运行中切换
    const apply = () => {
      if (media.matches) {
        stop();
        setIndex(0);
        setVisible(true);
      } else {
        start();
      }
    };
    apply();
    media.addEventListener("change", apply);
    return () => {
      stop();
      media.removeEventListener("change", apply);
    };
  }, []);

  const quote = LEARNING_QUOTES[index];
  return (
    <p
      data-testid="empty-quote"
      data-quote-index={index}
      data-quote-count={LEARNING_QUOTES.length}
      style={{
        margin: 0,
        fontSize: "var(--text-xl)",
        textAlign: "center",
        fontFamily: "var(--font-serif)",
        fontWeight: 600,
        color: "var(--color-text-primary)",
        opacity: visible ? 1 : 0,
        transition: "opacity var(--motion-duration-base) var(--motion-easing)",
      }}
    >
      {quote.text}
      <span
        style={{
          display: "block",
          marginTop: "var(--space-2)",
          fontSize: "var(--text-sm)",
          fontFamily: "var(--font-sans)",
          fontWeight: 400,
          color: "var(--color-text-tertiary)",
        }}
      >
        —— {quote.source}
      </span>
    </p>
  );
}
