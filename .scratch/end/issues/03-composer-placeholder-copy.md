# Issue 03：更新输入框占位文案以体现全部能力

Status: resolved

Type: task

Priority: P2

User stories: US-03

## What to build

将聊天输入框的占位文字从"向 BridGes 提问，或描述你的学习目标"改为用户指定的新文案：

> 和 BridGes 一起学习，可以搜论文、人味化你的文章、生涯规划或者生成图片或视频

改动点为 `apps/web/src/components/bridges/Composer.tsx:395` 的 `placeholder` 属性（硬编码单一来源）。`Composer` 被新聊天首页（`NewChatHome.tsx`，`variant="new-chat"`）与对话页共用，一处修改两态同时生效。

## 已验证复现与根因摘要

- 现场截图（`2.png`）显示新聊天首页输入框占位为旧文案；对话页截图（`屏幕截图 2026-08-12 102208.png`）显示同一旧文案。
- 代码事实：placeholder 仅在 `Composer.tsx:395` 出现一次；全仓库 e2e/单元测试均未以 `getByPlaceholder` 或文案字面量锁定该属性（已全量检索 `apps/web` 下 `*.spec.*`/`*.test.*`），改动无测试破坏面。
- 新文案约 36 个全角字符，输入框容器最大宽度 46rem（`chat.module.css:.blankStateInner`），按 `--text-base` 估算单行可容纳，不会截断；placeholder 不折行，实现时需在两种变体下目视确认。

## 非目标

- 不修改其他任何文案、语音听写提示、发送按钮或模式切换文案。
- 不调整输入框尺寸、内边距或字体。
- 不改开发模板页（`apps/web/src/app/templates/`）中的任何内容。
- 不引入文案配置化/国际化机制（当前无此基础设施，保持硬编码一致）。

## Acceptance criteria

- [ ] 新聊天首页输入框占位显示为新文案，无截断、无换行错位。
- [ ] 已有对话页（`/chat/[conversationId]`）输入框占位同步显示新文案。
- [ ] 学习模式与日常陪伴两种模式下文案一致（Composer 不分模式）。
- [ ] 输入任意字符后占位消失、清空后恢复，既有交互行为不变。
- [ ] 既有 e2e（含 `issue13-new-chat-composer.spec.ts`）与 Web 单元测试全部通过；如存在依赖旧文案的隐性断言则同步更新。

## Test plan

- 新增或更新一个组件级断言：渲染 `Composer` 两种变体，断言 textarea 的 `placeholder` 为新文案字面量。
- 跑 `apps/web` 单元测试、类型检查与 `issue13-new-chat-composer.spec.ts`；目视验证 1280px 与常见窄视口下无截断。

## Observability & rollback

- 纯文案变更，无需新增日志；回滚即还原该字符串。

## Blocked by

- None

## Comments

- 2026-08-12：文案全文由用户指定，逐字采用，不润色。

## Answer

- 2026-08-12：已在 `apps/web/src/components/bridges/Composer.tsx` 将共享输入框 placeholder 更新为“和 BridGes 一起学习，可以搜论文、人味化你的文章、生涯规划或者生成图片或视频”；`conversation` 与 `new-chat` 两种变体共用该来源，因此新聊天首页和已有对话页同步生效。
- 新增 `apps/web/src/components/bridges/Composer.test.tsx`，断言两种变体的 textarea 均使用新文案。
- 验证结果：Composer 组件测试 2/2 通过；Web 单元测试 30/30 通过；`npm run typecheck` 通过；`issue13-new-chat-composer.spec.ts` 提升权限单 worker 执行 6/8 通过，另一个视口用例单独重跑通过。剩余“创建中显示状态”用例因既有 `composer-sending-status` 元素缺失失败，与本次 placeholder 改动无关。
