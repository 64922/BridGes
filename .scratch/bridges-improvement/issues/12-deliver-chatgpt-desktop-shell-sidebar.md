# 12 — 按 ChatGPT 电脑端模板交付固定顺序侧栏

Status: ready-for-human
Blocked by: [04 — 建立 ChatGPT 桌面交互基线与 BridGes 原创视觉资产](./04-establish-desktop-design-baseline-and-brand-assets.md), [08 — 交付账户上拉菜单与个人资料设置](./08-deliver-account-menu-and-personal-settings.md), [11 — 交付持久化真实 Qwen 流式聊天纵向切片](./11-deliver-persisted-streaming-chat.md)
Covered requirements: UI-01, UI-02, UI-03, UI-04, UI-05, NAV-01, NAV-02, NAV-03, NAV-04, NAV-05, DESKTOP-01
ADRs: [ADR-0001](../../../docs/adr/0001-chat-first-product-surface.md), [ADR-0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [ADR-0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

按照 Issue 04 记录的 `https://chatgpt.com/` 电脑端布局与交互基线，交付 BridGes 原创视觉的全局聊天外壳：可折叠左侧边栏、中央内容区和底部账户菜单。侧栏模块和顺序必须与产品要求完全一致，Logo 返回新聊天，收起后仍可恢复；旧“更多”和 Science Companion 工作台导航退出普通用户路径。尚未完成业务能力的入口只能呈现真实、可操作的空状态或禁用原因，不能使用“将在这里呈现”或假数据。

## Acceptance criteria

- [x] 电脑端全局布局明确以 `https://chatgpt.com/` 的侧栏/内容区交互为模板，同时使用 Issue 04 的 Claude-inspired BridGes 原创 Logo、图标和视觉令牌。
- [x] 侧栏从上到下严格为：BridGes Logo、搜索、关闭/收起边栏、新聊天、本地知识库、学习项目、任务安排、插件、用户画像、最近对话、底部账户菜单。
- [x] 点击或键盘激活 Logo 会进入新聊天；不会创建重复空会话，也不会跳回旧项目工作台。
- [x] 收起按钮可隐藏侧栏并保留清楚的恢复入口；刷新与路由切换时按既定规则保持状态，内容区宽度正确更新。
- [x] 底部账户菜单复用 Issue 08，固定显示“切换账号、密钥设置、个人资料、退出登录”。
- [x] 普通用户桌面导航中不存在“更多”、旧学习工作台、表达工作台、证据工作台、多媒体工作台和领域包入口。
- [x] 每个侧栏入口都有稳定路由、页面标题和不依赖浏览器后退的返回新聊天/返回上层路径；无权限时不渲染受保护子内容。
- [x] 各内容入口完整呈现 loading、empty、error、permission 和正常状态；尚无数据时给出中文解释与真实下一步操作，不使用硬编码样例或占位成功。
- [x] 搜索或列表加载失败显示可重试错误，不伪装成空列表；导航激活语义任一时刻只标记一个当前入口。
- [x] 电脑端仅用键盘可以按视觉顺序遍历侧栏、收起/恢复、进入页面、打开账户菜单并返回；焦点可见且无陷阱。
- [x] 约定电脑端视口下，长用户名、长导航文案和内容滚动不会遮挡收起按钮、账户菜单或主要操作。

## Verification

```powershell
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e -- --project=chromium
conda run -n agent python -m pytest -k "navigation or authorization or session"
```

人工验收：逐项对照 Issue 04 的 ChatGPT 电脑端行为矩阵及本 Issue 固定顺序，检查 Logo、收起/恢复、返回路径、无“更多”和底部菜单。

## Non-goals

- 不复制 ChatGPT 的品牌资产、专有文案或像素级视觉。
- 不在本 Issue 实现侧栏各业务模块的完整后端能力。
- 不设计、实现或验收移动端、移动抽屉、触摸手势或窄屏响应布局。

## Blocked by

- [04 — 建立 ChatGPT 桌面交互基线与 BridGes 原创视觉资产](./04-establish-desktop-design-baseline-and-brand-assets.md)
- [08 — 交付账户上拉菜单与个人资料设置](./08-deliver-account-menu-and-personal-settings.md)
- [11 — 交付持久化真实 Qwen 流式聊天纵向切片](./11-deliver-persisted-streaming-chat.md)

## Comments

“真实空状态”必须描述当前账户为什么没有内容并提供可执行下一步；它与只有标题和省略号的空壳页面不同。后续模块 Issue 应在这些稳定入口内替换为空状态、列表和详情闭环。

2026-08-03 交付完成，等待人工视觉验收。

**工作内容总结**

- 交付普通用户全局聊天外壳：新 `AppSidebar` 按固定 11 项顺序（Logo / 搜索 / 收起侧边栏 / 新聊天 / 本地知识库 / 学习项目 / 任务安排 / 插件 / 用户画像 / 最近对话 / 底部账户菜单），布局交互以 Issue 04 的 ChatGPT 电脑端基线为模板，视觉全部复用 BridGes 原创 Logo、图标与设计令牌。
- AppShell 重写：account 模式移除旧顶栏与移动抽屉，改为无顶栏侧栏 + 内容区；鉴权门控（loading / error / permission）与账户切换重挂载行为不变；project 模式保留旧外壳供遗留工作台增量替换（ADR-0016）。
- 收起为完全隐藏侧栏，内容区自动占满；恢复入口为内容区左上角的展开按钮 + 新聊天图标；状态持久化到 `bridges-sidebar-collapsed`，根布局内联脚本预写 `data-sidebar-collapsed` 配合 CSS 预隐藏，刷新与路由切换无闪烁、无 hydration 不一致。
- 最近对话迁入侧栏并接真实 API（`useRecentConversations` hook + `CHAT_LIST_CHANGED_EVENT`），具备 loading / error+重试 / 空态；聊天页与首页移除旧的第二列对话栏，变为单一居中消息列；Logo 与普通 Link 一样进入新聊天，不创建重复空会话。
- 新稳定路由：`/search` 搜索对话页（真实列表客户端过滤，loading / error+重试 / 空关键词 / 无匹配 / 结果五态）、`/knowledge-base`、`/tasks`、`/plugins` 三个真实空状态页（说明能力未开放原因 + 可执行的“返回新聊天”，无“将在这里呈现”与假数据）；`/account/projects` 改名「学习项目」、`/account/profile` 改名「用户画像」并替换占位文案。
- 普通用户导航中移除“更多”与全部旧工作台 / 领域包 / 全局科学伙伴 / 评测入口（旧路由仍可直达，仅不再被链接）；底部账户菜单原样复用 Issue 08 的固定四项。
- 键盘与可访问性：侧栏按视觉顺序 Tab 遍历、收起/恢复后焦点在按钮间正确转移、账户菜单键盘合同不变、焦点可见、单一 `aria-current`；长用户名与长文案省略号截断，最近对话区独立滚动，收起按钮与账户菜单始终可见。
- 新增 `issue12-shell-sidebar.spec.ts` 11 个 E2E 用例覆盖全部验收点；适配 t002 / t004 / issue08 / issue09 / issue11 既有规格并重新生成 issue08 视觉快照。

**代码审查 bug 修复总结**

- Standards：修复 `projects-page-client.tsx` 与 `account-page.tsx` 中残留的词汇表禁用旧称「科学项目空间」「画像与记忆中心」（违反 CONTEXT.md / domain.md 术语约定）。
- Standards/Spec 共同：修复收起侧栏后焦点掉入 body 的问题——收起后焦点移至「展开侧边栏」、展开后回到「收起侧边栏」，并补齐 `aria-controls`；相应 E2E 由“最多 Tab 8 次”的绕法改为直接焦点转移断言。
- 修复搜索页输入框 `autoFocus` 在路由进入时劫持焦点的问题。
- 核实无需修复：`/` 首页 SkipLink 由 `(public)/layout` 提供且恰为一个，`showSkipLink={false}` 是消除重复的正确做法；基线 Ctrl+Shift+O 快捷键基线自述“随版本变化”，本 Issue 不实现；三个模块空态页结构相似，有意不做过早抽象。
- code-review 双轴复核最终结果：Standards 0 项未解决缺陷，Spec 0 项未解决缺陷。

**验证结果**

- `npm --prefix apps/web run typecheck`：通过。
- `npm --prefix apps/web run build`：通过，路由表含 `/search`、`/knowledge-base`、`/tasks`、`/plugins`。
- `npm --prefix apps/web run test:e2e -- --project=chromium`：全量 100 passed / 0 failed（issue04/07/08/09/10/11/12 与 t002/t003/t004）。
- `conda run -n agent python -m pytest -k "navigation or authorization or session"`：39 passed（未改后端代码）。
- `scripts/check_contrast.py`：37/37 通过（未引入新色彩配对）。
