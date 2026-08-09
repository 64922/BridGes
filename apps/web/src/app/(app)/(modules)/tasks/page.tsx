import Link from "next/link";

import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "任务安排 — BridGes",
};

/** 旧书签兼容页：退役任务安排后把用户带回聊天学习入口。 */
export default function TasksPage() {
  return (
    <MainContent>
      <section
        aria-labelledby="tasks-title"
        style={{ maxWidth: "52rem", marginInline: "auto" }}
      >
        <h1 id="tasks-title" className="sc-section-title">任务安排已退役</h1>
        <p>学习任务、复习计划和邮件提醒已停止使用。</p>
        <p>请在学习模式聊天中继续，学习进度会保留在连续教学回合里。</p>
        <Link href="/" className="sc-button sc-button-primary">
          返回聊天学习
        </Link>
      </section>
    </MainContent>
  );
}
