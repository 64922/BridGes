import { defineConfig } from "@playwright/test";

import baseConfig from "./playwright.config";

/**
 * 生产构建验收配置（.scratch/1 工单 02：生产构建路径与开发构建使用同一条
 * 可访问业务路径）。
 *
 * 与主配置的唯一差异：web 入口跑 `next build` 的产物（`next start`，
 * NODE_ENV=production），因此 `/templates/*` 会像线上一样被 middleware
 * 重定向到 `/`——任何指向模板页的账户入口都会在这里暴露。API、后台执行器、
 * 假邮件服务与每 run 独立数据目录仍沿用主配置。
 *
 * 用法（先构建，且构建时的 API_BASE_URL 必须等于本次 API_PORT，否则前端的
 * `/api/*` 改写会指向另一个端口）：
 *
 *   API_BASE_URL=http://127.0.0.1:8112 npm run build
 *   PORT=3212 API_PORT=8112 npx playwright test -c playwright.prod.config.ts
 */

interface WebServerEntry {
  command: string;
  reuseExistingServer?: boolean;
  timeout?: number;
}

const DEV_WEB_COMMAND = "npm run dev";
const WEB_PORT = process.env.PORT || "3000";

const webServer = (baseConfig.webServer as WebServerEntry[]).map((server) =>
  server.command === DEV_WEB_COMMAND
    ? {
        ...server,
        // 生产产物：必须先构建；绝不复用已在跑的 dev server（否则本轮就
        // 不是在生产构建上验收，且问题会静默隐藏）。
        command: `npm run start -- -p ${WEB_PORT}`,
        reuseExistingServer: false,
      }
    : server
);

export default defineConfig({ ...baseConfig, webServer });
