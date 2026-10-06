import { expect, test, type Page, type TestInfo } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 21 桌面交互验收（AC4、AC7）：真实服务、零 page route mock。
 *
 * 与既有专项脚本的分工：issue12/13/23/25 等各自核对一个切片，大多用协议
 * mock 驱动；本脚本是「正式界面验收」的合并口径——只经真实 HTTP 链路
 * （API + 后台执行器 + 前端），核对 AC4 的六个面（模式、六模块、附件、
 * 学习阶段、画像、设置）与 AC7 的六项桌面回归（`+` 菜单顺序与说明、输入框
 * 与菜单键盘操作、消息来源行、学习阶段条、画像空态、模型验证结果），并在
 * 1280×720、1440×900、1920×1080 三个视口各跑一遍。
 *
 * 外部证据来源按运行配置取值：默认配置下 arXiv／贴吧／GitHub 走真实网络，
 * 收尾夹具配置（BRIDGES_CLOSEOUT_FIXTURES=true）下为确定性替身。因此本
 * 脚本只断言「真实派发 + 明确终态」：模块请求确实带模块标识发出、消息落库
 * 后带中文模块标签、该轮给出可核对的结果卡或如实失败，绝不把没有证据的
 * 结果当作可用（AC7 末句）。退役能力不得出现在任何入口。
 *
 * 模块选择只发生在空白会话首轮前（对话内输入区不提供模块选择），因此
 * 六模块的真实派发各建一个会话。
 */

const VIEWPORTS = [
  { width: 1280, height: 720 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
] as const;

const PASSWORD = "correct-horse-acceptance";
const PLUS_MENU_LABEL = "添加功能或文件";

// 本脚本等待真实执行器处理（解析、模块派发、模型轮次），比默认 30 秒更长；
// 六模块逐个真实派发（各自带 30/20 秒的上游期限）也要留足总预算。
test.describe.configure({ timeout: 600_000 });

/** `+` 菜单的完整顺序与说明（Composer.tsx + lib/chat-modules.ts）。 */
const PLUS_MENU = [
  {
    label: "添加照片和文件",
    description: "支持 PDF、DOCX、TXT、Markdown 与图片，单个 10 MB 内，也可拖入或粘贴",
  },
  {
    label: "论文搜索",
    description: "按主题检索 arXiv 论文，给出阅读顺序与真实链接",
  },
  {
    label: "校园通勤",
    description: "按步行／自行车／电动车查校内路线，只画高德返回的路径",
  },
  {
    label: "学习资料推荐",
    description: "按技术方向给出图书与哔哩哔哩视频清单，按由浅入深排列",
  },
  {
    label: "贴吧信息搜集",
    description: "只看华东交通大学吧的公开帖子，拿不到回复时只给帖链",
  },
  {
    label: "职业规划",
    description: "按目标岗位与城市读公开岗位样本，不足时不称市场行情",
  },
  {
    label: "GitHub 项目推荐",
    description: "按你的 idea 检索公开仓库，逐项给出功能匹配与维护许可证据",
  },
] as const;

/** 六模块 = `+` 菜单第 2..7 项（第一项是文件选择，不是模块）。 */
const MODULES = PLUS_MENU.slice(1);

/** 各模块结果卡的 testid 前缀（状态后缀由真实结果决定）。 */
const MODULE_CARD_PREFIX: Record<string, RegExp> = {
  论文搜索: /^(paper-search-card|arxiv-search-card)/,
  校园通勤: /^commute-route-card/,
  学习资料推荐: /^resources-card/,
  贴吧信息搜集: /^tieba-research-card/,
  职业规划: /^(career-plan|career-result|career-process)/,
  "GitHub 项目推荐": /^github-projects-card/,
};

/** 各模块首轮的输入（模块需要的最小主题，避免「缺主题」被当成不可用）。 */
const MODULE_PROMPT: Record<string, string> = {
  论文搜索: "Transformer 架构",
  校园通勤: "从南区宿舍到图书馆怎么走",
  学习资料推荐: "数据结构入门",
  贴吧信息搜集: "华东交通大学吧里的自习室讨论",
  职业规划: "数据开发方向在南昌的岗位",
  "GitHub 项目推荐": "找一个小型待办清单 Web 应用",
};

/** 退役能力不得出现在任何桌面入口（AC1/AC7：未通过的模块不显示为可用）。 */
const RETIRED_SIDEBAR_ENTRIES = [
  "更多功能",
  "学习实验室",
  "科学表达工坊",
  "证据与校验台",
  "多模态科学实验室",
  "领域包",
  "全局科学伙伴",
  "评测与运行中心",
];

const PNG_PIXEL = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==",
  "base64"
);

async function signUpAndLand(page: Page, prefix: string, suffix: string, testInfo: TestInfo) {
  const credentials = uniqueCredentials(prefix);
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  await expect(page.getByTestId("new-chat-home")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath(`home-${suffix}.png`) });
  return credentials;
}

/**
 * 在隐藏的附件输入上派发真实 change 事件。
 *
 * Playwright 的 setInputFiles 不触发 display:none 输入上的 React onChange
 * （收尾脚本同样结论），因此走 DataTransfer + change：链路与用户选择文件
 * 一致（真实上传/草稿，无 mock）。
 */
async function attachFile(
  page: Page,
  fileName: string,
  mimeType: string,
  content: Buffer
): Promise<void> {
  await page.getByTestId("composer-file-input").evaluate(
    (el, payload) => {
      const input = el as HTMLInputElement;
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(
        new File([Uint8Array.from(atob(payload.base64), (c) => c.charCodeAt(0))], payload.name, {
          type: payload.mimeType,
        })
      );
      Object.defineProperty(input, "files", { configurable: true, value: dataTransfer.files });
      input.dispatchEvent(new Event("change", { bubbles: true }));
    },
    { name: fileName, mimeType, base64: content.toString("base64") }
  );
}

function plusTrigger(page: Page) {
  return page.getByTestId("composer").getByRole("button", { name: PLUS_MENU_LABEL });
}

async function openPlusMenu(page: Page) {
  const trigger = plusTrigger(page);
  await trigger.click();
  const menu = page.getByRole("menu", { name: PLUS_MENU_LABEL });
  await expect(menu).toBeVisible();
  return { trigger, menu };
}

/** `+` 菜单里选中模块：可见标签（chip）是「已选」的唯一可见凭据。 */
async function selectModule(page: Page, label: string) {
  const { menu } = await openPlusMenu(page);
  await menu.getByRole("menuitem", { name: label }).click();
  const chip = page.getByTestId("composer-module-chip");
  await expect(chip).toContainText(label);
  return chip;
}

async function sendFirstTurn(page: Page) {
  const [response] = await Promise.all([
    page.waitForResponse(
      (candidate) =>
        candidate.request().method() === "POST" &&
        candidate.url().includes("/api/chat/first-turn")
    ),
    page.getByRole("button", { name: "发送消息" }).click(),
  ]);
  // 真实服务必须先接受这一轮：非 2xx 时把状态与正文带进失败信息，避免
  // 只在「页面没跳转」上打转。
  expect(
    response.ok(),
    `首轮 HTTP ${response.status()}：${(await response.text()).slice(0, 400)}`
  ).toBeTruthy();
  // 真实首轮要先建会话再开流，慢机器上跳转可能晚于请求本身。
  await expect(page).toHaveURL(/\/chat\//, { timeout: 60_000 });
  return response.request().postDataJSON();
}

async function backToNewChat(page: Page) {
  await page
    .getByTestId("app-sidebar")
    .getByRole("link", { name: "新聊天", exact: true })
    .click();
  await expect(page.getByTestId("new-chat-home")).toBeVisible();
}

function threadMessages(page: Page) {
  return page.getByTestId("chat-thread").locator('[id^="msg-"]');
}

for (const viewport of VIEWPORTS) {
  const size = `${viewport.width}×${viewport.height}`;

  test.describe(`桌面验收 ${size}`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test(`首页模式、+ 菜单顺序与说明、输入与菜单键盘（${size}）`, async ({ page }, testInfo) => {
      await signUpAndLand(page, "i21-home", size, testInfo);

      // 模式：空白会话默认日常陪伴，另一模式在首轮前可选（不锁死也不假装可用）。
      const mode = page.getByTestId("mode-toggle");
      await expect(mode.getByRole("button", { name: "日常陪伴" })).toHaveAttribute(
        "aria-pressed",
        "true"
      );
      await expect(mode.getByRole("button", { name: "学习模式" })).toBeEnabled();
      await mode.getByRole("button", { name: "学习模式" }).click();
      await expect(mode.getByRole("button", { name: "学习模式" })).toHaveAttribute(
        "aria-pressed",
        "true"
      );
      await mode.getByRole("button", { name: "日常陪伴" }).click();
      await expect(mode.getByRole("button", { name: "日常陪伴" })).toHaveAttribute(
        "aria-pressed",
        "true"
      );

      // 退役入口：侧边栏与对话区都不出现旧工作台、朗读、更多功能菜单。
      const sidebar = page.getByTestId("app-sidebar");
      for (const retired of RETIRED_SIDEBAR_ENTRIES) {
        await expect(sidebar).not.toContainText(retired);
      }
      await expect(page.getByRole("button", { name: /朗读/ })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "更多功能" })).toHaveCount(0);

      // 键盘打开 `+` 菜单：Enter 展开并把焦点放到第一项。
      const { trigger, menu } = await openPlusMenu(page);
      await page.keyboard.press("Escape");
      await expect(page.getByRole("menu", { name: PLUS_MENU_LABEL })).toHaveCount(0);
      await trigger.press("Enter");
      await expect(trigger).toHaveAttribute("aria-expanded", "true");
      await expect(
        page.getByRole("menu", { name: PLUS_MENU_LABEL }).getByRole("menuitem").first()
      ).toBeFocused();

      // 完整顺序与说明：逐项按位置核对标签与第二行说明。
      const items = page.getByRole("menu", { name: PLUS_MENU_LABEL }).getByRole("menuitem");
      await expect(items).toHaveCount(PLUS_MENU.length);
      for (let index = 0; index < PLUS_MENU.length; index += 1) {
        const item = PLUS_MENU[index];
        await expect(items.nth(index)).toContainText(item.label);
        await expect(items.nth(index)).toContainText(item.description);
      }

      // 键盘遍历：End 到末项、Home 回首项、Escape 关闭并把焦点还给触发按钮。
      await page.keyboard.press("End");
      await expect(items.last()).toBeFocused();
      await page.keyboard.press("Home");
      await expect(items.first()).toBeFocused();
      await page.keyboard.press("Escape");
      await expect(page.getByRole("menu", { name: PLUS_MENU_LABEL })).toHaveCount(0);
      await expect(trigger).toBeFocused();

      // 六模块：每个都能选中成可见标签，也能按标签移除（模块标识不隐藏）。
      for (const module of MODULES) {
        const chip = await selectModule(page, module.label);
        await chip.getByRole("button", { name: `移除${module.label}模块` }).click();
        await expect(page.getByTestId("composer-module-chip")).toHaveCount(0);
      }

      // 输入框键盘：Shift+Enter 换行、Enter 发送（真实首轮，无 mock）。
      const input = page.getByLabel("输入消息");
      await input.focus();
      await page.keyboard.type("第一行");
      await page.keyboard.press("Shift+Enter");
      await page.keyboard.type("第二行");
      await expect(input).toHaveValue("第一行\n第二行");

      // 选中模块后发送：真实请求带模块标识，消息落库带中文模块标签。
      await selectModule(page, "论文搜索");
      await input.fill(MODULE_PROMPT["论文搜索"]);
      const firstTurn = await sendFirstTurn(page);
      expect(firstTurn).toMatchObject({ mode: "companion", module_id: "paper" });

      // 消息来源行（AC7）：用户消息带模块标签；本轮没有真实引用就不是引用卡。
      await expect(page.getByTestId("user-module-label").first()).toHaveText("论文搜索");
      await expect(page.getByTestId("retrieval-citations-card")).toHaveCount(0);
      await expect(page.getByText("本地检索")).toHaveCount(0);

      // 助手消息操作行真实可用（复制/有帮助/需改进）。
      const toolbar = page.getByRole("toolbar", { name: "消息操作" }).first();
      await expect(toolbar.getByRole("button", { name: "复制" })).toBeVisible();
      await expect(toolbar.getByRole("button", { name: "回答有帮助" })).toBeVisible();
      await expect(toolbar.getByRole("button", { name: "回答需改进" })).toBeVisible();

      // 首轮后模式锁定：只展示当前模式，不再提供按钮。
      await expect(page.getByTestId("conversation-mode")).toContainText("日常陪伴");
      await expect(page.getByTestId("mode-toggle")).toHaveCount(0);

      await page.screenshot({ path: testInfo.outputPath(`chat-${size}.png`) });
    });

    test(`六模块连接真实服务（${size}）`, async ({ page }, testInfo) => {
      await signUpAndLand(page, "i21-modules", size, testInfo);

      for (const module of MODULES) {
        await selectModule(page, module.label);
        await page.getByLabel("输入消息").fill(MODULE_PROMPT[module.label]);
        const firstTurn = await sendFirstTurn(page);
        expect(firstTurn.module_id).toBeDefined();
        expect(firstTurn.mode).toBe("companion");

        // 模块标识落库并回显：用户消息带该模块的中文标签（历史可读）。
        await expect(page.getByTestId("user-module-label").first()).toHaveText(module.label, {
          timeout: 60_000,
        });
        // 该轮给出可核对的结果卡或如实失败；不出现「无结果也不报错」。
        // 上游证据源不可达时，模块自带的有界期限（检索 30 秒／读取 20 秒）
        // 会把该轮收敛成如实降级卡，因此这里等的是「终态」而不是「成功」。
        await expect(page.getByTestId(MODULE_CARD_PREFIX[module.label]).first()).toBeVisible({
          timeout: 180_000,
        });
        await expect(threadMessages(page)).toHaveCount(2, { timeout: 60_000 });
        await expect(page.getByText("正在生成回答")).toHaveCount(0, { timeout: 60_000 });
        await page.screenshot({
          path: testInfo.outputPath(`module-${module.label}-${size}.png`),
        });
        await backToNewChat(page);
      }
    });

    test(`附件与学习阶段连接真实服务（${size}）`, async ({ page }, testInfo) => {
      await signUpAndLand(page, "i21-attach", size, testInfo);

      // 附件：真实上传草稿 → 真实首轮 → 真实解析状态（本地解析器，无 mock）。
      await attachFile(
        page,
        "验收笔记.txt",
        "text/plain",
        Buffer.from("桌面验收附件正文：用于核对真实上传、解析与可检索状态。", "utf-8")
      );
      await expect(page.getByTestId("composer-attachments")).toContainText("验收笔记.txt");
      await expect(page.getByTestId("composer-attachments")).toContainText("第 1 个");
      await page.getByLabel("输入消息").fill("带附件的桌面验收消息");
      await sendFirstTurn(page);
      await expect(page.getByTestId("chat-thread")).toBeVisible();

      // 解析终态：排队/解析中必须收敛，纯文本不得解析失败。
      await expect(page.getByTestId(/^ingestion-status-(queued|processing)$/)).toHaveCount(0, {
        timeout: 90_000,
      });
      await expect(
        page.getByTestId(/^ingestion-status-(ready|none|permission)$/).first()
      ).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId("ingestion-status-error")).toHaveCount(0);

      // 学习阶段：真实学习模式首轮（书页照片）→ 学习阶段条五个阶段与当前阶段。
      await backToNewChat(page);
      await page.getByTestId("mode-toggle").getByRole("button", { name: "学习模式" }).click();
      await expect(page.getByText("上传本节书页照片开始预习")).toBeVisible();
      await attachFile(page, "书页-1.png", "image/png", PNG_PIXEL);
      await expect(page.getByTestId("composer-attachments")).toContainText("书页-1.png");
      await expect(page.getByTestId("composer-attachments")).toContainText("第 1 张");
      await page.getByLabel("输入消息").fill("本节书页，请开始预习");
      const studyTurn = await sendFirstTurn(page);
      expect(studyTurn.mode).toBe("study");

      const progress = page.getByRole("region", { name: "学习阶段" });
      await expect(progress).toBeVisible({ timeout: 60_000 });
      for (const stage of ["书页识别", "辅助预习", "辅导", "复盘", "总结"]) {
        await expect(progress.getByText(stage, { exact: true })).toBeVisible();
      }
      // 当前阶段是书页识别：识别没完成就不冒充进入辅导/复盘。
      await expect(progress.getByText("书页识别", { exact: true })).toHaveAttribute(
        "aria-current",
        "step"
      );
      for (const later of ["辅助预习", "辅导", "复盘", "总结"]) {
        await expect(progress.getByText(later, { exact: true })).not.toHaveAttribute(
          "aria-current",
          "step"
        );
      }

      await page.screenshot({ path: testInfo.outputPath(`study-${size}.png`) });
    });

    test(`画像空态、设置与模型验证结果（${size}）`, async ({ page }, testInfo) => {
      await signUpAndLand(page, "i21-profile", size, testInfo);

      // 画像空态：新账户如实显示「还没有长期信息」，并说明提取边界。
      await page.goto("/account/profile");
      await expect(page.getByRole("heading", { name: "你的信息" })).toBeVisible();
      const empty = page.getByTestId("atomic-empty");
      await expect(empty).toContainText("这里还没有长期信息");
      await expect(empty).toContainText("不会推断人格或心理状态");
      await expect(page.getByTestId("atomic-item")).toHaveCount(0);
      await page.screenshot({ path: testInfo.outputPath(`profile-${size}.png`) });

      // 设置：入口与分区（个人资料 / 数据与隐私）。
      await page.goto("/account/settings");
      await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "个人资料" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "数据与隐私" })).toBeVisible();
      await expect(page.getByRole("link", { name: "打开个人资料" })).toHaveAttribute(
        "href",
        "/account/settings/profile"
      );
      await expect(page.getByRole("link", { name: "打开密钥与模型管理" })).toHaveAttribute(
        "href",
        "/account/settings/models"
      );

      // 模型验证结果：四类凭据各自给出明确状态，主模型给出配置来源。
      await page.goto("/account/settings/models");
      await expect(page.getByRole("heading", { name: "密钥与模型管理" })).toBeVisible();
      const credentialStatus = page.getByTestId("credential-status");
      await expect(credentialStatus).toHaveCount(4);
      for (let index = 0; index < 4; index += 1) {
        // 只接受两种明确状态：既不隐瞒已配置，也不把未配置说成可用。
        await expect(credentialStatus.nth(index)).toHaveText(/^(已配置|未配置)$/);
      }
      await expect(page.getByRole("heading", { name: "Qwen 主模型" })).toBeVisible();
      await expect(page.getByTestId("model-source")).toHaveText(/^(出厂默认|已验证配置)$/);
      await expect(page.getByTestId("active-model-id")).not.toBeEmpty();

      // 真实发起一次注定失败的验证：界面必须给出明确的「未通过」结论与原因，
      // 绝不把没通过的模型显示为可用（AC7 末句）。
      const mainModel = page.getByRole("region", { name: "Qwen 主模型" });
      await page.getByLabel("Qwen 主模型 ID").fill("qwen-acceptance-probe-does-not-exist");
      // 隔离环境无凭据时先验证配置指引；未发起探测不能伪造验证报告。
      const keyInput = page.getByLabel("Qwen API Key（可选：与主模型一起更换）");
      if ((await mainModel.getByText("当前没有可用的 Qwen 密钥", { exact: false }).count()) > 0) {
        const previousReport = await page.getByTestId("model-validation-report").allTextContents();
        await mainModel.getByRole("button", { name: "验证并保存" }).click();
        await expect(mainModel.getByRole("alert")).toContainText("当前没有可用的 Qwen 密钥");
        // 最近报告是全局模型配置记录，可能来自前一个视口；缺凭据不得更新它。
        expect(await page.getByTestId("model-validation-report").allTextContents()).toEqual(previousReport);
      }
      // 故意无效的非秘密测试值走真实元数据请求，失败不得改变当前模型或保存密钥。
      await keyInput.fill("issue43-invalid-credential-for-acceptance");
      await mainModel.getByRole("button", { name: "验证并保存" }).click();
      const report = page.getByTestId("model-validation-report");
      await expect(report).toBeVisible({ timeout: 90_000 });
      await expect(report).toContainText("最近一次验证：未通过");
      await expect(report).toContainText("qwen-acceptance-probe-does-not-exist");
      await expect(page.getByTestId("model-source")).toHaveText(/^(出厂默认|已验证配置)$/);

      await page.screenshot({ path: testInfo.outputPath(`settings-${size}.png`) });
    });
  });
}
