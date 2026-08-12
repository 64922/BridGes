import { defineConfig, devices } from "@playwright/test";
import { execSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

/**
 * 收尾 smoke 专用 Playwright 配置（issue 01：Windows 可执行反馈环）。
 *
 * 与主配置的差异——本配置服务于「干净 PowerShell 单命令、连续可重复」：
 * - 每次运行生成唯一绝对数据目录（.tmp/e2e-closeout/run-*，启动前预建），
 *   旧 SQLite WAL、PID 或残留进程不可能影响本次运行（AC1/AC2）；
 * - 端口由单一路径决定，主进程与 worker 进程取值一致（AC2）：
 *   · 经 scripts/closeout_smoke.py 运行时由驱动下发 BRIDGES_CLOSEOUT_PORTS
 *     （驱动用 Python 选空闲端口，写入环境变量，两个进程读到同一组值）；
 *   · 直接运行 npx playwright test -c playwright.closeout.config.ts 时使用
 *     固定默认端口，加载时预检占用并立即报错（绝不静默挂起或复用旧进程）；
 * - reuseExistingServer 固定 false：绝不复用陈旧进程，保证每运行全新；
 * - workers=1 串行、trace=on + 失败截图：失败保留完整产物（AC7）；
 * - 显式使用仓库虚拟环境 Python（不依赖 PATH 的 Anaconda/系统 Python）。
 */

const REPO_ROOT = path.resolve(__dirname, "../..");

function resolveVenvPython(): string {
  if (process.env.BRIDGES_PYTHON) return process.env.BRIDGES_PYTHON;
  return os.platform() === "win32"
    ? path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
    : path.join(REPO_ROOT, ".venv", "bin", "python");
}
const PYTHON = resolveVenvPython();

function isPortFree(port: number): boolean {
  // 用 bind 探测（SO_REUSEADDR 后仅监听进程使 bind 失败，TIME_WAIT 幽灵
  // 连接不误报）。脚本经 base64 传给 Python，避免 cmd 对多行参数改字面。
  const script =
    "import socket\n" +
    "s = socket.socket()\n" +
    "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n" +
    "occupied = 0\n" +
    "try:\n" +
    `    s.bind(('127.0.0.1', ${port}))\n` +
    "except OSError:\n" +
    "    occupied = 1\n" +
    "s.close()\n" +
    "print(occupied)\n";
  const payload = Buffer.from(script, "utf-8").toString("base64");
  const output = execSync(
    `"${PYTHON}" -c "import base64;exec(base64.b64decode('${payload}').decode('utf-8'))"`,
    { encoding: "utf-8" }
  );
  return output.trim() === "0";
}

interface Ports {
  api: number;
  web: number;
  smtp: number;
  imap: number;
  mailHttp: number;
}

// 直接运行（不经驱动脚本）时的默认端口：范围固定但预检占用，冲突立即报错。
const DEFAULT_PORTS: Ports = { api: 8910, web: 8911, smtp: 8912, imap: 8913, mailHttp: 8914 };

function resolvePorts(): Ports {
  const fromEnv = process.env.BRIDGES_CLOSEOUT_PORTS;
  if (fromEnv) {
    const values = fromEnv.split(",").map((item) => parseInt(item.trim(), 10));
    const names: (keyof Ports)[] = ["api", "web", "smtp", "imap", "mailHttp"];
    if (values.length === names.length && values.every((value) => Number.isInteger(value) && value > 0)) {
      const ports: Ports = { api: 0, web: 0, smtp: 0, imap: 0, mailHttp: 0 };
      names.forEach((name, index) => {
        ports[name] = values[index];
      });
      return ports;
    }
    throw new Error("BRIDGES_CLOSEOUT_PORTS 格式应为 api,web,smtp,imap,mailHttp 五个端口号。");
  }
  // 预检只在主进程执行（webServer 启动前）：worker 进程求值时本配置启动的
  // API 已在默认端口监听，会误报「被自家服务占用」。
  const isWorker = process.env.TEST_WORKER_INDEX !== undefined;
  if (!isWorker) {
    for (const [name, port] of Object.entries(DEFAULT_PORTS) as [keyof Ports, number][]) {
      if (!isPortFree(port)) {
        throw new Error(
          `端口 ${port}（${name}）已被占用。请先停止占用该端口的进程，` +
            "或通过 scripts/closeout_smoke.py 运行（自动选择空闲端口）。"
        );
      }
    }
  }
  return DEFAULT_PORTS;
}

const PORTS = resolvePorts();

// 每次运行唯一数据目录（绝对路径，启动前创建父目录）。worker 进程不消费
// 本目录（只读取端口与 baseURL），主/worker 各自求值不一致也无影响。
const RUN_ID = `run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const RUN_DIR = path.resolve(REPO_ROOT, ".tmp", "e2e-closeout", RUN_ID);
fs.mkdirSync(RUN_DIR, { recursive: true });

const BASE_URL = `http://127.0.0.1:${PORTS.web}`;
const DATABASE_URL = `sqlite:///${path.join(RUN_DIR, "bridges.db").replace(/\\/g, "/")}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: /closeout-smoke\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    trace: "on",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command: `"${PYTHON}" -m bridges.cli.main api --host 127.0.0.1 --port ${PORTS.api}`,
      url: `http://127.0.0.1:${PORTS.api}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        BRIDGES_ENVIRONMENT: "test",
        BRIDGES_DATABASE_URL: DATABASE_URL,
        BRIDGES_SECRET_KEY: "e2e-closeout-secret-key-01",
        BRIDGES_CLOSEOUT_FIXTURES: "true",
        BRIDGES_API_HOST: "127.0.0.1",
        BRIDGES_API_PORT: String(PORTS.api),
        // Issue 03：与全局 playwright.config.ts 对齐——收尾真实链路同样
        // 需要后台生成执行器（Issue 02 迁移后 test 环境默认不自动启动）。
        BRIDGES_GENERATION_EXECUTOR: "1",
        BRIDGES_SMTP_HOST: "127.0.0.1",
        BRIDGES_SMTP_PORT: String(PORTS.smtp),
        BRIDGES_SMTP_PLAIN: "true",
        BRIDGES_IMAP_HOST: "127.0.0.1",
        BRIDGES_IMAP_PORT: String(PORTS.imap),
        BRIDGES_IMAP_PLAIN: "true",
      },
    },
    {
      // 假 SMTP+IMAP 服务器：本次运行专属端口（邮箱在进程内存）。
      command: `"${PYTHON}" ../../scripts/e2e_mail_server.py --smtp-port ${PORTS.smtp} --imap-port ${PORTS.imap} --http-port ${PORTS.mailHttp}`,
      url: `http://127.0.0.1:${PORTS.mailHttp}/health`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `npm run dev -- -p ${PORTS.web}`,
      url: BASE_URL,
      reuseExistingServer: false,
      timeout: 180_000,
      // Next 代理 /api/* 的落点：指向本次运行的 API 端口（默认 8000）。
      env: {
        API_BASE_URL: `http://127.0.0.1:${PORTS.api}`,
      },
    },
  ],
});
