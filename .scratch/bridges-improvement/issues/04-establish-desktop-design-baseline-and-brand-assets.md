# 04 — 建立 ChatGPT 桌面交互基线与 BridGes 原创视觉资产

Status: ready-for-agent
Blocked by: [03 — 展开 BridGes 品牌与包名迁移并移除 `.env` 配置](./03-rename-bridges-and-remove-env-config.md)
Covered requirements: UI-01, UI-02, UI-03, UI-04, UI-05, NAV-01, DESKTOP-01
ADRs: [ADR-0001](../../../docs/adr/0001-chat-first-product-surface.md), [ADR-0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [ADR-0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

以 [ChatGPT](https://chatgpt.com/) 电脑端当前页面作为信息架构、可折叠侧栏、聊天区、输入区、消息操作和账户菜单的布局与交互模板，先形成带日期的行为对照基线，再建立 Claude-inspired 的温暖、克制、高可读性美术语言。所有品牌图形、Logo、模块图标、令牌和组件外观必须为 BridGes 原创，并落到可运行的登录、注册、聊天内容页、列表页、详情页和设置页桌面模板中，供后续功能 Issue 直接复用。

## Acceptance criteria

- [ ] 形成带采集日期的中文参考矩阵，逐项记录 `https://chatgpt.com/` 电脑端的页面骨架、侧栏、输入区、消息流、建议入口、搜索和账户菜单行为；对无法访问或地区差异有明确说明。
- [ ] 参考矩阵明确区分“借鉴的布局/交互”与“禁止复制的商标、Logo、品牌图形、插画、专有文案和像素级视觉细节”。
- [ ] 建立 Claude-inspired 的颜色、字体、层级、留白、圆角、阴影、边框、动效、焦点、成功/警告/错误状态令牌，并通过对比度检查。
- [ ] 设计表达“连接用户与知识之桥”的原创 BridGes Logo，交付横向完整版、独立图标版、单色版、浅色背景版、深色背景版及常用桌面尺寸。
- [ ] 设计统一原创图标集，至少覆盖 Logo/新聊天、搜索、收起/展开、本地知识库、学习项目、任务安排、插件、用户画像、最近、账户、上传文件/图片、论文搜索、文章人味化、生涯规划、听写、发送、复制、重试、反馈、朗读和状态提示。
- [ ] 可运行桌面模板覆盖登录、注册、聊天内容、列表、详情和设置页面；使用相同导航、表单、卡片、菜单、对话框和反馈组件，不出现“将在这里呈现”等占位页面。
- [ ] 每类页面模板都有真实的 loading、empty、error、permission/unauthenticated 和正常内容状态，使用清楚一致的中文文案；状态不能只靠颜色表达。
- [ ] 仅用键盘即可遍历模板中的导航、表单、菜单、对话框和主要操作，焦点顺序、可见焦点、Esc 关闭与焦点归还正确。
- [ ] 在约定的电脑端视口进行视觉回归；长中文、代码、公式、表格、文件名和错误信息不遮挡主要操作。

## Verification

```powershell
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e -- --project=chromium
```

人工验收：在电脑端逐页对照参考矩阵、Logo 资产清单、图标清单、深浅主题与键盘路径；确认视觉具有 BridGes 原创性，而非 ChatGPT 或 Claude 的品牌复制品。

## Non-goals

- 不复制 ChatGPT、OpenAI、Claude 或 Anthropic 的商标、Logo、品牌插画和像素级组件。
- 不在本 Issue 完成所有业务后端；模板状态由后续纵向 Issue 接入真实数据。
- 不设计、实现或验收移动端、响应式移动布局、移动抽屉和触摸手势。

## Blocked by

- [03 — 展开 BridGes 品牌与包名迁移并移除 `.env` 配置](./03-rename-bridges-and-remove-env-config.md)

## Comments

参考网页会变化，因此行为基线必须记录日期并进入仓库。后续页面 Issue 必须复用本 Issue 的令牌和组件，同时各自交付完整业务状态，不能把模板当作最终空壳。
