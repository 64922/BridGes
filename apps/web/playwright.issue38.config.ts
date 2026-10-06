import { defineConfig } from "@playwright/test";
import * as path from "path";
import base from "./playwright.config";

process.env.ISSUE38_DOMAIN_FIXTURES = "1";
process.env.PYTHONPATH = [
  path.resolve(__dirname, "../.."),
  path.resolve(__dirname, "../../src"),
].join(path.delimiter);

const webServers = Array.isArray(base.webServer) ? base.webServer : [base.webServer!];
const python = process.env.BRIDGES_PYTHON;
if (!python) throw new Error("工单 38 浏览器验收须设置 agent 环境的 BRIDGES_PYTHON。");

export default defineConfig({
  ...base,
  testMatch: "issue38-domain-paths.spec.ts",
  workers: 1,
  webServer: webServers.map((server, index) =>
    index === 0
      ? {
          ...server,
          command: `"${python}" -m tests.chat.issue38_browser_api`,
          env: {
            ...server.env,
            PYTHONPATH: [
              path.resolve(__dirname, "../.."),
              path.resolve(__dirname, "../../src"),
            ].join(path.delimiter),
            API_PORT: process.env.API_PORT || "8038",
          },
        }
      : server
  ),
});
