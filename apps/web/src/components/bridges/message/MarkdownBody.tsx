"use client";

import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

import styles from "./MarkdownBody.module.css";

const REMARK_PLUGINS = [remarkGfm, remarkBreaks];
const REHYPE_PLUGINS = [rehypeSanitize];

const components: Components = {
  a({ href, children }) {
    const external =
      typeof href === "string" && /^https?:\/\//i.test(href);
    return (
      <a
        href={href}
        {...(external
          ? { target: "_blank", rel: "noopener noreferrer" }
          : {})}
      >
        {children}
      </a>
    );
  },
  img({ src, alt }) {
    // 正文图片不自动加载远程资源；保留可点击链接或替代文本。
    if (typeof src === "string" && /^https?:\/\//i.test(src)) {
      return (
        <a href={src} target="_blank" rel="noopener noreferrer">
          {alt || "查看图片"}
        </a>
      );
    }
    return <span>{alt || "图片"}</span>;
  },
  table({ children }) {
    return (
      <div className={styles.tableScroll}>
        <table>{children}</table>
      </div>
    );
  },
};

/**
 * 助手连续正文：流式与历史消息共用同一 Markdown 渲染路径。
 *
 * 只渲染可公开的回答内容；原始 HTML 不执行，危险协议 URL 由
 * react-markdown 的默认 URL 转换与 rehype-sanitize 清理。流式光标由
 * CSS 挂在最后一个内容块末尾，不进入可访问名称，也不放入 live region。
 */
export function MarkdownBody({
  text,
  streaming = false,
}: {
  text: string;
  streaming?: boolean;
}) {
  const className = [
    styles.markdown,
    streaming ? styles.streaming : "",
    streaming && text.length === 0 ? styles.streamingEmpty : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={className} data-testid="assistant-body">
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
