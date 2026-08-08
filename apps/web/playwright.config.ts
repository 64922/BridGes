import { defineConfig, devices } from "@playwright/test";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

const PORT = process.env.PORT || "3000";
const API_PORT = process.env.API_PORT || "8000";
const BASE_URL = `http://127.0.0.1:${PORT}`;

// 仓库根目录（playwright.config.ts 位于 apps/web 下）
const REPO_ROOT = path.resolve(__dirname, "../..");

// 统一测试运行时定位（收尾 issue 01 步骤 1）：显式使用仓库虚拟环境里的
// Python，不依赖 PATH 上的 Anaconda/系统 Python。可用 BRIDGES_PYTHON 覆盖。
function resolveVenvPython(): string {
  if (process.env.BRIDGES_PYTHON) return process.env.BRIDGES_PYTHON;
  const candidate =
    os.platform() === "win32"
      ? path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
      : path.join(REPO_ROOT, ".venv", "bin", "python");
  return candidate;
}
const PYTHON = resolveVenvPython();

// E2E 数据目录：绝对路径 + 启动前显式创建父目录（不要求人工预建
// .e2e-data；唯一绝对 SQLite 路径避免相对工作目录解析差异）。
// 允许 E2E_DATA_DIR 覆盖：并行会话共用同一工作区时用独立目录隔离
// 数据库，避免多套 webServer 同时写同一 SQLite。
const E2E_DATA_DIR = process.env.E2E_DATA_DIR
  ? path.resolve(REPO_ROOT, process.env.E2E_DATA_DIR)
  : path.resolve(REPO_ROOT, "apps/web/.e2e-data");
fs.mkdirSync(E2E_DATA_DIR, { recursive: true });

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
      command: `"${PYTHON}" -m bridges.cli.main api --port ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
      // Issue 11：聊天纵向切片要求 bridges.db 持久化；e2e API 进程注入
      // 临时数据目录与测试密钥（仓库 .gitignore 已排除 .e2e-data）。
      // Issue 33：SMTP/IMAP 指向本地假邮件服务器（scripts/e2e_mail_server.py）。
      env: {
        BRIDGES_DATABASE_URL: `sqlite:///${path.join(E2E_DATA_DIR, "bridges.db").replace(/\\/g, "/")}`,
        BRIDGES_SECRET_KEY: "e2e-chat-test-secret-key",
        BRIDGES_ENVIRONMENT: "test",
        // Issue 02：e2e 是真实部署形态——后台执行器线程显式启用
        // （test 环境默认不自动启动，由 pytest 显式驱动）。
        BRIDGES_GENERATION_EXECUTOR: "1",
        BRIDGES_SMTP_HOST: "127.0.0.1",
        BRIDGES_SMTP_PORT: "8025",
        BRIDGES_SMTP_PLAIN: "true",
        BRIDGES_IMAP_HOST: "127.0.0.1",
        BRIDGES_IMAP_PORT: "8143",
        BRIDGES_IMAP_PLAIN: "true",
      },
    },
    {
      // Issue 33：本地假 SMTP+IMAP 服务器（自发自收验证与真实投递落点）。
      command: `"${PYTHON}" ../../scripts/e2e_mail_server.py`,
      url: "http://127.0.0.1:8026/health",
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      // Issue 04：真实后台执行器（与生产 BridGes start 拓扑一致：api +
      // worker + web）。摄取/检索披露依赖文档入索引，E2E 必须真实处理
      // 而非 mock 摄取终态。Playwright 对相同 URL 的条目视为已存在而
      // 跳过启动，因此 worker 经 e2e_worker.py 暴露独立健康端点 8027。
      command: `"${PYTHON}" ../../scripts/e2e_worker.py`,
      url: "http://127.0.0.1:8027/health",
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
      env: {
        BRIDGES_DATABASE_URL: `sqlite:///${path.join(E2E_DATA_DIR, "bridges.db").replace(/\\/g, "/")}`,
        BRIDGES_SECRET_KEY: "e2e-chat-test-secret-key",
        BRIDGES_ENVIRONMENT: "test",
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
