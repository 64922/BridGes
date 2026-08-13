# Issue 02：修复 Enter 创建首轮后 skip link 误占焦点

Status: resolved

Type: task

Priority: P0

User stories: US-A11Y-01、US-CHAT-FOCUS-01、US-CHAT-FOCUS-02

## 已验证现状与根因

- 已在真实 Chromium 中稳定复现：在新聊天输入框按 Enter 提交首轮消息并导航到新会话后，左上角 Logo 被蓝色“跳转到主内容”覆盖；鼠标点击发送的同一流程通常不会出现。
- Enter 提交保留了键盘输入模态。客户端导航期间浏览器/框架把焦点落到 DOM 中靠前的 skip link；该元素因此同时命中 `:focus-visible`，视觉隐藏样式被解除，蓝色覆盖层成为用户看到的“异常 Logo”。
- `apps/web/src/components/layout/AppShell.tsx` 的 pathname effect 目前只在焦点位于 `.sc-visually-hidden` 且 **不** 匹配 `:focus-visible` 时把焦点纠正到 `#main-content`。这恰好排除了 Enter 路径，导致错误焦点被保留。
- 现有 `apps/web/e2e/issue03-atomic-first-turn.spec.ts` 只覆盖“鼠标发送后 skip link 隐藏”和“整页加载后按 Tab 可见并可跳到主内容”，没有覆盖 Enter 创建首轮后的客户端导航。因此实现和现有测试都能通过，却遗漏了用户实际触发方式。
- skip link 本身是必要的无障碍功能，问题不是它可见，而是应用导航后把焦点错误地停在它上面。修复不能禁用 `:focus-visible`、移除跳转链接或破坏页面初次加载时的键盘顺序。

### 上下文指针

- `apps/web/src/components/layout/AppShell.tsx:37-51`：pathname 变化后的焦点纠正条件。
- `apps/web/src/components/design-system/SkipLink.tsx`：skip link 的固定目标与测试标识。
- `apps/web/src/components/layout/MainContent.tsx`：稳定的 `#main-content` 焦点目标。
- `apps/web/src/styles/globals.css`：`.sc-visually-hidden:focus-visible` 覆盖层与全局焦点样式。
- `apps/web/e2e/issue03-atomic-first-turn.spec.ts:194-225`：目前仅覆盖鼠标导航和整页加载 Tab 的测试。

## What to build

1. 给应用 shell 中的真实 skip link 建立稳定的语义身份（固定 ID、元素 ref 或等价的生产契约），不要依赖 `data-testid` 或过宽的 CSS 类来决定焦点迁移。
2. pathname 变化完成后，如果当前活动元素就是 shell 的 skip link，则无条件把焦点转移到当前页面的 `#main-content`，使用 `preventScroll` 避免导航完成后页面无意义跳动。
3. 对目标尚未挂载的时序建立有界、确定的处理。焦点纠正应在当前路由主区出现后执行一次，不使用无限重试、任意长 timeout 或全局 focus 监听器。
4. 保留真正的 skip-link 用户旅程：整页加载/刷新不主动移走焦点；用户从浏览器地址栏或页面起点按第一次 Tab 时，skip link 可见、可聚焦；按 Enter 后焦点落到主内容。
5. 为 Enter、发送按钮点击、普通站内导航、浏览器后退/前进和整页重载明确焦点契约。只有“路由变化后错误停在 skip link”需要纠正，用户主动 Tab 到 skip link 时不被抢焦点。
6. 添加真实浏览器回归测试，用键盘 Enter 提交首轮消息，等待新会话页面稳定后同时检查活动元素、skip link 可见性和主内容可见性。

## 非目标

- 不删除、永久隐藏或设置 `tabIndex=-1` 禁用 skip link。
- 不移除全局 `:focus-visible` 轮廓，也不通过改颜色把焦点错误伪装掉。
- 不重构聊天创建、原子首轮提交、侧栏或路由架构。
- 不为所有路由强制把焦点移到主内容；模态框、菜单、搜索快捷键等已有焦点归还契约不在本 issue 中重写。
- 不以 Playwright 中手工聚焦主内容代替生产修复。

## Acceptance criteria

- [ ] 在新聊天输入框按 Enter 发送首轮并跳转后，活动元素最终为当前页面的 `#main-content`，且 skip link 保持视觉隐藏，不再覆盖 Logo。
- [ ] 同一路径连续执行至少 20 次没有偶发停留在 skip link、body 或已卸载输入框上。
- [ ] 鼠标点击发送后的焦点契约保持稳定，skip link 不显示且页面可继续键盘操作。
- [ ] 地址栏直达或整页刷新后，第一次 Tab 仍聚焦并显示“跳转到主内容”；按 Enter 后焦点落到 `#main-content`。
- [ ] 用户在已加载页面主动 Shift+Tab/Tab 到 skip link 时，不会因为当前 effect 或全局监听立即被抢走焦点。
- [ ] 焦点迁移使用真实语义目标（固定 ID/ref），生产逻辑不读取 `data-testid`，并使用 `preventScroll` 或等价方式避免不必要滚动。
- [ ] 目标主区短暂未挂载时不会抛异常、不会无限轮询，也不会聚焦旧路由中的主区。
- [ ] 浏览器后退/前进、普通侧栏导航、账户切换以及 Ctrl/Cmd+K 搜索焦点归还的既有测试继续通过。
- [ ] 仅键盘和屏幕阅读器相关样式/可访问名称保持不变，axe 回归无新增严重或关键违规。

## Test plan

1. 扩展 `apps/web/e2e/issue03-atomic-first-turn.spec.ts`：用键盘 Enter 触发首轮创建，断言 URL、消息可见、activeElement ID、skip link 的计算样式和滚动位置。
2. 将鼠标点击和 Enter 路径做成独立测试，避免一个路径的手工焦点操作污染另一个路径。
3. 保留并强化现有“整页加载 → Tab → skip link 可见 → Enter → 主内容聚焦”测试，以证明无障碍能力未被修复误伤。
4. 增加主动键盘返回 skip link 的测试：路由稳定后通过键盘顺序聚焦它，短暂等待并确认焦点仍由用户控制。
5. 运行 shell、侧栏、搜索快捷键、账户切换和 a11y 相关 Playwright 回归；在 Chromium 至少执行一次重复测试检测时序抖动。

建议回归命令：

```powershell
cd apps/web
npx playwright test e2e/issue03-atomic-first-turn.spec.ts --project=chromium --repeat-each=3
npx playwright test e2e/t002-shell.spec.ts e2e/issue12-shell-sidebar.spec.ts e2e/issue38-a11y.spec.ts --project=chromium
```

## Observability & rollback

- 该修复原则上不新增生产业务日志；如需调试指标，仅记录路由标识、迁移原因和焦点目标类型，不记录消息正文、账户信息或 DOM 内容。
- E2E 失败时应输出 activeElement 的 tag/id、pathname、skip link 计算样式和主区是否存在，便于区分“目标未挂载”与“错误条件未命中”。
- 若上线后发现焦点被错误抢夺，优先回滚焦点 effect 的新条件并保留新增测试；不能通过永久隐藏 skip link 或移除键盘焦点样式止损。
- 若部分页面没有统一主区，应让该页面补齐现有 `#main-content` 契约或显式跳过；不得静默聚焦任意第一个 `<main>`。

## Blocked by

无。

## Comments

- 2026-08-13：根因已通过真实 Chromium 的 Enter 与鼠标对照验证；Enter 保持键盘 modality，因此原有 `!active.matches(":focus-visible")` 条件无法纠正错误焦点。
- 2026-08-13：本 issue 的双重成功条件是“Enter 路径不再显示蓝色覆盖层”与“真正的 skip-link 键盘能力仍然完整”。

## Answer

- `SkipLink` 增加固定语义 ID，`MainContent`、新聊天首页和会话页共用固定主区 ID；生产焦点逻辑只读取这些语义 ID，不读取 `data-testid` 或通用隐藏类。
- `AppShell` 在 pathname effect 中仅处理当前活动元素为 shell skip link 的场景，下一帧做一次有界检查；目标仍是当前活动 skip link 时，以 `preventScroll` 聚焦当前主区，目标未挂载或用户已移动焦点时直接结束。
- 新增真实 Chromium 的 Enter 首轮回归、主区可见性断言和连续 20 次键盘 Enter 稳定性回归。
- 验证通过：目标 Enter 用例重复 3 次、连续 20 次稳定性用例、Web 单元测试 39 项、TypeScript、ESLint 和 Next production build。shell/侧栏/a11y 批量回归中 25 项通过；其余 3 项单独重跑仍为既有模块标题等待和画像页重复 main 地标问题，与本次改动无关。全量 pytest 在收集阶段被仓库既有重复测试模块名阻断。
