import { AppShell } from "@/components/layout/AppShell";

/**
 * 普通用户功能模块布局（Issue 12）。
 *
 * 搜索 / 本地知识库 / 任务安排 / 插件共用全局聊天外壳（可折叠侧栏，
 * 无顶栏）。侧栏本身提供不依赖浏览器后退的返回新聊天路径。
 */
export default function ModulesLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppShell>{children}</AppShell>;
}
