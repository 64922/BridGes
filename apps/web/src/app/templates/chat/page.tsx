import { Suspense } from "react";

import { ChatTemplate } from "./chat-template";

export const metadata = { title: "BridGes — 聊天内容模板" };

export default function ChatTemplatePage() {
  return (
    <Suspense>
      <ChatTemplate />
    </Suspense>
  );
}
