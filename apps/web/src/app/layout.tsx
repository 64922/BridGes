import type { Metadata } from "next";

import { Providers } from "./providers";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: "BridGes",
  description: "长期科学学习与表达伙伴",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
