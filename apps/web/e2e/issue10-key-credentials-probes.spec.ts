import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const PASSWORD = "correct-horse-12";

/**
 * Issue 10 — 账户级百炼 Key 与固定能力探测页面。
 *
 * 探测状态在浏览器网络层用可控替身注入（真实供应商探测只在显式人工冒烟
 * 环境执行），UI 全流程：录入/显示隐藏/保存 → 探测中 → 可用/不可用 →
 * 单项重试 → 替换 → 两步删除确认 → 返回。全部交互只使用键盘可达控件。
 */

interface FixtureCapability {
  capability_id: string;
  display_name: string;
  model_id: string;
  status: "not_probed" | "probing" | "available" | "unavailable";
  message: string | null;
  can_retry: boolean;
  probed_at: string | null;
}

const MATRIX: Omit<FixtureCapability, "status" | "message" | "can_retry" | "probed_at">[] = [
  { capability_id: "chat", display_name: "核心对话", model_id: "qwen3.7-plus-2026-05-26" },
  { capability_id: "embedding", display_name: "知识库向量化", model_id: "text-embedding-v4" },
  { capability_id: "asr", display_name: "语音转写", model_id: "qwen3-asr-flash-2025-09-08" },
  { capability_id: "tts", display_name: "语音朗读", model_id: "qwen3-tts-flash-2025-11-27" },
  { capability_id: "image", display_name: "图片生成与编辑", model_id: "qwen-image-2.0-pro-2026-06-22" },
  { capability_id: "video", display_name: "视频生成", model_id: "wan2.7-t2v-2026-06-12" },
];

const TEST_KEY = "sk-test-e2e-1234567890abcdef";

class ProbeFixture {
  capabilities: FixtureCapability[] = this.unconfigured();
  configured = false;
  keyTail: string | null = null;

  unconfigured(): FixtureCapability[] {
    return MATRIX.map((m) => ({
      ...m,
      status: "not_probed" as const,
      message: "尚未探测。",
      can_retry: false,
      probed_at: null,
    }));
  }

  probing(): FixtureCapability[] {
    return MATRIX.map((m) => ({
      ...m,
      status: "probing" as const,
      message: "正在探测…",
      can_retry: false,
      probed_at: null,
    }));
  }

  allAvailable(): FixtureCapability[] {
    return MATRIX.map((m) => ({
      ...m,
      status: "available" as const,
      message: "可用。",
      can_retry: false,
      probed_at: new Date().toISOString(),
    }));
  }

  setCapability(id: string, status: FixtureCapability["status"], message: string | null) {
    this.capabilities = this.capabilities.map((c) =>
      c.capability_id === id
        ? {
            ...c,
            status,
            message,
            can_retry: status === "unavailable" || status === "not_probed",
            probed_at: status === "unavailable" ? new Date().toISOString() : c.probed_at,
          }
        : c
    );
  }

  projection(): Record<string, unknown> {
    return {
      status: this.configured ? "configured" : "unconfigured",
      configured: this.configured,
      key_tail: this.keyTail,
      updated_at: this.configured ? new Date().toISOString() : null,
      capabilities: this.capabilities,
      message: this.configured ? "已保存百炼 Key。" : "尚未配置百炼密钥。",
      next_step: "固定能力矩阵逐项真实探测。",
    };
  }
}

async function installProbeRoutes(page: Page, fixture: ProbeFixture) {
  // GET：轮询与首次加载返回夹具投影。
  await page.route("**/api/auth/key-settings", async (route, request) => {
    const method = request.method();
    if (method === "PUT") {
      const body = request.postDataJSON() as { key?: string } | null;
      const submitted = typeof body?.key === "string" ? body.key : TEST_KEY;
      fixture.configured = true;
      fixture.keyTail = `…${submitted.slice(-4)}`;
      fixture.capabilities = fixture.probing();
      await route.fulfill({ json: fixture.projection() });
      return;
    }
    if (method === "DELETE") {
      fixture.configured = false;
      fixture.keyTail = null;
      fixture.capabilities = fixture.unconfigured();
      await route.fulfill({ json: fixture.projection() });
      return;
    }
    await route.fulfill({ json: fixture.projection() });
  });
  await page.route("**/api/auth/key-settings/probes/**", async (route) => {
    await route.fulfill({ json: fixture.projection() });
  });
}

async function openKeySettings(page: Page) {
  await page.goto("/account/settings/keys");
  await expect(page.getByTestId("key-status")).toBeVisible();
}

test("键盘完成：录入 → 显示/隐藏 → 保存 → 探测中 → 可用", async ({ page }) => {
  const creds = uniqueCredentials("i10-save");
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  const fixture = new ProbeFixture();
  await installProbeRoutes(page, fixture);

  await openKeySettings(page);

  // 未配置空态：真实录入入口，能力全部未探测。
  await expect(page.getByText("固定能力矩阵")).toBeVisible();
  await expect(page.getByTestId("capability-chat")).toHaveText(/未探测/);

  // 纯键盘：Tab 到输入框录入，Tab 到显隐切换并回车显示。
  await page.getByLabel("百炼 API Key").fill(TEST_KEY);
  await page.getByLabel("百炼 API Key").press("Tab");
  await page.keyboard.press("Enter"); // 显示密码
  await expect(page.getByLabel("百炼 API Key")).toHaveAttribute("type", "text");

  // Tab 到保存按钮回车保存 → 探测中 → 轮询收敛为可用。
  await page.getByRole("button", { name: "保存并逐项探测" }).press("Enter");
  await expect(page.getByTestId("key-status")).toHaveText(/已配置/);
  await expect(page.getByTestId("key-tail")).toHaveText(/…cdef/);
  await expect(page.getByTestId("capability-chat")).toHaveText(/探测中/);

  fixture.capabilities = fixture.allAvailable();
  await expect(page.getByTestId("capability-chat")).toHaveText(/可用/);
  await expect(page.getByTestId("capability-video")).toHaveText(/可用/);
  await expect(page.getByText("能力探测已完成。")).toBeVisible();
  // Key 不回显。
  await expect(page.getByText(TEST_KEY)).toHaveCount(0);
});

test("部分失败只停用对应能力，单项重试恢复", async ({ page }) => {
  const creds = uniqueCredentials("i10-partial");
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  const fixture = new ProbeFixture();
  fixture.configured = true;
  fixture.keyTail = "…cdef";
  fixture.capabilities = fixture.allAvailable();
  fixture.setCapability("asr", "unavailable", "凭据无效或没有该模型权限，请检查百炼 Key 与模型开通状态。");
  await installProbeRoutes(page, fixture);

  await openKeySettings(page);

  // 仅 asr 不可用，其他能力可用。
  await expect(page.getByTestId("capability-asr")).toHaveText(/不可用/);
  await expect(page.getByTestId("capability-chat")).toHaveText(/可用/);
  await expect(
    page.getByText("凭据无效或没有该模型权限，请检查百炼 Key 与模型开通状态。")
  ).toBeVisible();

  // 键盘聚焦 asr 重试按钮并回车。
  const retryButton = page.getByRole("button", { name: "重试语音转写探测（同一模型绑定）" });
  await retryButton.focus();
  await page.keyboard.press("Enter");
  fixture.setCapability("asr", "available", "可用。");
  await expect(page.getByTestId("capability-asr")).toHaveText(/可用/);
});

test("删除密钥需两步确认，删除后回到未配置", async ({ page }) => {
  const creds = uniqueCredentials("i10-delete");
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  const fixture = new ProbeFixture();
  fixture.configured = true;
  fixture.keyTail = "…cdef";
  fixture.capabilities = fixture.allAvailable();
  await installProbeRoutes(page, fixture);

  await openKeySettings(page);
  await expect(page.getByText("已配置（尾号 …cdef）")).toBeVisible();

  // 两步确认：模态对话框聚焦陷阱，取消不删除，确认后回到未配置。
  await page.getByRole("button", { name: "删除密钥" }).press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText("确认删除百炼 Key")).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText("已配置（尾号 …cdef）")).toBeVisible();

  await page.getByRole("button", { name: "删除密钥" }).click();
  await page.getByTestId("confirm-delete-key").press("Enter");
  await expect(page.getByTestId("key-status")).toHaveText(/尚未配置/);
  await expect(page.getByLabel("百炼 API Key")).toBeVisible();
});

test("替换密钥走同一录入表单并重新探测", async ({ page }) => {
  const creds = uniqueCredentials("i10-replace");
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  const fixture = new ProbeFixture();
  fixture.configured = true;
  fixture.keyTail = "…cdef";
  fixture.capabilities = fixture.allAvailable();
  await installProbeRoutes(page, fixture);

  await openKeySettings(page);
  await page.getByRole("button", { name: "替换密钥" }).click();
  await expect(page.getByLabel("新的百炼 API Key")).toBeVisible();

  await page.getByLabel("新的百炼 API Key").fill("sk-test-e2e-new-0000abcd");
  await page.getByRole("button", { name: "保存新 Key 并重新探测" }).press("Enter");
  await expect(page.getByTestId("key-tail")).toHaveText(/…abcd/);
  await expect(page.getByTestId("capability-chat")).toHaveText(/探测中/);

  fixture.capabilities = fixture.allAvailable();
  await expect(page.getByTestId("capability-chat")).toHaveText(/可用/);
});

test("返回设置中心入口可键盘激活", async ({ page }) => {
  const creds = uniqueCredentials("i10-back");
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  await installProbeRoutes(page, new ProbeFixture());

  await openKeySettings(page);
  const back = page.getByRole("link", { name: "返回设置中心" });
  await back.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/account\/settings$/);
});
