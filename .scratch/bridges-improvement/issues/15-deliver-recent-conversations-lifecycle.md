# 15 — 交付最近对话与完整会话生命周期
Status: done
Blocked by: [11](./11-deliver-persisted-streaming-chat.md), [12](./12-deliver-chatgpt-desktop-shell-sidebar.md), [14](./14-deliver-conversation-modes-and-thinking-summary.md)
Covered requirements: NAV-05, CHAT-01, CHAT-06, DESKTOP-01
ADRs: [0001](../../../docs/adr/0001-chat-first-product-surface.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0022](../../../docs/adr/0022-persisted-conversation-mode.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

交付桌面侧边栏中的真实“最近”列表以及会话的置顶、改名、删除和跳转闭环。列表从持久化会话数据生成，同时包含“日常陪伴”和“学习模式”，不得使用硬编码演示项。所有读取和变更都以稳定账户 ID 为边界，并在刷新、重启和账户切换后保持一致。

侧边栏中的会话项显示标题、模式标识、更新时间和可选的学习项目归属。排序规则应稳定且可解释：置顶项优先，其余按最近活动时间排列。交互使用完整中文桌面菜单或对话框，并提供加载、空内容、错误、权限拒绝和操作失败后的恢复路径。

## Acceptance criteria

- [x] “最近”同时展示日常陪伴和学习模式会话，并以可访问文字而非仅颜色区分模式。
- [x] 会话标题来自首轮内容生成或用户改名；未命名草稿不会伪装成已存在的临时聊天。
- [x] 用户可以置顶、取消置顶、改名和删除自己的会话，刷新与重新启动后结果保持不变。
- [x] 删除前明确确认影响；删除当前会话后返回新聊天，不留下不可恢复的空白详情页。
- [x] 点击会话项进入正确会话并恢复消息、当前模式、附件、引用和项目归属。
- [x] 任何会话读取、改名、置顶和删除都强制校验账户归属；猜测其他账户 ID 只返回安全拒绝。
- [x] 账户切换后列表立即切换到目标账户，不短暂显示上一账户数据。
- [x] 列表具有中文加载骨架、首次使用空态与“新聊天”入口、可重试错误态、权限态和操作失败回滚提示。
- [x] 键盘可以聚焦会话项及其菜单，菜单支持 Esc 关闭和焦点归还；桌面窄窗口不遮挡主内容。
- [x] 页面不包含标题加占位文字、硬编码成功状态或无效按钮。

## Verification

- 在 Conda `agent` 环境运行会话仓库、排序、账户隔离、删除事务和重启恢复测试。
- 运行前端类型检查，并以桌面 Playwright 用例覆盖置顶、改名、删除、跨模式跳转、空态、失败重试和账户切换。
- 使用两个账户执行越权 API 测试，确认列表与所有变更接口均不泄露资源是否存在。
- 人工从新聊天创建两种模式会话，重启 `BridGes start` 后核对顺序、模式标识和当前路由。

## Non-goals

- 不在本 Issue 实现跨文档、图片和项目的统一搜索。
- 不在本 Issue 实现学习项目的创建和文件管理。
- 不实现或验收移动端布局与交互。

## Blocked by

- [Issue 11：持久化流式聊天](./11-deliver-persisted-streaming-chat.md)
- [Issue 12：ChatGPT 式桌面外壳与侧边栏](./12-deliver-chatgpt-desktop-shell-sidebar.md)
- [Issue 14：对话模式与思考摘要](./14-deliver-conversation-modes-and-thinking-summary.md)

## Comments

“最近”是已持久化会话的视图，不是独立副本。模式切换只影响后续消息，但列表显示会话当前模式。

完成记录（2026-08-03）：完成 schema v4 最近会话元数据迁移，新增稳定账户边界内的置顶、改名、删除、学习项目归属与确定性排序；侧栏接入真实持久化列表、模式/更新时间、加载/空态/权限/失败恢复状态，以及中文菜单、确认框和键盘焦点恢复。账户切换通过修订号门控首帧数据，避免旧账户列表短暂泄露。验证通过完整 Python 套件（1194 passed, 2 skipped）、前端类型检查、OpenAPI 同步测试与桌面 Playwright Issue 15 用例（2 passed）。
