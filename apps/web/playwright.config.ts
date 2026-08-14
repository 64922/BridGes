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

// Issue 06：每次运行生成唯一隔离目录，避免残留旧库/并发 web server 错用
// 上一次或开发环境数据库。允许 E2E_DATA_DIR 覆盖以支持显式指定目录。
// 注意：本配置模块会在主进程与每个 worker 进程分别求值，webServer 只由
// 主进程消费。Playwright 为 worker 进程设置 TEST_WORKER_INDEX，因此只在
// 主进程创建 run 目录/做清理（worker 进程各自求值只生成 RUN_ID 但不再
// 落盘空目录）；测试进程通过磁盘上的 run-<run_id> 目录与后端报告的
// run_id/路径指纹核对（见 issue06 spec）。
const IS_WORKER_PROCESS = process.env.TEST_WORKER_INDEX !== undefined;
const RUN_ID = `run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const E2E_DATA_DIR = process.env.E2E_DATA_DIR
  ? path.resolve(REPO_ROOT, process.env.E2E_DATA_DIR)
  : path.resolve(REPO_ROOT, ".tmp", "e2e-run", RUN_ID);
if (!IS_WORKER_PROCESS) {
  fs.mkdirSync(E2E_DATA_DIR, { recursive: true });
}

const DATABASE_PATH = path.join(E2E_DATA_DIR, "bridges.db");
const DATABASE_URL = `sqlite:///${DATABASE_PATH.replace(/\\/g, "/")}`;

// Issue 06（AC6）：默认目录下的陈旧 run-* 目录按现有清理规则回收（保留
// 7 天），失败诊断产物留在最近一次 run 目录中供排查；含账户数据的数据库
// 文件不进入任何 CI artifact（仓库 .gitignore 已排除 .tmp/）。本清理只删
// 超过 7 天的目录，本次运行新创建的目录绝不触碰；并发进程重复清理幂等。
function cleanupStaleRunDirs(): void {
  if (process.env.E2E_DATA_DIR) return; // 显式目录由调用方负责生命周期
  const runRoot = path.resolve(REPO_ROOT, ".tmp", "e2e-run");
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(runRoot, { withFileTypes: true });
  } catch {
    return; // 目录尚不存在时无需清理
  }
  const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
  for (const entry of entries) {
    if (!entry.isDirectory() || !entry.name.startsWith("run-")) continue;
    try {
      const stat = fs.statSync(path.join(runRoot, entry.name));
      if (stat.mtimeMs < cutoff) {
        fs.rmSync(path.join(runRoot, entry.name), {
          recursive: true,
          force: true,
        });
      }
    } catch {
      // 与其他进程竞争删除时忽略，不阻塞配置加载
    }
  }
}
if (!IS_WORKER_PROCESS) {
  cleanupStaleRunDirs();
}

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
      // Issue 06：必须等待 schema-ready 健康检查（未就绪返回 503），而非
      // 仅端口可连/HTTP 200；后端完成迁移并校验核心表后才开始测试。
      url: `http://127.0.0.1:${API_PORT}/health/ready`,
      // Issue 06：绝不复用已有进程——Playwright 复用时跳过 URL 轮询，无法
      // 核对被复用服务的数据库路径/run ID，可能误连上一次或开发环境数据库。
      // 初始化失败时启动明确失败，而不是在首个会话请求中报 500。
      reuseExistingServer: false,
      timeout: 60_000,
      // Issue 11：聊天纵向切片要求 bridges.db 持久化；e2e API 进程注入
      // 临时数据目录与测试密钥（仓库 .gitignore 已排除 .tmp/e2e-run）。
      // Issue 33：SMTP/IMAP 指向本地假邮件服务器（scripts/e2e_mail_server.py）。
      env: {
        BRIDGES_DATABASE_URL: DATABASE_URL,
        BRIDGES_SECRET_KEY: "e2e-chat-test-secret-key",
        BRIDGES_ENVIRONMENT: "test",
        BRIDGES_RUN_ID: RUN_ID,
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
      // 不复用陈旧进程：邮箱状态在进程内存中，复用会丢失本 run 的投递。
      command: `"${PYTHON}" ../../scripts/e2e_mail_server.py`,
      url: "http://127.0.0.1:8026/health",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      // Issue 04：真实后台执行器（与生产 BridGes start 拓扑一致：api +
      // worker + web）。摄取/检索披露依赖文档入索引，E2E 必须真实处理
      // 而非 mock 摄取终态。Playwright 对相同 URL 的条目视为已存在而
      // 跳过启动，因此 worker 经 e2e_worker.py 暴露独立健康端点 8027；
      // Issue 06：worker 健康端点在数据库就绪前返回 503，且不复用陈旧
      // 进程（陈旧 worker 连接的是上一次运行的数据库）。
      command: `"${PYTHON}" ../../scripts/e2e_worker.py`,
      url: "http://127.0.0.1:8027/health",
      reuseExistingServer: false,
      timeout: 30_000,
      env: {
        BRIDGES_DATABASE_URL: DATABASE_URL,
        BRIDGES_SECRET_KEY: "e2e-chat-test-secret-key",
        BRIDGES_ENVIRONMENT: "test",
        BRIDGES_RUN_ID: RUN_ID,
      },
    },
    {
      // 前端 dev server 是无状态代理（/api/* 转发到本 run 新启动的 API），
      // 复用本地已运行的 npm run dev 不影响数据库生命周期与 run ID 核对；
      // 因此本地保留复用加速迭代，CI 仍强制全新启动。
      command: "npm run dev",
      url: BASE_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
