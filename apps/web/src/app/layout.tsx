import type { Metadata } from "next";

import { Providers } from "./providers";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: "BridGes",
  description: "长期科学学习与表达伙伴",
};

const restoreThemeScript = `
try {
  const theme = localStorage.getItem("bridges-template-theme");
  if (theme === "dark" || theme === "light") {
    document.documentElement.dataset.theme = theme;
  }
} catch {}
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: restoreThemeScript }} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
