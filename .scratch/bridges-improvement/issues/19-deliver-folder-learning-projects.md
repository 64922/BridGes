# 19 — 交付文件夹式学习项目
Status: ready-for-human
Blocked by: [12](./12-deliver-chatgpt-desktop-shell-sidebar.md), [15](./15-deliver-recent-conversations-lifecycle.md), [16](./16-deliver-secure-chat-attachments.md)
Covered requirements: PROJ-01, CHAT-07, CHAT-10, NAV-02, DESKTOP-01
ADRs: [0001](../../../docs/adr/0001-chat-first-product-surface.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0020](../../../docs/adr/0020-layered-retrieval-and-mandatory-teaching-search.md), [0022](../../../docs/adr/0022-persisted-conversation-mode.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

把“学习项目”实现为当前账户内组织对话和项目文件的文件夹，而不是旧项目制课程工作台。交付项目列表、项目详情、创建、改名、删除、对话关联/移动/移除和项目文件管理，并把“选择学习项目”接入两种模式共用的“+”菜单。

从项目内创建的新对话默认使用学习模式；将已有对话加入项目不改写其历史消息。移除关联与删除原始对话或文件必须是不同操作，并在界面中清楚说明。

## Acceptance criteria

- [x] “学习项目”侧边栏入口打开完整桌面列表页，可创建、改名、打开和删除当前账户项目。
- [x] 项目详情只包含项目说明、相关对话、项目文件和新建学习对话入口，不出现旧课程节点或项目制教学工作流。
- [x] 用户可把已有会话移动/关联到一个学习项目，也可从项目移除；历史消息、模式切换记录和附件不被改写。
- [x] 用户可上传项目专属文件、查看状态、下载和移除；项目文件与全局知识库材料的作用域在界面中明确区分。
- [x] 两种对话模式的“+”菜单均保留“选择学习项目”，选择结果可见、可清除并持久化到会话。
- [x] 从项目中新建对话默认进入学习模式；用户仍可按对话模式规则切换，项目本身不强制课程流程。
- [x] 删除项目要求确认，并让用户选择保留独立对话/原始文件或一并删除；事务失败时不产生半删除状态。
- [x] 项目、会话和文件的全部读取与变更以账户归属校验；不能关联其他账户对象。
- [x] 列表、详情和选择器具有中文 loading、empty、error、permission 和 recovery 状态，失败不伪装成空项目。
- [x] 中文桌面键盘路径可完成创建、选择、移动、移除和删除，刷新与重启后归属不变。

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

本次交付：Schema v9 新增 learning_projects 表（账户外键级联）并将 document_records 重建扩展 source=project_file 与可空 project_id（迁移保留全部存量行/分块/向量/索引）；新增 LearningProjectService 与 /learning-projects 路由（创建/列表/详情/改名/删除、项目文件上传/列表/下载/移除，全程 ScopedConnection 账户作用域、跨账户统一 404）；删除项目 contents=keep 单事务内对话置空归属+项目文件级联清除，contents=delete 事务内拒绝流式中对话（409 generation_in_progress）后级联删除对话/附件/文件，无半删除状态；PATCH /chat/conversations 支持 project_id（显式 null 移出），移动不改写消息/模式事件/附件；摄入管道支持项目文件作用域（list_project_materials/delete_project_material），知识库列表不混入项目文件；chat 路由项目校验从旧 ProjectService 切换到新服务，旧 /projects 工作台不触碰。前端：/account/projects 重写为文件夹列表页（新建/改名/删除对话框，删除必选保留对话/一并删除并说明后果），新增 /account/projects/[id] 详情页（项目说明、相关对话及移出、项目文件 XHR 进度上传+状态轮询+下载/移除、作用域说明文案、专用 404 态、新建学习对话默认 study 模式）；Composer“+”菜单“选择学习项目”由占位实现为真实选择器（chip 可见/可清除，新聊天随创建写入、既有会话即时 PATCH 持久化，两种模式共用）；侧边栏对话菜单新增移动到/移出学习项目，徽标显示项目名；列表/详情/选择器均具中文 loading/empty/error/permission/recovery 态，失败不伪装为空。验证：pytest 1052 通过（tests/learning_projects 22 新增，含仓库/关联/删除策略/事务回滚/跨账户攻击/v8→v9 迁移）；mypy strict 仅两处既有 credentials 错误；ruff 改动文件干净；openapi.json 与 generated.ts 重新生成、契约同步测试通过；npm typecheck/build 通过（npm lint 仓库从未配置，属既有状态）；桌面 E2E 139 通过/3 失败（issue19 11/11；issue04/issue08/issue12 各 1 个失败经 HEAD 对照确认为预置失败），同步修复 issue13 占位断言与 t004 旧 UI 依赖，并修复 mocked-auth 规格因 /api/learning-projects 401 清 cookie 的级联问题；UI 参考本地 ChatGPT 截图（chatgpt.com 实时抓取受登录墙阻挡）。

双轴代码审查修复（9 处）：会话 PATCH 携带 project_id 时与标题/置顶合并为单事务原子更新（消除半更新），显式 name:null 由静默 no-op 改为 422，删除项目流式检查移入事务消除 TOCTOU，项目摘要 SQL 提取共享 SELECT，侧边栏/对话页/详情页移动与移出逻辑收敛为共享 helper 并统一错误路径，Composer 项目 prop 对齐 project_id 命名消除双形状，选择器补齐 classifyApiError 区分的 permission 态，删除对话框明确“对话及其中原始文件（附件）保留、仅项目文件随项目删除”，详情页作用域文案改为归属语义诚实表述（检索接入属 Issue 20/ADR-0020）。
