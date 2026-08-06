import { expect, test, type Locator, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 38 — AC5 / AC6 / V3：可访问性结构与键盘黄金路径。
 *
 * 结构扫描（每个关键页面）：
 *   - 恰好一个 h1，标题层级不跳级；
 *   - main 地标存在且可聚焦（跳转锚点）；
 *   - 导航具有 aria-label；
 *   - 全部表单控件有可访问名称（label/aria-label/aria-labelledby）；
 *   - 无「仅图标无名称」按钮（无文字且无 aria-label 且无 title）；
 *   - 图片有 alt 或显式 aria-hidden；
 *   - 链接都有 href。
 *
 * 键盘黄金路径（注册登录 / 侧栏 / 对话框 / 聊天发送）：
 *   - 登录空表单提交：焦点移到第一个错误字段，错误以 role=alert 播报；
 *   - 侧栏 Tab 顺序（Logo → 搜索 → 收起 → 新聊天 → 模块），
 *     收起后焦点移到展开按钮、展开后归还收起按钮；
 *   - 对话框：Escape 关闭并归还触发点焦点；
 *   - 聊天发送：Enter 发送、Shift+Enter 换行。
 */

interface StructureReport {
  headings: { level: number; text: string }[];
  mainCount: number;
  navCount: number;
  navLabels: string[];
  ariaCurrentCount: number;
  unlabeledControls: string[];
  iconOnlyButtons: string[];
  imagesWithoutAlt: string[];
  emptyLinks: string[];
}

async function scanStructure(page: Page): Promise<StructureReport> {
  return page.evaluate(() => {
    const visible = (element: Element) => {
      const style = window.getComputedStyle(element);
      return style.display !== "none" && style.visibility !== "hidden";
    };
    const describe = (element: Element) => {
      const tag = element.tagName.toLowerCase();
      const id = element.getAttribute("id");
      const cls = element.getAttribute("data-testid");
      const text = (element.textContent || "").trim().slice(0, 40);
      return `<${tag}${id ? ` id="${id}"` : ""}${cls ? ` data-testid="${cls}"` : ""}> ${text}`.trim();
    };

    const headings = Array.from(document.querySelectorAll("h1, h2, h3, h4, h5, h6"))
      .filter(visible)
      .map((h) => ({ level: Number(h.tagName.slice(1)), text: (h.textContent || "").trim() }));

    const mainCount = document.querySelectorAll("main").length;

    const navs = Array.from(document.querySelectorAll("nav")).filter(visible);
    const navLabels = navs
      .map((nav) => nav.getAttribute("aria-label") || nav.getAttribute("aria-labelledby") || "")
      .filter((label) => label.length > 0);
    // 当前导航语义（AC6）：导航中的链接/按钮至多一个 aria-current（当前页）
    const ariaCurrentCount = Array.from(document.querySelectorAll('nav [aria-current]')).filter(
      visible
    ).length;

    const unlabeledControls: string[] = [];
    for (const control of Array.from(document.querySelectorAll("input, textarea, select"))) {
      if (!visible(control)) continue;
      if (control.hasAttribute("aria-hidden")) continue;
      const labeled =
        control.getAttribute("aria-label") ||
        control.getAttribute("aria-labelledby") ||
        (control.id && document.querySelector(`label[for="${control.id}"]`)) ||
        control.closest("label");
      if (!labeled) {
        unlabeledControls.push(describe(control));
      }
    }

    const iconOnlyButtons: string[] = [];
    for (const button of Array.from(document.querySelectorAll("button"))) {
      if (!visible(button)) continue;
      const text = (button.textContent || "").trim();
      const hasName =
        text ||
        button.getAttribute("aria-label") ||
        button.getAttribute("aria-labelledby") ||
        button.getAttribute("title");
      if (!hasName) {
        iconOnlyButtons.push(describe(button));
      }
    }

    const imagesWithoutAlt: string[] = [];
    for (const img of Array.from(document.querySelectorAll("img"))) {
      if (!visible(img)) continue;
      if (img.getAttribute("aria-hidden") === "true") continue;
      if (!img.hasAttribute("alt")) {
        imagesWithoutAlt.push(describe(img));
      }
    }

    const emptyLinks: string[] = [];
    for (const link of Array.from(document.querySelectorAll("a"))) {
      if (!visible(link)) continue;
      if (!link.hasAttribute("href") || link.getAttribute("href") === "") {
        emptyLinks.push(describe(link));
      }
    }

    return {
      headings,
      mainCount,
      navCount: navs.length,
      navLabels,
      ariaCurrentCount,
      unlabeledControls,
      iconOnlyButtons,
      imagesWithoutAlt,
      emptyLinks,
    };
  });
}

async function expectAccessibleStructure(page: Page, pageName: string) {
  const report = await scanStructure(page);

  const h1s = report.headings.filter((h) => h.level === 1);
  expect(h1s.length, `${pageName}：h1 数量应为 1，实际 ${h1s.length}`).toBe(1);

  // 标题层级不跳级（h1 → h2 → h3 …）
  const levels = report.headings.map((h) => h.level);
  for (let i = 1; i < levels.length; i += 1) {
    expect(
      levels[i],
      `${pageName}：标题层级跳级 ${levels[i - 1]} → ${levels[i]}`
    ).toBeLessThanOrEqual(levels[i - 1] + 1);
  }

  expect(report.mainCount, `${pageName}：应恰好一个 main 地标`).toBe(1);
  // 存在导航时每个导航都应有 aria-label（登录/注册页无导航也合规）
  expect(report.navLabels.length, `${pageName}：存在无 aria-label 的导航`).toBe(report.navCount);
  // 当前导航语义（AC6）：导航单一点——至多一个 aria-current。
  // 账户设置系列页经账户菜单进入、在主导航无对应项（既有设计），
  // 不强制其必须有当前态；但绝不允许多个当前态并存。
  if (report.navCount > 0) {
    expect(
      report.ariaCurrentCount,
      `${pageName}：导航中出现多个 aria-current（当前页），实际 ${report.ariaCurrentCount}`
    ).toBeLessThanOrEqual(1);
  }
  expect(report.unlabeledControls, `${pageName}：存在无名称的表单控件`).toEqual([]);
  expect(report.iconOnlyButtons, `${pageName}：存在仅图标无名称的按钮`).toEqual([]);
  expect(report.imagesWithoutAlt, `${pageName}：存在无 alt 的图片`).toEqual([]);
  expect(report.emptyLinks, `${pageName}：存在空链接`).toEqual([]);
}

test.describe("Issue 38 — 可访问性结构扫描", () => {
  // 内容态标志：h1 在加载态已渲染的页面（知识库/学习项目/任务/插件的
  // 壳层 h1，设置页静态 h1），必须等真实内容出现再扫描（否则扫描落在
  // 加载态，掩盖内容态标题层级问题——Issue 38 修复后复验）。
  // 知识库与学习项目页的内容区只有 StateBlock 分支，用「加载指示消失」
  // 作为加载完成标志（error/empty/content 均为合法内容态）。
  // wait: "visible" 等待标志出现；"hidden" 等待标志消失。
  const CONTENT_MARKERS: Record<string, { wait: "visible" | "hidden"; locator: (page: Page) => Locator }> = {
    "/knowledge-base": { wait: "hidden", locator: (page) => page.getByTestId("state-loading") },
    "/account/projects": { wait: "hidden", locator: (page) => page.getByTestId("state-loading") },
    "/tasks": {
      wait: "visible",
      locator: (page) => page.getByRole("heading", { name: "QQ 邮箱提醒设置" }),
    },
    "/plugins": {
      wait: "visible",
      locator: (page) => page.getByRole("button", { name: "安装插件" }).first(),
    },
    "/account/settings": {
      wait: "visible",
      locator: (page) => page.getByRole("heading", { name: "个人资料" }),
    },
  };
  for (const target of [
    { path: "/login", name: "登录页" },
    { path: "/register", name: "注册页" },
    { path: "/search", name: "统一搜索页" },
    { path: "/knowledge-base", name: "本地知识库页" },
    { path: "/tasks", name: "任务安排页" },
    { path: "/plugins", name: "插件页" },
    { path: "/account/settings", name: "账户设置页" },
    { path: "/account/profile", name: "画像中心页" },
    { path: "/account/projects", name: "学习项目页" },
    { path: "/", name: "新聊天首页" },
  ] as const) {
    test(`${target.name}（${target.path}）结构合规`, async ({ page }) => {
      if (target.path !== "/login" && target.path !== "/register") {
        const creds = uniqueCredentials(`i38a-${target.name}`);
        await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
      }
      await page.goto(target.path);
      await expect(page.getByTestId("main-content")).toBeVisible();
      // 等待页面内容态（数据加载完成），避免在加载态扫描
      const marker = CONTENT_MARKERS[target.path];
      if (marker) {
        // 数据加载可能受并行负载影响，放宽到 15s（超过默认 5s）
        if (marker.wait === "visible") {
          await expect(marker.locator(page)).toBeVisible({ timeout: 15_000 });
        } else {
          await expect(marker.locator(page)).toHaveCount(0, { timeout: 15_000 });
        }
      }
      await expect(page.locator("h1").first()).toBeVisible();
      await expectAccessibleStructure(page, target.name);
    });
  }

  test("对话页结构合规", async ({ page }) => {
    const creds = uniqueCredentials("i38a-chat");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await page.getByTestId("composer").getByRole("textbox").fill("可访问性检查对话");
    await page.getByRole("button", { name: "发送消息" }).click();
    await page.waitForURL(/\/chat\//);
    await expect(page.getByTestId("composer")).toBeVisible();
    // 对话页 h1 为视觉隐藏的会话标题（sc-visually-hidden，仍在无障碍树中）
    await expect(page.locator("h1").first()).toBeVisible();
    await expectAccessibleStructure(page, "对话页");
  });
});

test.describe("Issue 38 — 键盘黄金路径", () => {
  test("登录空表单提交：焦点移到第一个错误字段，字段级错误以 role=alert 播报并关联 aria-invalid", async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("button", { name: "登录" }).press("Enter");
    // 焦点进入第一个无效字段（用户名）
    await expect(page.getByLabel("用户名或 QQ 邮箱")).toBeFocused();
    // 字段级错误通过 role=alert 即时播报（FormField 约定），不只靠红边
    const identifierAlert = page.getByRole("alert").filter({ hasText: "请输入用户名或 QQ 邮箱。" });
    await expect(identifierAlert).toBeVisible();
    await expect(page.getByLabel("用户名或 QQ 邮箱")).toHaveAttribute("aria-invalid", "true");
  });

  test("侧栏 Tab 顺序与折叠/展开焦点归还", async ({ page }) => {
    const creds = uniqueCredentials("i38k-sidebar");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await page.getByTestId("composer").getByRole("textbox").fill("侧栏键盘测试");
    await page.getByRole("button", { name: "发送消息" }).click();
    await page.waitForURL(/\/chat\//);
    await expect(page.getByTestId("composer")).toBeVisible();

    // 验证侧栏固定 Tab 顺序（跳转链接 → Logo → 搜索 → 收起侧边栏 →
    // 新聊天 → 本地知识库 …）。SPA 导航后焦点可能已在跳转链接上，
    // 从跳转链接程序化聚焦后走序列，顺序断言与起点无关（AC5）。
    await page.evaluate(() => {
      (document.querySelector<HTMLElement>('[data-testid="skip-link"]') ?? document.body).focus();
    });
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "BridGes — 新聊天" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "搜索" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "收起侧边栏" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "新聊天", exact: true })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "本地知识库" })).toBeFocused();

    // 收起侧边栏：焦点移到「展开侧边栏」恢复按钮
    // （Shift+Tab 从本地知识库经新聊天返回收起按钮）
    await page.keyboard.press("Shift+Tab");
    await expect(page.getByRole("link", { name: "新聊天", exact: true })).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(page.getByRole("button", { name: "收起侧边栏" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "展开侧边栏" })).toBeFocused();
    // 展开：焦点归还「收起侧边栏」
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "收起侧边栏" })).toBeFocused();
  });

  test("菜单激活的对话框：Escape 关闭并归还触发点焦点", async ({ page }) => {
    const creds = uniqueCredentials("i38k-dialog");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");

    // 真实创建学习项目（不依赖对话消息），用项目操作菜单 → 改名对话框
    // 验证菜单 → 对话框的焦点归还链（Issue 38 AC5）。
    await page.goto("/account/projects");
    await page.getByTestId("learning-project-create").click();
    await page.getByRole("dialog", { name: /新建学习项目/ }).getByLabel("名称").fill("焦点回归项目");
    await page.getByRole("dialog", { name: /新建学习项目/ }).getByRole("button", { name: "创建" }).click();
    // 创建成功自动进入项目详情页（含「项目操作」菜单）
    await expect(page.getByRole("heading", { name: "焦点回归项目" })).toBeVisible();

    // 打开项目操作菜单 → 改名 → 对话框内聚焦 → Escape 关闭并归还触发点
    const projectMenu = page.getByRole("button", { name: "项目操作：焦点回归项目" });
    await projectMenu.click();
    await page.getByRole("menuitem", { name: "改名" }).click();
    const dialog = page.getByRole("dialog", { name: /修改学习项目/ });
    await expect(dialog).toBeVisible();
    // 焦点进入对话框内首个可聚焦元素
    await expect(page.getByLabel("名称")).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    // 焦点归还触发点（项目操作菜单按钮）
    await expect(projectMenu).toBeFocused();
  });

  test("菜单键盘：Enter 打开并聚焦首项、方向键移动、Escape 归还焦点", async ({ page }) => {
    const creds = uniqueCredentials("i38k-menu");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await page.goto("/account/projects");
    await page.getByTestId("learning-project-create").click();
    await page.getByRole("dialog", { name: /新建学习项目/ }).getByLabel("名称").fill("菜单键盘项目");
    await page.getByRole("dialog", { name: /新建学习项目/ }).getByRole("button", { name: "创建" }).click();
    await expect(page.getByRole("heading", { name: "菜单键盘项目" })).toBeVisible();

    // Enter 打开菜单并聚焦首项（WAI-ARIA menu 约定）
    const projectMenu = page.getByRole("button", { name: "项目操作：菜单键盘项目" });
    await projectMenu.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menu")).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "改名" })).toBeFocused();

    // ↓ 移动到删除，↑ 回到改名
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("menuitem", { name: "删除" })).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await expect(page.getByRole("menuitem", { name: "改名" })).toBeFocused();

    // Escape 关闭菜单并把焦点归还触发按钮
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(projectMenu).toBeFocused();
  });

  test("聊天输入：Enter 发送、Shift+Enter 换行", async ({ page }) => {
    const creds = uniqueCredentials("i38k-send");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    const composer = page.getByTestId("composer");
    const input = composer.getByRole("textbox");

    // Shift+Enter 换行不发送
    await input.fill("");
    await input.pressSequentially("第一行", { delay: 0 });
    await input.press("Shift+Enter");
    await input.pressSequentially("第二行", { delay: 0 });
    const value = await input.inputValue();
    expect(value).toContain("\n");

    // Enter 发送：跳转到真实对话页
    await input.press("Enter");
    await page.waitForURL(/\/chat\//);
    await expect(page.getByTestId("composer")).toBeVisible();
  });
});
