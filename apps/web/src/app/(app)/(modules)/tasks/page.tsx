import { TaskSchedule } from "@/components/account/TaskSchedule";
import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "任务安排 — BridGes",
};

/**
 * 任务安排（Issue 33）：QQ SMTP 邮件提醒。
 *
 * 配置并验证 QQ 邮箱授权码（自发自收），用自然语言创建带时区的提醒，
 * 经预览确认后由本地调度器按时投递；支持暂停/恢复/编辑/取消/手动补发
 * 与投递记录查看。收件人与发件人固定为当前账户 QQ 邮箱。
 */
export default function TasksPage() {
  return (
    <MainContent>
      <section
        aria-labelledby="tasks-title"
        style={{ maxWidth: "52rem", marginInline: "auto" }}
      >
        <h1 id="tasks-title" className="sc-section-title">
          任务安排
        </h1>
        <TaskSchedule />
      </section>
    </MainContent>
  );
}
