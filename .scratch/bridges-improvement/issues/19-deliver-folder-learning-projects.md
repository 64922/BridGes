# 19 — 交付文件夹式学习项目
Status: ready-for-agent
Blocked by: [12](./12-deliver-chatgpt-desktop-shell-sidebar.md), [15](./15-deliver-recent-conversations-lifecycle.md), [16](./16-deliver-secure-chat-attachments.md)
Covered requirements: PROJ-01, CHAT-07, CHAT-10, NAV-02, DESKTOP-01
ADRs: [0001](../../../docs/adr/0001-chat-first-product-surface.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0020](../../../docs/adr/0020-layered-retrieval-and-mandatory-teaching-search.md), [0022](../../../docs/adr/0022-persisted-conversation-mode.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

把“学习项目”实现为当前账户内组织对话和项目文件的文件夹，而不是旧项目制课程工作台。交付项目列表、项目详情、创建、改名、删除、对话关联/移动/移除和项目文件管理，并把“选择学习项目”接入两种模式共用的“+”菜单。

从项目内创建的新对话默认使用学习模式；将已有对话加入项目不改写其历史消息。移除关联与删除原始对话或文件必须是不同操作，并在界面中清楚说明。

## Acceptance criteria

- [ ] “学习项目”侧边栏入口打开完整桌面列表页，可创建、改名、打开和删除当前账户项目。
- [ ] 项目详情只包含项目说明、相关对话、项目文件和新建学习对话入口，不出现旧课程节点或项目制教学工作流。
- [ ] 用户可把已有会话移动/关联到一个学习项目，也可从项目移除；历史消息、模式切换记录和附件不被改写。
- [ ] 用户可上传项目专属文件、查看状态、下载和移除；项目文件与全局知识库材料的作用域在界面中明确区分。
- [ ] 两种对话模式的“+”菜单均保留“选择学习项目”，选择结果可见、可清除并持久化到会话。
- [ ] 从项目中新建对话默认进入学习模式；用户仍可按对话模式规则切换，项目本身不强制课程流程。
- [ ] 删除项目要求确认，并让用户选择保留独立对话/原始文件或一并删除；事务失败时不产生半删除状态。
- [ ] 项目、会话和文件的全部读取与变更以账户归属校验；不能关联其他账户对象。
- [ ] 列表、详情和选择器具有中文 loading、empty、error、permission 和 recovery 状态，失败不伪装成空项目。
- [ ] 中文桌面键盘路径可完成创建、选择、移动、移除和删除，刷新与重启后归属不变。

## Verification

- 在 Conda `agent` 环境运行项目仓库、关联约束、删除策略、事务回滚和跨账户攻击测试。
- 运行前端类型检查，并以桌面 E2E 覆盖空项目、创建、改名、文件上传、会话移动、“+”菜单选择和删除恢复。
- 从项目创建学习对话，再切换模式并重启服务，核对项目归属、模式和历史不被改写。
- 人工确认普通用户路径中不再出现旧项目制教学工作台。

## Non-goals

- 不在项目对象中保存固定课程树、节点状态或强制学习流程。
- 不在本 Issue 实现检索排序和教学编排。
- 不实现或验收移动端布局与交互。

## Blocked by

- [Issue 12：ChatGPT 式桌面外壳与侧边栏](./12-deliver-chatgpt-desktop-shell-sidebar.md)
- [Issue 15：最近对话生命周期](./15-deliver-recent-conversations-lifecycle.md)
- [Issue 16：安全聊天附件](./16-deliver-secure-chat-attachments.md)

## Comments

首版一个会话归属至多一个学习项目，以保持“文件夹”心智模型和检索作用域清晰；若实现要支持多归属，需先重新确认领域合同。
