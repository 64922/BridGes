# Issue 04：增大首页格言字号并将免责声明钉至屏幕底部

Status: ready-for-agent

Type: task

Priority: P2

User stories: US-04

## What to build

调整新聊天首页（`/` 登录后空白态）两处排版，均为用户指定的纯展示变更：

1. **格言字号增大一档**：`apps/web/src/components/bridges/RotatingQuote.tsx:77` 的 `fontSize` 从 `var(--text-xl)`（1.25rem）升至 `var(--text-2xl)`（1.5rem）。出处行（"——《庄子·养生主》"）的 `--text-sm` 保持不变。
2. **免责声明钉到屏幕底部**：`apps/web/src/components/bridges/NewChatHome.tsx:114-116` 的 `<p className={styles.blankStateNote}>`（"BridGes 的回答会标注依据与来源；重要内容请核对引用。"）从输入框正下方的文档流位置，改为主内容区底部常驻——推荐做法：将 note 移出 `.blankStateInner` 居中容器，作为 `.blankState` 的末尾子元素并以 `margin-top: auto` 钉底（`.blankState` 已是 `flex: 1` 的纵向 flex 容器，`chat.module.css:52-60`），保持 `text-align: center`、`--text-xs`、`--color-text-tertiary` 样式不变。居中块（格言+输入框）视觉位置不因钉底而偏移。

两处变更只影响新聊天首页空白态；对话页无此格言与免责声明确认无误（已全量检索，生产代码中该文案仅 `NewChatHome.tsx` 一处）。

## 已验证复现与根因摘要

- 现场截图（`3.png`）：格言"吾生也有涯，而知也无涯。"以 `--text-xl` 渲染偏小；免责声明紧跟输入框下缘，距屏幕底部有大片空白。
- 代码事实：`RotatingQuote` 使用内联样式（fontSize 令牌直写）；note 位于 `.blankStateInner` 内部流中，随居中块一起垂直居中，因此无法靠外层间距自然落底，需要结构调整（移出居中容器）+ `margin-top: auto`。
- e2e 现状：`apps/web/e2e/issue13-new-chat-composer.spec.ts:194-226` 断言 `empty-quote` 可见性与轮换 `data-quote-index`，字号变更不影响这些断言；无测试锁定 note 位置。
- 高度链：`.blankState` 撑满 `.chatMain`（flex 链至 `AppShell`），`margin-top: auto` 方案依赖该链未被破坏，实现时需验证短视口（如 600px 高）下 note 与输入框不重叠——flex 容器在内容超高时 `margin-top: auto` 退化为 0，note 自然跟在内容后，页面可滚动，满足不重叠要求。

## 非目标

- 不改格言内容、轮换节奏、淡入淡出与 `prefers-reduced-motion` 行为。
- 不改免责声明文案、字号与颜色。
- 不改对话页、开发模板页（`app/templates/chat/chat-template.tsx:445` 的同款 footerNote 属开发脚手架，保持原样）。
- 不调整侧边栏、`AppShell` 或其他页面布局。

## Acceptance criteria

- [ ] 首页格言以 `--text-2xl` 渲染，出处行样式不变，轮换与无障碍行为（`data-testid="empty-quote"`、`data-quote-index`、reduced-motion 静止）全部保持。
- [ ] 免责声明在常规桌面视口（≥900px 高）下位于主内容区底部，与视口底边保持自然间距（沿用 `.blankState` 的 `padding`），不被输入框遮挡、不随居中块上下漂移。
- [ ] 短视口（≤600px 高）与窗口缩放过程中，note 与输入框、格言不重叠，内容超高时页面可正常滚动到底看到 note。
- [ ] 输入框聚焦、发送出错横幅（`ChatSendErrorBanner`）出现/消失时，note 位置稳定不跳动。
- [ ] `issue13-new-chat-composer.spec.ts` 及 Web 单元测试、类型检查全部通过。

## Test plan

- 组件/e2e 断言：渲染新聊天首页，断言 `empty-quote` 计算样式 `font-size` 为 24px（1.5rem）；断言 note 元素底边与主内容区底边的距离等于容器 padding（容差若干像素）。
- 在 e2e 或手动清单中加入 600px 短视口检查：note 不与 composer 相交。
- 跑 `apps/web` 单元测试、类型检查与 `issue13-new-chat-composer.spec.ts`。

## Observability & rollback

- 纯样式/结构变更，无需新增日志；回滚即还原 `fontSize` 令牌与 note 位置。

## Blocked by

- None

## Comments

- 2026-08-12：字号档位（xl→2xl）与"钉底用 margin-top:auto 而非 absolute 定位"为推荐实现，用户确认目标效果为"略微增大"与"下移到屏幕底部"。
- 后续讨论、实施证据和验收结果追加于本节。
