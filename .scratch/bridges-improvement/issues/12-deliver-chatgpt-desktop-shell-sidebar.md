# 12 — 按 ChatGPT 电脑端模板交付固定顺序侧栏

Status: ready-for-agent
Blocked by: [04 — 建立 ChatGPT 桌面交互基线与 BridGes 原创视觉资产](./04-establish-desktop-design-baseline-and-brand-assets.md), [08 — 交付账户上拉菜单与个人资料设置](./08-deliver-account-menu-and-personal-settings.md), [11 — 交付持久化真实 Qwen 流式聊天纵向切片](./11-deliver-persisted-streaming-chat.md)
Covered requirements: UI-01, UI-02, UI-03, UI-04, UI-05, NAV-01, NAV-02, NAV-03, NAV-04, NAV-05, DESKTOP-01
ADRs: [ADR-0001](../../../docs/adr/0001-chat-first-product-surface.md), [ADR-0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [ADR-0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

按照 Issue 04 记录的 `https://chatgpt.com/` 电脑端布局与交互基线，交付 BridGes 原创视觉的全局聊天外壳：可折叠左侧边栏、中央内容区和底部账户菜单。侧栏模块和顺序必须与产品要求完全一致，Logo 返回新聊天，收起后仍可恢复；旧“更多”和 Science Companion 工作台导航退出普通用户路径。尚未完成业务能力的入口只能呈现真实、可操作的空状态或禁用原因，不能使用“将在这里呈现”或假数据。

## Acceptance criteria

- [ ] 电脑端全局布局明确以 `https://chatgpt.com/` 的侧栏/内容区交互为模板，同时使用 Issue 04 的 Claude-inspired BridGes 原创 Logo、图标和视觉令牌。
- [ ] 侧栏从上到下严格为：BridGes Logo、搜索、关闭/收起边栏、新聊天、本地知识库、学习项目、任务安排、插件、用户画像、最近对话、底部账户菜单。
- [ ] 点击或键盘激活 Logo 会进入新聊天；不会创建重复空会话，也不会跳回旧项目工作台。
- [ ] 收起按钮可隐藏侧栏并保留清楚的恢复入口；刷新与路由切换时按既定规则保持状态，内容区宽度正确更新。
- [ ] 底部账户菜单复用 Issue 08，固定显示“切换账号、密钥设置、个人资料、退出登录”。
- [ ] 普通用户桌面导航中不存在“更多”、旧学习工作台、表达工作台、证据工作台、多媒体工作台和领域包入口。
- [ ] 每个侧栏入口都有稳定路由、页面标题和不依赖浏览器后退的返回新聊天/返回上层路径；无权限时不渲染受保护子内容。
- [ ] 各内容入口完整呈现 loading、empty、error、permission 和正常状态；尚无数据时给出中文解释与真实下一步操作，不使用硬编码样例或占位成功。
- [ ] 搜索或列表加载失败显示可重试错误，不伪装成空列表；导航激活语义任一时刻只标记一个当前入口。
- [ ] 电脑端仅用键盘可以按视觉顺序遍历侧栏、收起/恢复、进入页面、打开账户菜单并返回；焦点可见且无陷阱。
- [ ] 约定电脑端视口下，长用户名、长导航文案和内容滚动不会遮挡收起按钮、账户菜单或主要操作。

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
