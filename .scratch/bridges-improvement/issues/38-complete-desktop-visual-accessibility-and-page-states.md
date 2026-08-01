# 38 — 完成电脑端视觉、可访问性与页面状态
Status: ready-for-agent
Blocked by: 07, 12, 18, 19, 24, 25, 33, 34, 35, 36
Covered requirements: UI-01, UI-02, UI-03, UI-04, UI-05, NAV-01, NAV-02, NAV-03, NAV-04, NAV-05, IMP-03, SCORE-03, DESKTOP-01
ADRs: [0010](../../../docs/adr/0010-declarative-skills-and-permissioned-mcp.md), [0012](../../../docs/adr/0012-source-environment-and-container-deployment.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

对 BridGes 正式电脑端路由执行逐页成品化验收与收口：以 ChatGPT 电脑端结构为交互参考，以原创 BridGes 品牌资产和 Claude-inspired 温暖克制风格统一登录、注册、聊天、搜索、本地知识库、学习项目、任务安排、插件、画像、账户设置及媒体结果页面。每个真实页面补齐加载、空、错误、权限、成功和恢复状态，移除占位文案、假按钮与硬编码运行状态；同时完成键盘、ARIA、焦点、对比度和 100%–200% 浏览器缩放路径。删除 375px 移动 Playwright 项目及移动端专用验收，不把桌面收口重新扩成响应式移动项目。

## Acceptance criteria

- [ ] 所有 BridGes 正式页面统一使用 BridGes 名称、Logo、原创图标、色彩、排版、间距、层级和消息操作语言，不再向普通用户显示 Science Companion 品牌。
- [ ] 登录、注册及所有内容页都有针对真实数据的加载、空、错误、权限不足、成功和恢复状态；不存在“将在这里呈现”、固定演示数据或没有 handler 的可见按钮。
- [ ] 电脑端侧栏可折叠和重新展开，Logo 可进入新聊天，搜索、最近对话、知识库、学习项目、任务安排、插件、画像与账户菜单均有明确当前态和退出路径。
- [ ] 在 1280×720、1440×900、1920×1080 的 100%、125%、150% 和 200% 浏览器缩放下，主要任务无水平页面滚动、遮挡按钮、不可达对话框或丢失操作。
- [ ] 所有交互可仅用键盘完成，具有可见焦点；桌面侧边栏、菜单和对话框打开后正确移入焦点、限制不应操作的背景、支持 Escape 关闭并恢复触发点焦点。
- [ ] 页面具有正确标题层级、地标、表单标签、错误关联、实时状态播报、当前导航和图标替代语义，颜色不作为唯一状态编码。
- [ ] 正文、控件、焦点和状态色满足 WCAG 2.2 AA 对比度目标，文本在 200% 缩放下仍可阅读和操作。
- [ ] 所有异步动作显示真实进度、成功、失败、取消和重试结果；网络或能力不可用时不显示假成功。
- [ ] Playwright 配置和回归矩阵只保留受支持电脑端浏览器视口，删除 375px 移动项目、移动截图和触屏专用断言。
- [ ] 旧普通用户工作台不投入新的视觉重做；其最终删除与发布清理由 Issue 41 完成。

## Verification

- [ ] 为 1280×720、1440×900、1920×1080 建立关键页面视觉回归，覆盖加载、空、错误、权限、成功和恢复代表状态。
- [ ] 在 100%、125%、150% 和 200% 缩放下自动检查页面宽度、可见操作和焦点目标，并人工复核长中文内容与对话框。
- [ ] 对注册登录、聊天发送、统一搜索、知识库、学习项目、提醒、插件、画像和账户设置黄金路径执行键盘回归与自动化可访问性扫描。
- [ ] 使用屏幕阅读器抽查路由公告、消息流式状态、表单错误、桌面侧边栏、菜单、对话框、任务状态和媒体替代说明。
- [ ] 静态扫描和浏览器测试共同证明正式路由不存在占位文案、空链接、无 handler 按钮和硬编码成功状态。
- [ ] 检查测试项目清单，确认不存在移动设备、375px 视口、触摸模拟或 PWA 验收。

## Non-goals

- 不适配手机、平板、移动浏览器、触屏专用布局、PWA 或原生移动应用。
- 不复制 ChatGPT 或 Claude 的商标、Logo、图标、插画或其他品牌资产。
- 不为即将在 Issue 41 删除的旧普通用户工作台增加新功能或视觉装饰。
- 不以纯截图替代真实键盘、ARIA、焦点和数据状态验收。

## Blocked by

- [07 — 交付 QQ 邮箱与用户名认证页面](./07-deliver-qq-username-auth-pages.md)
- [12 — 交付 ChatGPT 式电脑端壳与侧栏](./12-deliver-chatgpt-desktop-shell-sidebar.md)
- [18 — 交付本地知识库页面](./18-deliver-local-knowledge-base-page.md)
- [19 — 交付文件夹式学习项目](./19-deliver-folder-learning-projects.md)
- [24 — 交付统一电脑端搜索](./24-deliver-unified-desktop-search.md)
- [25 — 交付画像中心与静态头像](./25-deliver-profile-center-and-static-avatar.md)
- [33 — 交付 QQ SMTP 任务提醒](./33-deliver-qq-smtp-reminders.md)
- [34 — 交付 SKILL 插件中心](./34-deliver-skill-plugin-center.md)
- [35 — 交付显式授权 MCP 插件管理](./35-deliver-permissioned-mcp-plugin-management.md)
- [36 — 集成聊天工具、学习项目与插件选择](./36-integrate-chat-tools-project-and-plugin-selectors.md)

## Comments

- 2026-08-01：按已批准的 BridGes 改进计划发布。
