# 14 — 交付对话双模式与可折叠思考摘要

Status: ready-for-human
Blocked by: [11 — 交付持久化真实 Qwen 流式聊天纵向切片](./11-deliver-persisted-streaming-chat.md), [13 — 交付新聊天输入区与完整空白态](./13-deliver-new-chat-composer-and-blank-state.md)
Covered requirements: CHAT-05, CHAT-06, CHAT-07, CHAT-08, CHAT-09, CHAT-10, CHAT-11, UI-01, UI-05, DESKTOP-01
ADRs: [ADR-0001](../../../docs/adr/0001-chat-first-product-surface.md), [ADR-0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [ADR-0022](../../../docs/adr/0022-persisted-conversation-mode.md), [ADR-0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

在同一聊天系统中交付对话级“日常陪伴 / 学习模式”。普通新聊天默认日常陪伴，从学习项目创建的对话默认学习模式；用户可在对话中切换，切换只影响后续消息并保存可见事件。两种模式共用输入区和工具入口，但采用不同角色合同。回答生成时按用户确认截图所示自动展开可读思考摘要，完成后折叠为“已思考（用时 X 秒）”，用户可再次展开；内容只展示步骤、证据、工具和质量检查摘要，绝不暴露原始思维链。

## Acceptance criteria

- [x] 每个对话持久化唯一当前模式；普通新聊天默认“日常陪伴”，项目新建学习对话默认“学习模式”。
- [x] 输入区附近使用清楚的“日常陪伴 / 学习模式”切换控件，替代原“聊天/工作”控件，两个模式都不出现模型选择器。
- [x] 切换会在消息流写入可见模式切换事件，只影响切换后的请求；既有消息、回答和引用不会被重写。
- [x] 日常陪伴模式使用自然、有分寸的个性化陪伴合同，可识别当前情境信号但不形成心理诊断或把单次情绪写成长期事实。
- [x] 学习模式使用因材施教老师合同，为后续画像、材料检索、教学规划、理解检查和适量测验保留明确编排接口，不恢复旧项目制教学页面。
- [x] 两种模式共用 Issue 13 的“+”菜单、建议卡、听写与发送入口；模式差异体现在回答策略，不复制两套聊天页面。
- [x] 生成开始后思考区域自动展开；生成完成后折叠并显示精确格式“已思考（用时 X 秒）”；点击或键盘激活可再次展开/折叠。
- [x] 思考摘要只包含可公开的步骤、采用的证据、工具调用进度和质量检查结论，不包含原始 Chain-of-Thought、系统提示、隐藏指令或逐 token 推理。
- [x] 工具失败、生成取消和断流时保留已完成摘要并显示中文状态；耗时基于实际生成生命周期，不使用硬编码数字。
- [x] 模式控件、切换事件和思考摘要完整覆盖 loading、empty/no-summary、streaming、error、permission 和完成状态，中文文案与用户截图呈现一致。
- [x] 刷新、重启和重新打开对话后，当前模式、切换历史、摘要折叠状态规则与消息顺序正确恢复。
- [x] 电脑端仅用键盘可以切换模式、展开/折叠摘要、停止和重试；控件具备正确 ARIA 状态，流式更新不抢焦点。
- [x] 两账户不能读取彼此的模式事件、思考摘要或耗时记录。

## Verification

```powershell
conda run -n agent python -m pytest -k "conversation_mode or thinking or message"
conda run -n agent python -m ruff check .
conda run -n agent python -m mypy src
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e -- --project=chromium
```

人工验收：按用户提供的生成中与完成后截图核对自动展开、完成折叠、耗时文本和再次展开行为，并确认页面未显示原始思维链。

## Non-goals

- 不在本 Issue 完成分层检索、画像长期写入、自动联网或测验评分实现；后续 Issue 在已建立的学习模式合同上接入。
- 不拆分两套聊天路由或恢复项目制课程工作台。
- 不设计、实现或验收移动端模式切换与思考摘要。

## Blocked by

- [11 — 交付持久化真实 Qwen 流式聊天纵向切片](./11-deliver-persisted-streaming-chat.md)
- [13 — 交付新聊天输入区与完整空白态](./13-deliver-new-chat-composer-and-blank-state.md)

## Comments

“思考摘要”是面向用户的过程说明，不是模型隐藏推理。实现时应由结构化进度事件和可披露结果生成，而不是截取供应商内部推理字段。

完成记录（2026-08-03）：后端 `conversations.mode` 双模式持久化 + `mode_events` 可见切换事件表（schema v3）+ `messages.thinking` 思考摘要列；`ModeContract` 为学习模式画像/材料检索/教学规划/理解检查/测验保留代码级编排接口（`_MODE_CONTRACTS` 单一事实源），当前只注入合同与编排步骤、不伪造未实现能力；SSE started/error 事件携带思考摘要，error 附带真实耗时，前端 error 态渲染不依赖重新加载间隙。人工验收提示：思考摘要的自动展开→折叠“已思考（用时 X 秒）”行为已由 e2e（协议级替身 + hang 流）覆盖，请按本机真实 Qwen Key 发送一问答核对流式表现与截图一致；当前会话模型无法直接查看用户截图，若截图呈现与实现存在差异请以截图为准反馈修正。
