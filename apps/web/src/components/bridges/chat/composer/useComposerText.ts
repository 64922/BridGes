"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface ComposerPrefill {
  text: string;
  nonce: number;
}

interface UseComposerTextOptions {
  /** 外部预填请求（建议卡等）：nonce 变化时把 text 作为结构化意图填入并聚焦。 */
  prefill: ComposerPrefill | null;
  /** Enter（非 Shift）提交；输入法组合期间不触发。 */
  onSubmit: () => void;
}

/**
 * 输入区文本行为的唯一所有者：正文、预填、自动增高与 Enter 提交。
 *
 * Enter 发送、Shift+Enter 换行；中文输入法组合期间的 Enter（确认候选）
 * 既不发送也不阻止默认行为，避免误发。
 */
export function useComposerText({ prefill, onSubmit }: UseComposerTextOptions) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);
  const onSubmitRef = useRef(onSubmit);
  onSubmitRef.current = onSubmit;

  const autoGrow = useCallback(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 12 * 16)}px`;
  }, []);

  const resetText = useCallback(() => {
    setText("");
    requestAnimationFrame(autoGrow);
  }, [autoGrow]);

  /** 用外部结果（如听写转写）替换正文；可选择把焦点交还输入框。 */
  const replaceText = useCallback(
    (value: string, focus = false) => {
      setText(value);
      requestAnimationFrame(() => {
        if (focus) textareaRef.current?.focus();
        autoGrow();
      });
    },
    [autoGrow]
  );

  const applyPrefill = useCallback(
    (value: string) => {
      setText((current) => (current.startsWith(value) ? current : `${value}${current}`));
      requestAnimationFrame(() => {
        const element = textareaRef.current;
        if (!element) return;
        element.focus();
        element.setSelectionRange(element.value.length, element.value.length);
        autoGrow();
      });
    },
    [autoGrow]
  );

  const prefillNonce = prefill?.nonce;
  useEffect(() => {
    if (prefillNonce === undefined || !prefill) return;
    applyPrefill(prefill.text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillNonce]);

  const onChange = useCallback(
    (event: React.ChangeEvent<HTMLTextAreaElement>) => {
      setText(event.target.value);
      autoGrow();
    },
    [autoGrow]
  );

  const onKeyDown = useCallback((event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 组合期间的 Enter 属于输入法确认：不发送、不阻止输入法自身的默认行为。
    if (event.nativeEvent.isComposing || event.keyCode === 229 || composingRef.current) {
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSubmitRef.current();
    }
  }, []);

  const onCompositionStart = useCallback(() => {
    composingRef.current = true;
  }, []);

  const onCompositionEnd = useCallback(() => {
    composingRef.current = false;
  }, []);

  return {
    text,
    textareaRef,
    onChange,
    onKeyDown,
    onCompositionStart,
    onCompositionEnd,
    resetText,
    replaceText,
  };
}
