"use client";

import { useEffect, useState } from "react";

import { SkipLink } from "@/components/design-system/SkipLink";
import { ChatSidebar, type RecentConversation } from "./ChatSidebar";

export const DEMO_RECENTS: RecentConversation[] = [
  { id: "c1", title: "拉格朗日方程的物理意义是什么", mode: "学习" },
  { id: "c2", title: "帮我把课程论文摘要从翻译腔改自然", mode: "日常陪伴" },
  { id: "c3", title: "arXiv 上近一年量子纠错综述有哪些", mode: "学习" },
  { id: "c4", title: "研究生三年生涯规划怎么排优先级", mode: "日常陪伴" },
  { id: "c5", title: "这段 Python 报错堆栈我看不懂，帮我逐行解释一下是什么原因导致的", mode: "日常陪伴" },
];

const PROFILE_STORAGE_KEY = "bridges-template-profile";
const DEFAULT_ACCOUNT_NAME = "示例账户";

interface TemplateShellProps {
  activeConversation?: string;
  activeModule?: string;
  children: React.ReactNode;
}

/**
 * 桌面模板主壳：可折叠侧栏 + 主内容区。
 * 供 Issue 04 的六类页面模板复用，后续功能 Issue 在此骨架上接入真实数据。
 * 侧栏账户名读取设置模板写入本机的个人资料，与「个人资料」修改保持同步。
 */
export function TemplateShell({ activeConversation, activeModule, children }: TemplateShellProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [accountName, setAccountName] = useState(DEFAULT_ACCOUNT_NAME);

  useEffect(() => {
    const stored = window.localStorage.getItem(PROFILE_STORAGE_KEY);
    if (!stored) return;
    try {
      const profile = JSON.parse(stored) as { username?: unknown };
      if (typeof profile.username === "string" && profile.username.trim()) {
        setAccountName(profile.username);
      }
    } catch {
      window.localStorage.removeItem(PROFILE_STORAGE_KEY);
    }
  }, []);

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <SkipLink />
      <ChatSidebar
        activeConversation={activeConversation}
        activeModule={activeModule}
        recents={DEMO_RECENTS}
        accountName={accountName}
        collapsed={collapsed}
        onToggleCollapsed={() => setCollapsed((value) => !value)}
      />
      <main
        id="main-content"
        tabIndex={-1}
        data-testid="main-content"
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          maxHeight: "100vh",
          overflowY: "auto",
        }}
      >
        {children}
      </main>
    </div>
  );
}
