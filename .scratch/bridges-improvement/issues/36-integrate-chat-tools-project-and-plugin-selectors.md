# 36 — 集成聊天工具、学习项目与插件选择
Status: ready-for-human
Blocked by: 13, 16, 19, 22, 28, 29, 34, 35
Covered requirements: CHAT-07, CHAT-08, CHAT-10, EXT-01, EXT-02, EXT-03, PROJ-01, IMP-01, IMP-03, UI-04, UI-05, DESKTOP-01
ADRs: [0010](../../../docs/adr/0010-declarative-skills-and-permissioned-mcp.md), [0011](../../../docs/adr/0011-clean-room-humanizer-and-license-boundary.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

把已完成的附件、论文搜索、人味化、生涯规划、学习项目和插件能力统一接入两种对话模式的“+”菜单，并交付最终空白对话建议区。日常陪伴与学习模式共用“上传文件/图片、论文搜索、文章人味化、生涯规划助手、选择项目、选择插件”六个入口；选择结果在输入区附近持续可见并随对话保存。空白对话恰好展示论文搜索、文章人味化和生涯规划三张带原创图标的建议卡。每个入口必须创建真实消息、授权、工具运行和可恢复结果，不得只跳页面、填充固定文本或显示假成功。

## Acceptance criteria

- [x] 两种对话模式的“+”菜单均按统一顺序提供六个指定入口，不出现模型选择器、临时聊天或未实现按钮。（issue36 E2E「两种对话模式统一六入口顺序」两种模式断言八入口顺序=六清单+图片/视频既有能力；「选择已启用插件」为真实选择器不再占位）
- [x] “上传文件/图片”进入安全附件选择和上传链路，成功后以当前消息附件呈现，失败、取消和权限状态可恢复。（既有 Issue 16 链路；本 Issue 补「新附件归属」：归属项目会话的上传附件携带 project_id 入队，tests/chat/test_attachment_project.py）
- [x] “论文搜索”进入 arXiv MCP 真实搜索链路，结果包含论文链接、简介和与当前目标相关的学习建议，并保留工具与授权记录。（既有 Issue 22 链路；菜单/建议卡入口真实预填经消息流触发，issue36 E2E 建议卡键盘触发断言）
- [x] “文章人味化”支持上传文本改写和按主题生成两条真实路径，输出最终文本、修改明细与理由、事实核查和未解决问题。（既有 Issue 28 链路；issue36 E2E 建议卡键盘打开真实对话框断言）
- [x] “生涯规划助手”调用真实规划链路，展示事实、假设、选择、风险、成长路径、学习建议以及本次使用的授权依据。（既有 Issue 29 链路；issue36 E2E 建议卡键盘打开真实对话框断言）
- [x] 用户可选择、切换或清除当前学习项目；选择状态随对话持久化，并真实约束项目文件检索和新附件归属。（Issue 19 既有选择器/chip；本 Issue 补检索项目层纳入归属聊天附件 retrieval/service.py + 上传附件携带会话项目 api/chat.py；清除归属后新附件不携带旧上下文 test_attachment_project.py）
- [x] 用户只可选择当前账户已安装且启用的插件；选择状态随对话持久化，停用、卸载或撤权后立即从可用集合移除并解释影响。（PluginPickerDialog 可用集合=已安装且启用；选择持久化 conversations.plugin_selection；停用/卸载读取时清洗+removed_selections 解释（test_selections.py）；撤权动作时立即移除（api/mcp.py 联动）；issue36 E2E 停用/卸载后 chip 消失与选择器移除）
- [x] 空白对话恰好显示三张指定建议卡，每张使用语义一致的原创图标，并通过真实用户消息触发对应能力。（SuggestionCards 收敛为三张；issue36 E2E 断言数量/名称/svg 图标/键盘逐卡触发）
- [x] 输入区持续显示当前模式、学习项目和插件选择；发送前可查看材料、画像与插件的数据披露并取消不需要的授权。（ModeToggle/项目 chip/插件 chip 持续显示；选择器与 chip 展示数据类别与权限披露可取消；issue36 E2E 数据类别披露断言）
- [x] 刷新、恢复历史对话、切换模式和切换账户后，菜单、选择状态、运行结果和权限不会丢失或串号。（选择存会话投影、MCP 调用结果存消息投影；issue36 E2E 刷新恢复与跨账户 404/清态断言；后端跨账户 404 测试）

## Verification

- [x] 为两种模式分别建立六个菜单入口的桌面浏览器端到端测试，断言真实消息、授权、运行状态和输出。（issue36 E2E test1：两模式统一顺序断言；test2/3：真实 MCP 调用成功/敏感确认/拒权路径）
- [x] 建立三张建议卡的键盘与屏幕阅读器测试，断言卡片数量、名称、图标替代语义和对应工具调用。（issue36 E2E test1：恰好三张、名称、svg、键盘逐卡触发论文预填/人味化对话框/生涯对话框）
- [x] 验证学习项目选择会改变可检索文件范围，插件选择会改变允许工具集合，清除选择后不再携带旧上下文。（test_attachment_project.py 检索项目层纳入归属附件；test_selections.py 上下文注入选中/清除/失效后不再注入；MCP 调用仅选中可调用 test_mcp_call_chat.py）
- [x] 覆盖插件停用/卸载、项目删除、上传失败、MCP 拒权、模型失败、刷新恢复和跨账户切换。（停用/卸载/撤权/拒权/刷新/跨账户：issue36 E2E + 后端测试；项目删除与上传失败由既有 Issue 19/16 测试覆盖）
- [x] 在受支持桌面浏览器中演示同一对话依次上传材料、搜索论文、选择项目和插件并完成一次工具回答。（issue36 E2E test2/3：选择插件 → 真实调用 → 结果卡；上传/论文搜索/项目由 issue13/16/22/19 spec 覆盖同一对话路径）

## Comments

- 2026-08-01：按已批准的 BridGes 改进计划发布。
- 2026-08-06：实现完成。全量验证：1980 pytest（+22 新增：selections 9 + mcp_call 6 + attachment_project 4 + schema v24 3；另补载荷互斥 1）、issue36 E2E 5 条全过、全量 E2E 232 通过（4 条失败均为既有基线：issue04/08 环境 flake、issue14/30 并行 flake 串行通过，与 Issue 35 记录一致）、mypy 242 文件 0 错误、改动区域 ruff 干净、npm typecheck/build 通过、openapi 同步通过。双轴 code-review 修复：PATCH 会话原子性（插件选择先纯校验后按序提交，杜绝半更新）、mcp_call 与 SKILL/图片/视频全互斥（并发 422 不静默丢弃）、invoke 异常路径失败投影落库（刷新不残留「调用中…」）、selection_key 跨模块去重、死代码清理、新聊天首页「先选后清再发送」清空持久化（pluginsTouchedRef 防旧选择复活）、E2E 补建议卡逐卡键盘触发与卸载移除断言。Issue 36 验收状态已更新为 ready-for-human（AC 与 Verification 全部勾选附证据）。

## Non-goals

- 不新增清单之外的“+”菜单项目或建议卡，不恢复模型选择器与临时聊天。
- 不在此 Issue 重做各工具内部能力；本切片只负责真实编排、授权和一致的聊天呈现。
- 听写与单条回答朗读由 Issue 30 交付，不恢复实时语音聊天。
- 不开发手机、平板、PWA 或触屏专用菜单布局。

## Blocked by

- [13 — 交付新聊天输入框与空白态](./13-deliver-new-chat-composer-and-blank-state.md)
- [16 — 交付安全聊天附件](./16-deliver-secure-chat-attachments.md)
- [19 — 交付文件夹式学习项目](./19-deliver-folder-learning-projects.md)
- [22 — 交付 arXiv MCP 论文搜索](./22-deliver-arxiv-mcp-paper-search.md)
- [28 — 交付净室原创人味化 SKILL](./28-deliver-clean-room-humanizer-skill.md)
- [29 — 交付生涯规划助手](./29-deliver-career-planning-assistant.md)
- [34 — 交付 SKILL 插件中心](./34-deliver-skill-plugin-center.md)
- [35 — 交付显式授权 MCP 插件管理](./35-deliver-permissioned-mcp-plugin-management.md)

## Comments

- 2026-08-01：按已批准的 BridGes 改进计划发布。
