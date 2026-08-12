import { defineConfig } from "@playwright/test";

import closeoutConfig from "./playwright.closeout.config";

/** Issue 09：只运行三条旅程的真实 API + 浏览器收尾门。 */
export default defineConfig({
  ...closeoutConfig,
  testMatch: /release-gate\.spec\.ts/,
});
