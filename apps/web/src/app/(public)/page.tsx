import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { NewChatHome } from "@/components/bridges/NewChatHome";

export const metadata = {
  title: "BridGes — 新聊天",
};

/**
 * 根路径是登录后的新聊天首页；未登录用户先进入新版登录页。
 */
export default function PublicEntryPage() {
  const hasSession = Boolean(cookies().get("bridges_session")?.value);

  if (!hasSession) {
    redirect("/login");
  }

  return <NewChatHome />;
}
