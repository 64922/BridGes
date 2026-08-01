# 13 — 交付新聊天输入区与完整空白态

Status: ready-for-agent
Blocked by: [04 — 建立 ChatGPT 桌面交互基线与 BridGes 原创视觉资产](./04-establish-desktop-design-baseline-and-brand-assets.md), [11 — 交付持久化真实 Qwen 流式聊天纵向切片](./11-deliver-persisted-streaming-chat.md), [12 — 按 ChatGPT 电脑端模板交付固定顺序侧栏](./12-deliver-chatgpt-desktop-shell-sidebar.md)
Covered requirements: UI-01, UI-03, UI-04, UI-05, CHAT-01, CHAT-02, CHAT-03, CHAT-04, CHAT-05, CHAT-06, CHAT-07, CHAT-08, DESKTOP-01
ADRs: [ADR-0001](../../../docs/adr/0001-chat-first-product-surface.md), [ADR-0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [ADR-0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [ADR-0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

把登录后的默认落点完善为 ChatGPT 式电脑端新聊天空白态和输入区，并使用 BridGes 原创视觉。输入区顶部按参考网页的可变文字机制轮换恰好五条已核查的简短学习名言；输入控件取消临时聊天、模型选择器和实时语音，只保留听写入口与发送。左侧“+”菜单交付六个明确入口的可用外壳，空白态展示带原创图标的论文搜索、文章人味化和生涯规划助手三张建议卡；所有入口沿正常消息、授权和审计流程工作，不伪造尚未实现的工具结果。

## Acceptance criteria

- [ ] 登录成功和点击“新聊天”均进入同一新聊天空白态；没有临时聊天入口、状态、路由或快捷键。
- [ ] 输入区顶部只轮换恰好五条简短学习名言，内容已记录出处/事实与版权检查；切换节奏遵循参考机制并尊重减少动态效果设置。
- [ ] 日常陪伴和学习模式均不显示模型选择器，用户不能从输入区修改固定模型。
- [ ] 输入区没有实时语音/语音通话入口，只保留听写按钮和发送按钮；未实现听写能力时显示真实不可用原因，不冒充成功。
- [ ] “+”菜单固定包含“上传文件/图片、论文搜索、文章人味化、生涯规划助手、选择学习项目、选择已启用插件”，使用 Issue 04 的原创图标。
- [ ] 尚未由后续 Issue 实现的工具入口只能预填结构化意图或显示明确不可用原因；不得产生假论文、假文件、假规划或假插件结果。
- [ ] 空白态显示“论文搜索、文章人味化、生涯规划助手”三张原创图标建议卡；点击后按参考网页逻辑预填或提交到正常消息流，不跳过授权、审计或对话保存。
- [ ] 发送后提供复制、重新生成、反馈等完整消息操作入口；每项均有中文可访问名称和真实状态，不显示无功能按钮。
- [ ] 输入框支持多行编辑、Enter/组合键规则、发送中禁重、停止与错误恢复；超长中文和粘贴内容不破坏电脑端布局。
- [ ] 新聊天页与“+”菜单完整覆盖 loading、empty、error、permission/key-unavailable、tool-unavailable 和正常状态，文案全部为中文且可操作。
- [ ] 电脑端仅用键盘可进入输入框、打开和遍历“+”菜单、触发建议卡、发送、停止及执行消息操作；Esc 关闭菜单并归还焦点。
- [ ] 页面按 `https://chatgpt.com/` 电脑端输入区与空白态交互基线实现，但不复制其品牌图形、图标和专有文案。

## Verification

```powershell
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e -- --project=chromium
conda run -n agent python -m pytest -k "conversation or message or capability"
```

人工验收：逐项确认五条名言、六个“+”菜单入口、三张建议卡、无临时聊天、无模型选择器、无实时语音及桌面键盘路径。

## Non-goals

- 不在本 Issue 完成文件摄取、arXiv、Humanizer、生涯规划、项目或插件的业务实现，只交付诚实可用的入口外壳与正常消息意图。
- 不实现实时语音通话或自动播放回答。
- 不设计、实现或验收移动端输入区和空白态。

## Blocked by

- [04 — 建立 ChatGPT 桌面交互基线与 BridGes 原创视觉资产](./04-establish-desktop-design-baseline-and-brand-assets.md)
- [11 — 交付持久化真实 Qwen 流式聊天纵向切片](./11-deliver-persisted-streaming-chat.md)
- [12 — 按 ChatGPT 电脑端模板交付固定顺序侧栏](./12-deliver-chatgpt-desktop-shell-sidebar.md)

## Comments

听写的真实 ASR 纵向能力由后续多模态 Issue 接入。本 Issue 只允许“明确不可用”或真实能力，不能保留当前会误导用户的硬编码按钮状态。
