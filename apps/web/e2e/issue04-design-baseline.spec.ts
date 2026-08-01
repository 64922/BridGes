import { expect, test, type Page } from "@playwright/test";

const STATE_KINDS = ["loading", "empty", "error", "permission", "success", "recovery"] as const;

const STATE_LABELS = {
  loading: "加载中",
  empty: "空",
  error: "错误",
  permission: "未登录",
  success: "成功",
  recovery: "恢复",
} as const;

async function switchState(page: Page, label: string) {
  await page.getByTestId("state-switcher").getByRole("button", { name: label }).click();
}

test.describe("Issue 04 — 桌面设计基线模板", () => {
  test("模板索引展示六个模板入口、Logo 资产与完整图标集", async ({ page }) => {
    await page.goto("/templates");
    await expect(page.getByRole("heading", { name: "桌面设计基线模板" })).toBeVisible();
    for (const label of ["登录模板", "注册模板", "聊天内容模板", "列表模板", "详情模板", "设置模板"]) {
      await expect(page.getByRole("link", { name: label })).toBeVisible();
    }
    for (const label of ["横向完整版", "独立图标版", "单色版", "浅色背景版", "深色背景版"]) {
      await expect(page.getByAltText(`BridGes Logo ${label}`)).toBeVisible();
    }
    // 图标集覆盖验收要求的关键图标
    await expect(page.getByRole("heading", { name: /图标集（\d+ 个）/ })).toBeVisible();
    await expect(page.getByText("readAloud", { exact: true })).toBeVisible();
    await expect(page.getByText("paperSearch", { exact: true })).toBeVisible();
  });

  test("聊天模板正常状态：消息流、思考摘要、消息操作与输入区齐备", async ({ page }) => {
    await page.goto("/templates/chat");
    await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
    await expect(page.getByRole("list", { name: "对话消息" })).toBeVisible();
    await expect(page.getByText("已思考（用时 3 秒）")).toBeVisible();
    const toolbar = page.getByRole("toolbar", { name: "消息操作" }).first();
    for (const label of ["复制", "重试", "回答有帮助", "回答需改进", "朗读"]) {
      await expect(toolbar.getByRole("button", { name: label })).toBeVisible();
    }
    await expect(page.getByTestId("composer")).toBeVisible();
    await expect(page.getByRole("button", { name: "上传文件" })).toBeVisible();
    await expect(page.getByRole("button", { name: "开始听写" })).toBeVisible();
    // 消息级错误带文字说明，不只靠颜色
    await expect(page.getByRole("alert").first()).toContainText("回答生成失败");
  });

  test("输入区支持 Shift+Enter 换行、Enter 发送、Esc 停止与重试", async ({ page }) => {
    await page.goto("/templates/chat");
    const composer = page.getByLabel("输入消息");
    await composer.fill("什么是哈密顿量？");
    await composer.press("Shift+Enter");
    await composer.type("请分两段解释。");
    await expect(composer).toHaveValue("什么是哈密顿量？\n请分两段解释。");
    await composer.press("Enter");
    await expect(page.getByText("正在生成回答…")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByText("已停止生成。")).toBeVisible();
    await page.getByRole("button", { name: "重试" }).last().click();
    await expect(page.getByText("已在本地重新生成模板回答。")).toBeVisible();
  });

  test("输入区通过真实文件控件选择附件，权限与错误状态禁止继续输入", async ({ page }) => {
    await page.goto("/templates/chat");
    await page.getByTestId("composer-file-input").setInputFiles({
      name: "2026-春季学期-量子信息课程综述-导师批注修订最终版.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("template-file"),
    });
    await expect(page.getByRole("list", { name: "待发送附件" })).toContainText("量子信息课程综述");

    await switchState(page, "未登录");
    await expect(page.getByTestId("composer")).not.toBeVisible();
    await expect(page.getByTestId("composer-unavailable")).toContainText("登录后");

    await switchState(page, "错误");
    await expect(page.getByTestId("composer-unavailable")).toContainText("先重试恢复对话");
  });

  test("聊天与列表、详情、设置模板均具备完整页面状态", async ({ page }) => {
    for (const path of ["/templates/chat", "/templates/list", "/templates/detail", "/templates/settings"]) {
      await page.goto(path);
      for (const kind of STATE_KINDS) {
        await switchState(page, STATE_LABELS[kind]);
        await expect(page.getByTestId(`state-${kind}`)).toBeVisible();
      }
      await switchState(page, "正常");
    }
  });

  test("登录与注册模板：真实校验、提交中、错误与会话失效状态", async ({ page }) => {
    await page.goto("/templates/login");
    // 真实校验：空表单提交出现字段级错误
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(page.getByText("请输入 QQ 邮箱地址。")).toBeVisible();
    await expect(page.getByText("请输入密码。")).toBeVisible();
    await expect(page.getByLabel("QQ 邮箱")).toBeFocused();
    await switchState(page, "空");
    await expect(page.getByText("表单尚未填写：请输入注册时使用的 QQ 邮箱。")).toBeVisible();
    await expect(page.getByText("请输入 QQ 邮箱地址。")).not.toBeVisible();
    await switchState(page, "错误");
    await expect(page.getByTestId("error-summary")).toContainText("邮箱或密码不正确");
    await switchState(page, "未登录");
    await expect(page.getByText("你的会话已过期或尚未登录，请重新登录后继续。")).toBeVisible();
    await switchState(page, "加载中");
    await expect(page.getByRole("button", { name: "登录", exact: true })).toHaveAttribute("aria-busy", "true");

    await switchState(page, "正常");
    await page.getByLabel("QQ 邮箱").fill("123456@qq.com");
    await page.getByLabel("密码").fill("correct-horse-battery-staple");
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(page.getByTestId("state-success")).toContainText("登录表单验证通过");

    await page.goto("/templates/register");
    await page.getByRole("button", { name: "注册", exact: true }).click();
    await expect(page.getByText("QQ 邮箱应为纯数字 QQ 号加 @qq.com。")).toBeVisible();
    await expect(page.getByText("密码至少需要 12 个字符。")).toBeVisible();

    await page.getByLabel("用户名").fill("桥桥");
    await page.getByLabel("QQ 邮箱").fill("123456@qq.com");
    await page.locator("#password").fill("correct-horse-battery-staple");
    await page.getByLabel("确认密码").fill("correct-horse-battery-staple");
    await page.getByRole("button", { name: "注册", exact: true }).click();
    await expect(page.getByTestId("state-success")).toContainText("注册表单验证通过");

    await switchState(page, "恢复");
    await expect(page.getByTestId("state-recovery")).toContainText("注册表单已恢复");
  });

  test("键盘路径：账户菜单方向键遍历、Esc 关闭并归还焦点", async ({ page }) => {
    await page.goto("/templates/chat");
    const trigger = page.getByRole("button", { name: "账户菜单：示例账户" });
    await trigger.focus();
    await page.keyboard.press("Enter");
    const menu = page.getByRole("menu", { name: "账户菜单：示例账户" });
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "个人设置" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("menuitem", { name: "设置", exact: true })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("menuitem", { name: "退出登录" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(menu).not.toBeVisible();
    await expect(trigger).toBeFocused();
  });

  test("键盘路径：注册表单可依次填写并提交", async ({ page }) => {
    await page.goto("/templates/register");
    const username = page.getByLabel("用户名");
    await username.focus();
    await page.keyboard.type("桥桥");
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("QQ 邮箱")).toBeFocused();
    await page.keyboard.type("123456@qq.com");
    await page.keyboard.press("Tab");
    await expect(page.locator("#password")).toBeFocused();
    await page.keyboard.type("correct-horse-battery-staple");
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("确认密码")).toBeFocused();
    await page.keyboard.type("correct-horse-battery-staple");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "注册", exact: true })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("state-success")).toContainText("注册表单验证通过");
  });

  test("键盘路径：设置页对话框焦点陷阱、Esc 关闭并归还焦点", async ({ page }) => {
    await page.goto("/templates/settings");
    const openButton = page.getByRole("button", { name: "验证密码表单", exact: true }).first();
    await openButton.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByTestId("dialog");
    await expect(dialog).toBeVisible();
    // 焦点进入对话框并在其中循环
    await expect(dialog.getByLabel("当前密码")).toBeFocused();
    for (let i = 0; i < 6; i += 1) {
      await page.keyboard.press("Tab");
      const inside = await page.evaluate(
        () => document.querySelector('[data-testid="dialog"]')?.contains(document.activeElement) ?? false,
      );
      expect(inside).toBe(true);
    }
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
    await expect(openButton).toBeFocused();
  });

  test("侧栏可折叠与重新展开，Logo 链接进入新聊天", async ({ page }) => {
    await page.goto("/templates/chat");
    const sidebar = page.getByTestId("chat-sidebar");
    await expect(page.getByRole("link", { name: "BridGes — 新聊天" })).toHaveAttribute(
      "href",
      "/templates/chat",
    );
    const logoLink = page.getByRole("link", { name: "BridGes — 新聊天" });
    await logoLink.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/templates\/chat$/);
    await page.getByRole("button", { name: "收起侧边栏" }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await expect(page.getByRole("button", { name: "展开侧边栏" })).toBeVisible();
    await page.getByRole("button", { name: "展开侧边栏" }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
    // 折叠状态下唯一默认入口仍可键盘到达，领域能力不进入普通用户主导航。
    await page.getByRole("button", { name: "收起侧边栏" }).click();
    await expect(page.getByRole("link", { name: "新聊天", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "本地知识库" })).toHaveCount(0);
  });

  test("列表模板搜索过滤与真实空态", async ({ page }) => {
    await page.goto("/templates/list?section=knowledge");
    await expect(page.getByRole("heading", { name: "本地知识库" })).toBeVisible();
    await page.getByLabel("搜索本地知识库").fill("不存在的条目xyz");
    await expect(page.getByTestId("state-empty")).toContainText("没有匹配");
    await page.getByRole("button", { name: "清除搜索" }).click();
    await expect(page.getByRole("link", { name: /量子纠错综述阅读清单/ })).toBeVisible();
  });

  test("列表条目菜单可固定、重命名、移除并撤销", async ({ page }) => {
    await page.goto("/templates/list?section=knowledge");
    const rowTitle = "量子纠错综述阅读清单.md";
    const firstTrigger = page.getByRole("button", { name: `条目操作：${rowTitle}` });
    await firstTrigger.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menuitem", { name: "固定到顶部" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("menuitem", { name: "重命名" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("条目名称")).toBeFocused();
    await page.getByLabel("条目名称").fill("量子纠错综述精读清单.md");
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("link", { name: /量子纠错综述精读清单/ })).toBeVisible();

    await page.getByRole("button", { name: "条目操作：量子纠错综述精读清单.md" }).click();
    await page.getByRole("menuitem", { name: "从列表移除" }).click();
    await expect(page.getByRole("link", { name: /量子纠错综述精读清单/ })).not.toBeVisible();
    await page.getByRole("button", { name: "撤销移除" }).click();
    await expect(page.getByRole("link", { name: /量子纠错综述精读清单/ })).toBeVisible();
  });

  test("设置模板主题切换真实生效", async ({ page }) => {
    await page.goto("/templates/settings");
    await page.getByRole("radio", { name: /深色/ }).check();
    const theme = await page.evaluate(() => document.documentElement.dataset.theme);
    expect(theme).toBe("dark");
    await expect(page.locator('img[src="/brand/bridges-logo-horizontal-dark.svg"]')).toBeVisible();
    await expect(page.locator('img[src="/brand/bridges-logo-horizontal.svg"]')).not.toBeVisible();
    await page.reload();
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe("dark");
  });

  test("设置模板真实持久化并可清除本机模板数据", async ({ page }) => {
    await page.goto("/templates/settings");
    await page.getByLabel("用户名").fill("本机测试账户");
    await page.getByLabel("QQ 邮箱").fill("654321@qq.com");
    await page.getByRole("button", { name: "保存个人资料" }).click();
    await expect(page.getByTestId("state-success")).toContainText("已写入浏览器本地存储");
    await page.getByRole("button", { name: "返回设置" }).click();
    await page.reload();
    await expect(page.getByLabel("用户名")).toHaveValue("本机测试账户");
    await expect(page.getByLabel("QQ 邮箱")).toHaveValue("654321@qq.com");

    await page.getByRole("button", { name: "清除本机模板数据" }).click();
    await page.getByRole("button", { name: "确认清除本机模板数据" }).click();
    await expect(page.getByText("已清除本机模板数据")).toBeVisible();
    await expect(page.getByLabel("用户名")).toHaveValue("示例账户");
    await expect(page.getByLabel("QQ 邮箱")).toHaveValue("123456@qq.com");
  });

  test("设置模板密码对话框可编辑、校验并反馈成功", async ({ page }) => {
    await page.goto("/templates/settings");
    await page.getByRole("button", { name: "验证密码表单", exact: true }).first().click();
    await page.getByRole("button", { name: "验证密码表单", exact: true }).last().click();
    await expect(page.getByText("请输入当前密码。")).toBeVisible();
    await expect(page.getByLabel("当前密码")).toBeFocused();

    await page.getByLabel("当前密码").fill("old-password");
    await page.getByLabel("新密码").fill("new-password-with-12-characters");
    await page.getByRole("button", { name: "验证密码表单", exact: true }).last().click();
    await expect(page.getByText("密码表单校验通过；未调用账户服务。")).toBeVisible();
  });

  test("详情模板执行真实的内容完整性检查", async ({ page }) => {
    await page.goto("/templates/detail");
    await page.getByRole("button", { name: "检查内容完整性" }).click();
    await expect(page.getByText("已检查 3 个内容区块，均可读取。")).toBeVisible();
  });

  test("桌面视口下长中文、代码、公式、表格与长文件名不产生横向溢出", async ({ page }) => {
    for (const viewport of [
      { width: 1280, height: 720 },
      { width: 1440, height: 900 },
      { width: 1920, height: 1080 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/templates/chat");
      await page.waitForLoadState("networkidle");
      const { scrollWidth, innerWidth } = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
      }));
      expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
      // 主要操作不被遮挡：发送按钮与消息操作行可见
      await expect(page.getByRole("button", { name: "发送消息" })).toBeInViewport();
      await page.getByRole("toolbar", { name: "消息操作" }).first().scrollIntoViewIfNeeded();
      await expect(page.getByRole("toolbar", { name: "消息操作" }).first()).toBeInViewport();
    }
  });

  test.describe("视觉回归（桌面视口）", () => {
    test("六类模板覆盖三个约定桌面视口", async ({ page }) => {
      for (const viewport of [
        { width: 1280, height: 720, name: "1280x720" },
        { width: 1440, height: 900, name: "1440x900" },
        { width: 1920, height: 1080, name: "1920x1080" },
      ]) {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        for (const target of [
          { path: "/templates/login", name: "login" },
          { path: "/templates/register", name: "register" },
          { path: "/templates/chat", name: "chat" },
          { path: "/templates/list?section=knowledge", name: "list-knowledge" },
          { path: "/templates/detail?section=knowledge&id=doc1", name: "detail" },
          { path: "/templates/settings", name: "settings-dark", dark: true },
        ]) {
          await page.goto(target.path);
          await page.waitForLoadState("networkidle");
          if (target.dark) await page.getByRole("radio", { name: /深色/ }).check();
          await expect(page).toHaveScreenshot(`${target.name}-${viewport.name}.png`, {
            animations: "disabled",
          });
          if (target.dark) {
            await page.evaluate(() => {
              window.localStorage.removeItem("bridges-template-theme");
              document.documentElement.dataset.theme = "light";
            });
          }
        }
      }
    });

    test("聊天模板空白建议态", async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto("/templates/chat");
      await switchState(page, "空");
      await expect(page).toHaveScreenshot("chat-empty-1440x900.png", { animations: "disabled" });
    });

  });
});
