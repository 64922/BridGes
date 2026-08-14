import * as fs from "fs";
import * as path from "path";

import { describe, expect, it } from "vitest";

import playwrightConfig from "../../playwright.config";

/**
 * Issue 06 契约测试：Playwright webServer 的数据库生命周期不变量。
 *
 * 直接对真实求值后的配置断言（而非字符串匹配），保证任何回归——
 * 例如把 /health/ready 改回 /health、开启 DB 进程复用、或丢失
 * BRIDGES_RUN_ID——都会在这里失败。
 */

// 测试文件位于 apps/web/src/lib/，仓库根目录需上溯四级。
const REPO_ROOT = path.resolve(__dirname, "../../../../");

interface WebServerEntry {
  command?: string;
  url?: string;
  reuseExistingServer?: boolean;
  env?: Record<string, string>;
}

const servers = (playwrightConfig.webServer ?? []) as WebServerEntry[];
const apiServer = servers.find((s) => s.command?.includes("bridges.cli.main api"));
const mailServer = servers.find((s) => s.command?.includes("e2e_mail_server.py"));
const workerServer = servers.find((s) => s.command?.includes("e2e_worker.py"));
const webServer = servers.find((s) => s.command?.includes("npm run dev"));

describe("Playwright webServer 数据库生命周期契约（Issue 06）", () => {
  it("API 条目等待 schema-ready 健康检查而非仅端口/基础 200", () => {
    expect(apiServer).toBeDefined();
    expect(apiServer!.url).toMatch(/\/health\/ready$/);
  });

  it("API 条目注入本 run 的唯一绝对数据库 URL 与 run ID", () => {
    const env = apiServer!.env!;
    const runId = env.BRIDGES_RUN_ID;
    expect(runId).toMatch(/^run-\d+-[a-z0-9]{6}$/);

    const databaseUrl = env.BRIDGES_DATABASE_URL;
    expect(databaseUrl).toMatch(/^sqlite:\/\/\//);
    const databasePath = databaseUrl.replace(/^sqlite:\/\/\//, "");
    expect(path.isAbsolute(databasePath)).toBe(true);
    // 规范化：URL 使用正斜杠，磁盘路径使用平台分隔符
    expect(databasePath.replace(/\//g, path.sep)).toBe(
      path.join(REPO_ROOT, ".tmp", "e2e-run", runId, "bridges.db")
    );
    // 本 run 的隔离目录在配置加载时已创建
    expect(fs.existsSync(path.dirname(databasePath.replace(/\//g, path.sep)))).toBe(true);
  });

  it("数据库相关条目（API/邮件/worker）绝不复用陈旧进程", () => {
    for (const server of [apiServer, mailServer, workerServer]) {
      expect(server).toBeDefined();
      expect(server!.reuseExistingServer).toBe(false);
    }
  });

  it("worker 与 API 使用同一个数据库 URL", () => {
    expect(workerServer!.env!.BRIDGES_DATABASE_URL).toBe(
      apiServer!.env!.BRIDGES_DATABASE_URL
    );
  });

  it("前端 dev server 是无状态代理，本地可复用、CI 强制全新", () => {
    expect(webServer).toBeDefined();
    expect(webServer!.reuseExistingServer).toBe(!Boolean(process.env.CI));
  });

  it("closeout 配置同样等待 /health/ready、使用本 run 数据库且绝不复用进程", async () => {
    // closeout 配置在未指定端口时会预检默认端口占用；注入测试端口绕过。
    process.env.BRIDGES_CLOSEOUT_PORTS = "9,8,7,6,5";
    const { default: closeoutConfig } = await import(
      "../../playwright.closeout.config"
    );
    const closeoutServers = (closeoutConfig.webServer ?? []) as WebServerEntry[];
    const api = closeoutServers.find((s) =>
      s.command?.includes("bridges.cli.main api")
    );
    expect(api).toBeDefined();
    expect(api!.url).toMatch(/\/health\/ready$/);
    expect(api!.reuseExistingServer).toBe(false);
    expect(api!.env!.BRIDGES_RUN_ID).toMatch(/^run-\d+-[a-z0-9]{6}$/);
    expect(api!.env!.BRIDGES_DATABASE_URL).toMatch(/^sqlite:\/\/\//);
    // closeout 全部条目固定不复用陈旧进程（含前端 dev server）。
    for (const server of closeoutServers) {
      expect(server.reuseExistingServer).toBe(false);
    }
  });
});
