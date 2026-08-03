"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { StateBlock } from "@/components/bridges/StateBlock";
import { MainContent } from "@/components/layout/MainContent";
import { useRecentConversations } from "@/lib/recent-conversations";

function formatUpdatedAt(updatedAt?: string): string | null {
  if (!updatedAt) return null;
  const date = new Date(updatedAt);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString("zh-CN", { hour12: false });
}

/**
 * 搜索对话页（Issue 12）。
 *
 * 真实行为：加载当前账户的对话列表，按输入关键词在标题中过滤。
 * 状态完整：加载中 / 加载失败可重试（role=alert，不伪装成空列表）/
 * 空关键词提示 / 无匹配空态（指向新聊天）/ 结果列表链接到对话页。
 */
export default function SearchPageClient() {
  const { conversations, loading, loadError, reload } = useRecentConversations();
  const [keyword, setKeyword] = useState("");

  const results = useMemo(() => {
    const query = keyword.trim();
    if (!query) return [];
    return conversations.filter((conversation) =>
      (conversation.title || "新对话").includes(query)
    );
  }, [conversations, keyword]);

  const searching = keyword.trim().length > 0;

  return (
    <MainContent>
      <section aria-labelledby="search-title" style={{ maxWidth: "46rem", marginInline: "auto" }}>
        <h1 id="search-title" className="sc-section-title">
          搜索对话
        </h1>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          <label htmlFor="conversation-search" style={{ fontSize: "var(--text-sm)", fontWeight: 500 }}>
            关键词
          </label>
          <input
            id="conversation-search"
            type="search"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="输入对话标题中的关键词"
            style={{
              width: "100%",
              minHeight: "var(--target-size)",
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-surface)",
              color: "var(--color-text-primary)",
              fontSize: "var(--text-base)",
            }}
          />
        </div>

        <div style={{ marginTop: "var(--space-6)" }}>
          {loading && conversations.length === 0 ? (
            <StateBlock kind="loading" title="正在加载对话" description="读取当前账户的对话列表。" />
          ) : loadError ? (
            <StateBlock
              kind="error"
              title="对话列表加载失败"
              description={loadError || "请检查连接后重试。"}
              actionLabel="重试"
              onAction={() => void reload()}
            />
          ) : !searching ? (
            <p style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
              输入关键词搜索你的对话。
            </p>
          ) : results.length === 0 ? (
            <div>
              <p style={{ color: "var(--color-text-secondary)" }}>没有匹配的对话。</p>
              <p style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)", marginTop: "var(--space-2)" }}>
                换个关键词试试，或<Link href="/">开始新聊天</Link>。
              </p>
            </div>
          ) : (
            <ul role="list" style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
              {results.map((conversation) => {
                const updatedAt = formatUpdatedAt(conversation.updated_at);
                return (
                  <li key={conversation.conversation_id}>
                    <Link
                      href={`/chat/${conversation.conversation_id}`}
                      style={{
                        display: "flex",
                        alignItems: "baseline",
                        justifyContent: "space-between",
                        gap: "var(--space-3)",
                        padding: "var(--space-3) var(--space-4)",
                        borderRadius: "var(--radius-md)",
                        border: "1px solid var(--color-border)",
                        backgroundColor: "var(--color-surface)",
                        color: "var(--color-text-primary)",
                        textDecoration: "none",
                      }}
                    >
                      <span
                        style={{
                          minWidth: 0,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          fontWeight: 500,
                        }}
                      >
                        {conversation.title || "新对话"}
                      </span>
                      {updatedAt && (
                        <span
                          style={{
                            flexShrink: 0,
                            fontSize: "var(--text-xs)",
                            color: "var(--color-text-tertiary)",
                          }}
                        >
                          更新于 {updatedAt}
                        </span>
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </section>
    </MainContent>
  );
}
