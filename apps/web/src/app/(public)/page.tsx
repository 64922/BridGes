import { cookies } from "next/headers";

import { NewChatHome } from "@/components/bridges/NewChatHome";
import { WelcomePage } from "@/components/bridges/WelcomePage";

export const metadata = {
  title: "BridGes",
};

/**
 * 根路径入口：已登录用户进入新聊天首页；未登录用户先看到启动欢迎页，
 * 由其右上角的「登录 / 注册」按钮进入对应流程。
 */
export default function PublicEntryPage() {
  const hasSession = Boolean(cookies().get("bridges_session")?.value);

  if (!hasSession) {
    return <WelcomePage />;
  }

  return <NewChatHome />;
}
