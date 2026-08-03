import { StateBlock } from "@/components/bridges/StateBlock";
import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "任务安排 — BridGes",
};

/**
 * 任务安排（Issue 12 稳定入口）。
 *
 * 后端尚无账户级任务能力，因此这里呈现真实、可操作的空状态：
 * 说明当前账户没有任务的原因，并给出真实的下一步（返回新聊天）。
 */
export default function TasksPage() {
  return (
    <MainContent>
      <section aria-labelledby="tasks-title" style={{ maxWidth: "46rem", marginInline: "auto" }}>
        <h1 id="tasks-title" className="sc-section-title">
          任务安排
        </h1>
        <StateBlock
          kind="empty"
          title="当前账户还没有任务"
          description="当前版本尚未开放账户级任务安排，因此没有任何任务可以创建或跟踪。你可以先回到新聊天描述你的学习目标；任务能力开放后，本页会展示你的真实任务。"
          actionLabel="返回新聊天"
          actionHref="/"
        />
      </section>
    </MainContent>
  );
}
