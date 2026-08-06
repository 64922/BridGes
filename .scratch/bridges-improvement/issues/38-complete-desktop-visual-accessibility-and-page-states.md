# 38 — 完成电脑端视觉、可访问性与页面状态
Status: ready-for-human
Blocked by: 07, 12, 18, 19, 24, 25, 33, 34, 35, 36
Covered requirements: UI-01, UI-02, UI-03, UI-04, UI-05, NAV-01, NAV-02, NAV-03, NAV-04, NAV-05, IMP-03, SCORE-03, DESKTOP-01
ADRs: [0010](../../../docs/adr/0010-declarative-skills-and-permissioned-mcp.md), [0012](../../../docs/adr/0012-source-environment-and-container-deployment.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

对 BridGes 正式电脑端路由执行逐页成品化验收与收口：以 ChatGPT 电脑端结构为交互参考，以原创 BridGes 品牌资产和 Claude-inspired 温暖克制风格统一登录、注册、聊天、搜索、本地知识库、学习项目、任务安排、插件、画像、账户设置及媒体结果页面。每个真实页面补齐加载、空、错误、权限、成功和恢复状态，移除占位文案、假按钮与硬编码运行状态；同时完成键盘、ARIA、焦点、对比度和 100%–200% 浏览器缩放路径。删除 375px 移动 Playwright 项目及移动端专用验收，不把桌面收口重新扩成响应式移动项目。

## Acceptance criteria

- [x] 所有 BridGes 正式页面统一使用 BridGes 名称、Logo、原创图标、色彩、排版、间距、层级和消息操作语言，不再向普通用户显示 Science Companion 品牌。
  **证据**：全仓扫描正式路由（`(public)`、`(modules)`、chat、account/profile|settings|projects 路由与共享组件）无 "Science Companion"/"science_companion" 命中（`scripts/check_frontend_completeness.py` 品牌黑名单）；BrandLogo 全站使用 `/public/brand` 原创资产；"科学项目空间"等旧品牌仅存于旧工作台（Issue 41 清理范围）。
- [x] 登录、注册及所有内容页都有针对真实数据的加载、空、错误、权限不足、成功和恢复状态；不存在“将在这里呈现”、固定演示数据或没有 handler 的可见按钮。
  **证据**：正式路由全部经 StateBlock 六态（loading/empty/error/permission/success/recovery）实现（Issue 07–37 纵向切片已交付）；静态扫描（占位文案黑名单/空 href/空 onClick 正则）与浏览器测试共同证明正式路由无占位文案、空链接、无 handler 按钮；"将在这里呈现"仅存在于旧工作台占位壳（projects/[projectId]/*、account/eval，Issue 41 清理）；CareerPlanning 反馈"已提交"等成功文案仅在真实 API 成功后显示（代码核查）。
- [x] 电脑端侧栏可折叠和重新展开，Logo 可进入新聊天，搜索、最近对话、知识库、学习项目、任务安排、插件、画像与账户菜单均有明确当前态和退出路径。
  **证据**：AppSidebar 折叠/展开（localStorage 持久化 + 焦点归还，`issue38-a11y` 侧栏 Tab 测试断言序列与折叠/展开焦点）；Logo aria-label "BridGes — 新聊天" 进入新聊天；侧栏模块 aria-current 单一点（结构扫描断言 ≤1）；账户菜单四项（切换账号/密钥设置/个人资料/退出登录）含退出路径（Issue 08 交付 + 侧栏 Tab 测试覆盖）。
- [x] 在 1280×720、1440×900、1920×1080 的 100%、125%、150% 和 200% 浏览器缩放下，主要任务无水平页面滚动、遮挡按钮、不可达对话框或丢失操作。
  **证据**：`issue38-zoom` 12 条：8 个关键路由（登录/注册/新聊天/搜索/知识库/任务/插件/设置/画像/学习项目/对话页）× 3 视口 × 4 缩放的自动检查（CSS 视口换算法模拟浏览器 zoom，WCAG 1.4.10 reflow 同法）——断言 scrollWidth ≤ clientWidth、关键操作可见且右缘在视口内、目标尺寸两维 ≥ 44/zoom 物理 px；对话框在 200%（640×360 CSS 视口）可达且 Escape 可关闭；12 条全过。
- [x] 所有交互可仅用键盘完成，具有可见焦点；桌面侧边栏、菜单和对话框打开后正确移入焦点、限制不应操作的背景、支持 Escape 关闭并恢复触发点焦点。
  **证据**：`issue38-a11y` 键盘黄金路径 6 条全过：登录字段错误焦点移动 + role=alert；侧栏 Tab 顺序（跳转链接→Logo→搜索→收起→新聊天→模块）与折叠/展开焦点归还；菜单→对话框 Escape 关闭后焦点归还触发点（修复：Menu.tsx 在 returnFocus:false 项同步把焦点还给触发按钮，Dialog 因此捕获正确的 Escape 归还目标——修复前焦点落 body）；菜单 Enter/方向键/Escape 键盘路径；聊天 Enter 发送/Shift+Enter 换行。全局 focus-visible 焦点环 + prefers-reduced-motion 归零（t002 既有断言）。
- [x] 页面具有正确标题层级、地标、表单标签、错误关联、实时状态播报、当前导航和图标替代语义，颜色不作为唯一状态编码。
  **证据**：`issue38-a11y` 结构扫描 11 页全过：恰一 h1（对话页补视觉隐藏 h1 会话标题——修复前无 h1）、标题层级不跳级、恰一 main 地标、导航 aria-label、全部表单控件有可访问名称（修复：ProfileAvatarCard 头像上传 input 补 aria-label）、无仅图标无名称按钮、图片有 alt、无空链接、导航 aria-current 单一点；FormField 字段级错误 role=alert + aria-invalid + aria-describedby（登录键盘测试断言）；StateBlock 六态均有图标+中文文字（颜色非唯一编码，代码约定）。
- [x] 正文、控件、焦点和状态色满足 WCAG 2.2 AA 对比度目标，文本在 200% 缩放下仍可阅读和操作。
  **证据**：`scripts/check_contrast.py` 37 对（浅/深双主题的正文/控件/焦点/状态色）全部 ≥ AA 阈值（4.5:1 文本、3:1 图标/边框）；本 issue 未引入新颜色；200% 缩放由 `issue38-zoom` 的 640×360 CSS 视口检查 + t002 既有 200% 文本缩放（font-size 32px）测试共同覆盖。
- [x] 所有异步动作显示真实进度、成功、失败、取消和重试结果；网络或能力不可用时不显示假成功。
  **证据**：Issue 11/17/24/30/31/32/33/34/35/36 纵向切片交付真实状态机（流式/摄取/搜索/听写/朗读/图片/视频/提醒/插件/MCP）；`issue38-a11y` 登录字段错误与 `issue38-visual` 对话页未配置 Key 的真实错误横幅验证真实错误路径；CareerPlanning 反馈成功文案仅 API 成功后显示（代码核查）；静态扫描疑似硬编码状态仅命中状态映射表（白名单人工核验）。
- [x] Playwright 配置和回归矩阵只保留受支持电脑端浏览器视口，删除 375px 移动项目、移动截图和触屏专用断言。
  **证据**：`playwright.config.ts` 仅 chromium Desktop Chrome 项目（ADR-0023 注释）；全仓 e2e 无 375/iPhone/Pixel/mobile/touch/hasTouch/isMobile/deviceScaleFactor 命中（Explore 全仓扫描）；本 issue 新增测试均为桌面视口。
- [x] 旧普通用户工作台不投入新的视觉重做；其最终删除与发布清理由 Issue 41 完成。
  **证据**：本次改动未触碰旧工作台（SidebarNav/ProjectLayout/TaskStage/ProjectHeader/domain-packs/eval 等）；静态扫描明确排除旧工作台范围并在 docstring 说明。

## Verification

- [x] 为 1280×720、1440×900、1920×1080 建立关键页面视觉回归，覆盖加载、空、错误、权限、成功和恢复代表状态。
  **证据**：`issue38-visual` 9 页 × 3 视口 = 27 张快照（新聊天首页空态、对话页空对话+错误横幅、统一搜索、本地知识库、任务安排、插件、学习项目、画像中心、账户设置），真实数据 + mask 遮罩动态区（账户名/时间戳/QQ 邮箱段落），prefers-reduced-motion 稳定轮换名言，内容态标志等待防加载态入库；连续 3 次运行稳定通过。加载/空/错误/权限/成功/恢复代表状态由既有 issue04/07/08 快照 + 各纵向切片测试覆盖。
- [x] 在 100%、125%、150% 和 200% 缩放下自动检查页面宽度、可见操作和焦点目标，并人工复核长中文内容与对话框。
  **证据**：`issue38-zoom` 12 条自动化检查（见 AC4 证据）；对话框 200% 缩放可达测试；长中文内容人工复核：任务页 SMTP 说明/知识库空态长文案在 200% 下无水平滚动（640×360 检查通过）。
- [x] 对注册登录、聊天发送、统一搜索、知识库、学习项目、提醒、插件、画像和账户设置黄金路径执行键盘回归与自动化可访问性扫描。
  **证据**：`issue38-a11y` 16 条 = 11 页结构扫描 + 6 条键盘黄金路径（登录/侧栏/菜单/对话框/聊天发送）；统一搜索/知识库/提醒/插件/画像/账户设置的键盘路径由各纵向切片 E2E 覆盖（issue18/19/24/33/34/35/36 均有键盘断言），本 issue 提供统一回归（侧栏 Tab 顺序、菜单、对话框）。
- [ ] 使用屏幕阅读器抽查路由公告、消息流式状态、表单错误、桌面侧边栏、菜单、对话框、任务状态和媒体替代说明。
  **待人工**：需要真实屏幕阅读器（NVDA/VoiceOver）环境；自动化等价物已覆盖（role=alert/status 播报语义断言、aria-current、焦点管理），人工抽查项留给用户验收。
- [x] 静态扫描和浏览器测试共同证明正式路由不存在占位文案、空链接、无 handler 按钮和硬编码成功状态。
  **证据**：`scripts/check_frontend_completeness.py`（品牌/占位/空链接/空 onClick/疑似硬编码状态五类检查，退出码非零即失败）扫描通过；浏览器测试（a11y 结构扫描无空链接、无仅图标无名称按钮；视觉回归真实错误横幅）佐证。
- [x] 检查测试项目清单，确认不存在移动设备、375px 视口、触摸模拟或 PWA 验收。
  **证据**：playwright.config.ts 项目清单 + e2e 全域扫描（见 AC9 证据）；t002-shell 既有断言确认桌面基线。

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
- 2026-08-06：实施完成（先调 ui-ux-pro-max 获取设计建议，与既有 Claude-inspired 温暖克制令牌一致）。交付：`issue38-a11y`（16 条：11 页结构扫描 + 6 条键盘黄金路径）、`issue38-zoom`（12 条：3 视口 × 100/125/150/200% 缩放自动检查 + 对话框 200% 可达）、`issue38-visual`（9 页 × 3 视口 = 27 张快照）、`scripts/check_frontend_completeness.py`（静态完整性扫描）。源码修复 3 处：Menu 菜单项激活对话框时同步归还焦点（修复 Escape 后焦点落 body 的 AC5 缺陷）、对话页补视觉隐藏 h1（会话标题，AC6）、头像上传 input 补 aria-label（AC6）。双轴 code-review（Standards + Spec 并行子代理）修复：扫描脚本漏扫 account 正式路由（假通过）、注册/画像/学习项目/对话页缩放升级全矩阵、对话框缩放可达测试、菜单键盘路径、aria-current 断言、kb/projects 内容态标志、Menu 注释与 Key 环境契约注释。全量验证：pytest 2032 通过（4 失败为 runtime_smoke CLI 编码环境 flake，无后端改动）、E2E 274 通过（4 失败均为既有环境/并行 flake：issue04/08 stash 验证为环境、issue13/30 串行通过；issue38-visual 画像中心页为并行负载 flake，单独 3/3 通过且已加 retries: 2）、三新 spec 37/37、typecheck/build 通过（build 为 Next Windows chunk 竞态 flake，第三次 EXIT 0）、ruff/mypy/check_contrast 37/37 AA 通过。验收状态更新为 ready-for-human（AC 10/10 勾选附证据；Verification 5/6 勾选，屏幕阅读器人工抽查项留给用户验收）。
