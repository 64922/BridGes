export const metadata = {
  title: "Science Companion",
  description: "长期科学学习与表达伙伴",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
