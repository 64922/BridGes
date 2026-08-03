import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.PORT || "3000";
const API_PORT = process.env.API_PORT || "8000";
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  // 限制本地并发，避免 Windows 上多个重页面同时触发 Next 开发编译；
  // CI 保持串行，视觉快照与跨页面状态回归可稳定复现。
  workers: process.env.CI ? 1 : 4,
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [
    // ADR-0023：BridGes 只面向桌面浏览器，不创建移动端 E2E 项目。
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command: `python -m bridges.cli.main api --port ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
      // Issue 11：聊天纵向切片要求 bridges.db 持久化；e2e API 进程注入
      // 临时数据目录与测试密钥（仓库 .gitignore 已排除 .e2e-data）。
      env: {
        BRIDGES_DATABASE_URL: `sqlite:///./.e2e-data/bridges.db`,
        BRIDGES_SECRET_KEY: "e2e-chat-test-secret-key",
      },
    },
    {
      command: "npm run dev",
      url: BASE_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
