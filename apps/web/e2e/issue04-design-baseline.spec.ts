import { expect, test, type Page } from "@playwright/test";

const STATE_KINDS = ["loading", "empty", "error", "permission", "success", "recovery"] as const;

/** 通过 URL 参数落在指定模板状态（页面上不提供状态切换按钮） */
async function gotoState(page: Page, path: string, kind: (typeof STATE_KINDS)[number]) {
  const separator = path.includes("?") ? "&" : "?";
  await page.goto(`${path}${separator}state=${kind}`);
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
    await page.goto("/templates/chat?conversation=c1&state=normal");
    await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
    await expect(page.getByRole("list", { name: "对话消息" })).toBeVisible();
    await expect(page.getByText("已思考（用时 3 秒）")).toBeVisible();
    const toolbar = page.getByRole("toolbar", { name: "消息操作" }).first();
    for (const label of ["复制", "重试", "回答有帮助", "回答需改进", "朗读"]) {
      await expect(toolbar.getByRole("button", { name: label })).toBeVisible();
    }
    await expect(page.getByTestId("composer")).toBeVisible();
    await expect(page.getByRole("button", { name: "更多功能" })).toBeVisible();
    await expect(page.getByRole("button", { name: "开始听写" })).toBeVisible();
    // 消息级错误带文字说明，不只靠颜色
    await expect(page.getByRole("alert").first()).toContainText("回答生成失败");
    // 页面上不提供状态切换按钮，状态由系统行为自动转换
    await expect(page.getByTestId("state-switcher")).toHaveCount(0);
  });

  test("打开已有对话时先加载再自动进入正常内容", async ({ page }) => {
    await page.goto("/templates/chat?conversation=c1", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("state-loading")).toBeVisible();
    // 加载完成后系统自动进入正常内容，无需用户点击
    await expect(page.getByRole("list", { name: "对话消息" })).toBeVisible();
    await expect(page.getByTestId("composer")).toBeVisible();
  });

  test("顶部提供日常陪伴/学习模式切换，按对话持久化", async ({ page }) => {
    // 新聊天默认日常陪伴
    await page.goto("/templates/chat");
    const toggle = page.getByTestId("mode-toggle");
    await expect(toggle).toBeVisible();
    await expect(toggle.getByRole("button", { name: "日常陪伴" })).toHaveAttribute("aria-pressed", "true");

    // 打开模式为「学习」的演示对话，切换器跟随对话模式
    await page.goto("/templates/chat?conversation=c1&state=normal");
    await expect(toggle.getByRole("button", { name: "学习模式" })).toHaveAttribute("aria-pressed", "true");

    // 切换为日常陪伴并持久化：刷新后保持
    await toggle.getByRole("button", { name: "日常陪伴" }).click();
    await expect(toggle.getByRole("button", { name: "日常陪伴" })).toHaveAttribute("aria-pressed", "true");
    await page.reload();
    await expect(toggle.getByRole("button", { name: "日常陪伴" })).toHaveAttribute("aria-pressed", "true");
  });

  test("空白新对话：学习名言在输入区上方，输入区居中，下方有功能推荐", async ({ page }) => {
    await page.goto("/templates/chat");
    const empty = page.getByTestId("state-empty");
    await expect(empty).toBeVisible();
    // 关于学习的名人名言，位置在对话框上方
    const quote = page.getByTestId("empty-quote");
    await expect(quote).toBeVisible();
    await expect(quote).toContainText("——");
    const quoteBox = await quote.boundingBox();
    const composerBox = await page.getByTestId("composer").boundingBox();
    expect(quoteBox!.y + quoteBox!.height).toBeLessThanOrEqual(composerBox!.y);
    // 空对话时输入区纵向大致居中（与参考网页一致）
    const viewport = page.viewportSize()!;
    const composerCenter = composerBox!.y + composerBox!.height / 2;
    expect(Math.abs(composerCenter - viewport.height / 2)).toBeLessThan(viewport.height * 0.2);
    // 对话框下方的功能推荐
    const suggestions = page.getByRole("list", { name: "功能推荐" });
    for (const label of ["论文搜索", "文章人味化", "生涯规划助手"]) {
      await expect(suggestions.getByRole("button", { name: label })).toBeVisible();
    }
    // 页面中央不再直接摆放功能卡片
    await expect(page.getByRole("list", { name: "建议入口" })).toHaveCount(0);
    // 点击功能推荐即发起对应任务
    await suggestions.getByRole("button", { name: "论文搜索" }).click();
    await expect(page.getByText("正在生成回答…")).toBeVisible();
  });

  test("输入区「+」菜单提供上传与三项功能，听写位于发送左边", async ({ page }) => {
    await page.goto("/templates/chat");
    const composer = page.getByTestId("composer");
    const plusButton = composer.getByRole("button", { name: "更多功能" });
    const dictationButton = composer.getByRole("button", { name: "开始听写" });
    const sendButton = composer.getByRole("button", { name: "发送消息" });
    await expect(plusButton).toBeVisible();
    await expect(dictationButton).toBeVisible();
    await expect(sendButton).toBeVisible();

    // 位置：「+」在输入区左边，听写在发送按钮左边且相邻
    const plusBox = await plusButton.boundingBox();
    const dictationBox = await dictationButton.boundingBox();
    const sendBox = await sendButton.boundingBox();
    expect(plusBox!.x).toBeLessThan(dictationBox!.x);
    expect(dictationBox!.x + dictationBox!.width).toBeLessThanOrEqual(sendBox!.x + 1);

    // 「+」弹窗提供四项功能
    await plusButton.click();
    const menu = page.getByRole("menu", { name: "更多功能" });
    await expect(menu).toBeVisible();
    for (const label of ["上传文件/图片", "论文搜索", "文章人味化", "生涯规划助手"]) {
      await expect(menu.getByRole("menuitem", { name: label })).toBeVisible();
    }
    // 选择功能后填入输入区并聚焦
    await menu.getByRole("menuitem", { name: "论文搜索" }).click();
    await expect(page.getByLabel("输入消息")).toHaveValue(/^论文搜索：/);
    await expect(page.getByLabel("输入消息")).toBeFocused();
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
    // 「+」菜单的「上传文件/图片」打开真实文件选择器
    const fileChooserPromise = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: "更多功能" }).click();
    await page.getByRole("menuitem", { name: "上传文件/图片" }).click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles({
      name: "2026-春季学期-量子信息课程综述-导师批注修订最终版.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("template-file"),
    });
    await expect(page.getByRole("list", { name: "待发送附件" })).toContainText("量子信息课程综述");

    await gotoState(page, "/templates/chat", "permission");
    await expect(page.getByTestId("composer")).not.toBeVisible();
    await expect(page.getByTestId("composer-unavailable")).toContainText("登录后");

    await gotoState(page, "/templates/chat", "error");
    await expect(page.getByTestId("composer-unavailable")).toContainText("先重试恢复对话");

    // 错误 → 重试 → 恢复 → 继续对话：状态由用户操作自然转换
    await page.getByRole("button", { name: "重试" }).click();
    await expect(page.getByTestId("state-recovery")).toBeVisible();
    await page.getByRole("button", { name: "继续对话" }).click();
    await expect(page.getByTestId("composer")).toBeVisible();
  });

  test("聊天与列表、详情、设置模板均具备完整页面状态", async ({ page }) => {
    for (const path of [
      "/templates/chat?conversation=c1",
      "/templates/list?section=knowledge",
      "/templates/detail?section=knowledge&id=doc1",
      "/templates/settings?section=profile",
    ]) {
      for (const kind of STATE_KINDS) {
        await gotoState(page, path, kind);
        await expect(page.getByTestId(`state-${kind}`)).toBeVisible();
      }
    }
  });

  test("登录与注册模板：真实校验、提交中、错误与会话失效状态", async ({ page }) => {
    await page.goto("/templates/login");
    // 真实校验：空表单提交出现字段级错误（登录接受用户名或 QQ 邮箱，见 ADR-0003）
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(page.getByText("请输入用户名或 QQ 邮箱。")).toBeVisible();
    await expect(page.getByText("请输入密码。")).toBeVisible();
    await expect(page.getByLabel("用户名或 QQ 邮箱")).toBeFocused();

    await gotoState(page, "/templates/login", "empty");
    await expect(page.getByText("表单尚未填写：请输入注册时使用的用户名或 QQ 邮箱。")).toBeVisible();
    await expect(page.getByText("请输入用户名或 QQ 邮箱。")).not.toBeVisible();
    await gotoState(page, "/templates/login", "error");
    await expect(page.getByTestId("error-summary")).toContainText("用户名/邮箱或密码不正确");
    await gotoState(page, "/templates/login", "permission");
    await expect(page.getByText("你的会话已过期或尚未登录，请重新登录后继续。")).toBeVisible();
    await gotoState(page, "/templates/login", "loading");
    await expect(page.getByRole("button", { name: "登录", exact: true })).toHaveAttribute("aria-busy", "true");

    await page.goto("/templates/login");
    await page.getByLabel("用户名或 QQ 邮箱").fill("123456@qq.com");
    await page.getByLabel("密码").fill("correct-horse-battery-staple");
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(page.getByTestId("state-success")).toContainText("登录表单验证通过");

    // 账户菜单入口：退出登录与切换账号落到带对应提示的登录页
    await page.goto("/templates/login?from=logout");
    await expect(page.getByText("你已退出登录。")).toBeVisible();
    await page.goto("/templates/login?from=switch");
    await expect(page.getByText(/切换账号/)).toBeVisible();

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

    await gotoState(page, "/templates/register", "recovery");
    await expect(page.getByTestId("state-recovery")).toContainText("注册表单已恢复");
  });

  test("键盘路径：账户菜单方向键遍历、Esc 关闭并归还焦点", async ({ page }) => {
    await page.goto("/templates/chat");
    const trigger = page.getByRole("button", { name: "账户菜单：示例账户" });
    await trigger.focus();
    await page.keyboard.press("Enter");
    const menu = page.getByRole("menu", { name: "账户菜单：示例账户" });
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "切换账号" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("menuitem", { name: "个人资料" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("menuitem", { name: "退出登录" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(menu).not.toBeVisible();
    await expect(trigger).toBeFocused();
  });

  test("账户菜单三项入口跳转到各自页面（GQ-06 无密钥入口）", async ({ page }) => {
    await page.goto("/templates/chat");
    const trigger = page.getByRole("button", { name: "账户菜单：示例账户" });

    await trigger.click();
    await page.getByRole("menuitem", { name: "个人资料" }).click();
    await expect(page).toHaveURL(/\/templates\/settings\?section=profile/);
    await expect(page.getByRole("heading", { name: "个人资料" })).toBeVisible();

    await trigger.click();
    await page.getByRole("menuitem", { name: "退出登录" }).click();
    await expect(page).toHaveURL(/\/templates\/login\?from=logout/);

    await page.goto("/templates/chat");
    await trigger.click();
    await page.getByRole("menuitem", { name: "切换账号" }).click();
    await expect(page).toHaveURL(/\/templates\/login\?from=switch/);
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
    await page.goto("/templates/settings?section=security");
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

  test("侧栏包含全部功能模块入口，可折叠与重新展开，Logo 链接进入新聊天", async ({ page }) => {
    await page.goto("/templates/chat");
    const sidebar = page.getByTestId("chat-sidebar");
    await expect(page.getByRole("link", { name: "BridGes — 新聊天" })).toHaveAttribute(
      "href",
      "/templates/chat",
    );
    // 功能模块入口与 1.txt 侧边栏清单一致
    const modules = [
      { label: "本地知识库", section: "knowledge" },
      { label: "学习项目", section: "projects" },
      { label: "插件", section: "plugins" },
      { label: "用户画像", section: "profile" },
    ];
    for (const module of modules) {
      await expect(sidebar.getByRole("link", { name: module.label })).toHaveAttribute(
        "href",
        `/templates/list?section=${module.section}`,
      );
    }
    // 模块入口可跳转到对应列表页并高亮当前模块
    await sidebar.getByRole("link", { name: "本地知识库" }).click();
    await expect(page.getByRole("heading", { name: "本地知识库" })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "本地知识库" })).toHaveAttribute("aria-current", "page");

    await page.goto("/templates/chat");
    const logoLink = page.getByRole("link", { name: "BridGes — 新聊天" });
    await logoLink.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/templates\/chat$/);
    await page.getByRole("button", { name: "收起侧边栏" }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    await expect(page.getByRole("button", { name: "展开侧边栏" })).toBeVisible();
    // 折叠后新聊天与功能模块入口仍可以图标形式到达
    await expect(page.getByRole("link", { name: "新聊天", exact: true })).toBeVisible();
    for (const module of modules) {
      await expect(sidebar.getByRole("link", { name: module.label })).toBeVisible();
    }
    await page.getByRole("button", { name: "展开侧边栏" }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
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
    await page.goto("/templates/settings?section=appearance");
    await page.getByRole("radio", { name: /深色/ }).check();
    const theme = await page.evaluate(() => document.documentElement.dataset.theme);
    expect(theme).toBe("dark");
    await expect(page.locator('img[src="/brand/bridges-logo-horizontal-dark.svg"]')).toBeVisible();
    await expect(page.locator('img[src="/brand/bridges-logo-horizontal.svg"]')).not.toBeVisible();
    await page.reload();
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe("dark");
  });

  test("设置模板真实持久化并可清除本机模板数据", async ({ page }) => {
    await page.goto("/templates/settings?section=profile");
    await page.getByLabel("用户名").fill("本机测试账户");
    await page.getByLabel("QQ 邮箱").fill("654321@qq.com");
    await page.getByRole("button", { name: "保存个人资料" }).click();
    await expect(page.getByTestId("state-success")).toContainText("已写入浏览器本地存储");
    await page.getByRole("button", { name: "返回设置" }).click();
    await page.reload();
    await expect(page.getByLabel("用户名")).toHaveValue("本机测试账户");
    await expect(page.getByLabel("QQ 邮箱")).toHaveValue("654321@qq.com");
    // 侧栏账户名与个人资料同步
    await expect(page.getByRole("button", { name: "账户菜单：本机测试账户" })).toBeVisible();

    await page.goto("/templates/settings?section=danger");
    await page.getByRole("button", { name: "清除本机模板数据" }).click();
    await page.getByRole("button", { name: "确认清除本机模板数据" }).click();
    await expect(page.getByText("已清除本机模板数据")).toBeVisible();
    await page.goto("/templates/settings?section=profile");
    await expect(page.getByLabel("用户名")).toHaveValue("示例账户");
    await expect(page.getByLabel("QQ 邮箱")).toHaveValue("123456@qq.com");
  });

  test("设置模板密码对话框可编辑、校验并反馈成功", async ({ page }) => {
    await page.goto("/templates/settings?section=security");
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
      await page.goto("/templates/chat?conversation=c1&state=normal");
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
    // 18 个页面快照串行采集，Next 开发编译下需要更长的测试时限
    test.setTimeout(300_000);

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
          { path: "/templates/chat?conversation=c1&state=normal", name: "chat" },
          { path: "/templates/list?section=knowledge", name: "list-knowledge" },
          { path: "/templates/detail?section=knowledge&id=doc1", name: "detail" },
          { path: "/templates/settings?section=appearance", name: "settings-dark", dark: true },
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
      await expect(page.getByTestId("state-empty")).toBeVisible();
      await expect(page).toHaveScreenshot("chat-empty-1440x900.png", { animations: "disabled" });
    });

  });
});
