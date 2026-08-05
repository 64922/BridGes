# 34 — 交付 SKILL 插件中心
Status: ready-for-human (验收通过，等待用户确认)
Blocked by: 04, 12, 16, 28
Covered requirements: EXT-01, EXT-02, EXT-03, IMP-02, IMP-03, B-01, SCORE-02, SCORE-03, UI-05, DESKTOP-01
ADRs: [0010](../../../docs/adr/0010-declarative-skills-and-permissioned-mcp.md), [0011](../../../docs/adr/0011-clean-room-humanizer-and-license-boundary.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

交付完整的电脑端插件中心，并先把内置及用户上传的声明式 SKILL 做成可运行的端到端能力。页面清楚区分内置、已安装、已停用和安装失败状态；PDF、Documents 与原创 bridges-humanizer 随应用发布并默认安装，展示固定版本、来源、能力和授权状态。用户可上传只含 SKILL.md、静态参考资料、模板及资源的声明式包，在安装前完成安全检查和内容预览，并可按账户启用、停用和卸载用户包。插件卡片必须对应真实运行注册与审计状态，不能只是目录展示或无 handler 按钮。

## Acceptance criteria

- [x] 插件中心完整呈现内置、已安装、已停用和失败四类状态，并为加载、空、错误、权限不足、安装中、成功及恢复提供成品页面状态。
      （/plugins 页面：内置三卡+启停、我的插件已安装/已停用/安装失败卡；StateBlock 六态 + 上传对话框 checking/installing/done 过程态；E2E issue34 覆盖）
- [x] PDF、Documents 和 bridges-humanizer 以只读内置包随应用安装，展示固定版本、能力、来源与授权；账户可启停，但不能篡改或卸载内置包内容。
      （plugins/registry.py 三个内置清单；humanizer 与 SKILL 注册表同源；卸载内置返回 403 builtin_not_mutable；无任何内容修改端点；test_plugin_service + E2E）
- [x] 三个默认能力均有可演示真实路径：PDF 与 Documents 能处理受支持附件，bridges-humanizer 能按其既有合同生成结果，而非返回固定样例。
      （内置卡「演示」→ 上传附件 → parsers.parse_document 真实解析（单元含真实 PDF via PyMuPDF、E2E 用 md）；humanizer「在聊天中使用」→ 首页自动打开 HumanizerDialog，走 Issue 28 既有真实生成合同，不返回固定样例）
- [x] 用户上传包只允许声明式 SKILL.md、静态参考、模板和资源；脚本、可执行文件、符号链接、路径穿越、越界引用和不受支持文件被拒绝并说明具体原因。
      （plugins/checker.py：扩展名白名单+类别名、S_IFLNK 检测、normpath 穿越拒绝、SKILL.md 引用越界检查、损坏/超限/隐藏文件拒绝；37 条检查器测试逐项断言中文原因）
- [x] 安装确认前展示包名、固定版本、内容清单、声明能力、来源和将接收的数据类别；用户取消时不留下半安装状态。
      （check 为纯函数不落库不落对象（test_check_package_is_side_effect_free）；前端预览对话框展示全部信息+文件清单表格；取消/Esc 无残留）
- [x] 用户包可按当前账户启用、停用和卸载；状态、固定版本及审计记录在刷新、重登和应用重启后保持一致。
      （skill_packages 持久化 + test_state_survives_service_recreation 重建 service 读库验证）
- [x] 安装失败进入可恢复失败状态，不污染运行注册表；修正包后可重新检查和安装。
      （install_failed 记录不持有对象、不进已安装集合（test_failed_install_does_not_enter_running_registry）；同标识修正包覆盖重装；声明内置标识的坏包不落失败卡）
- [x] 每次安装、启停、卸载和调用都记录账户、版本、授权数据类别、时间与结果，但不复制秘密或完整私人正文。
      （5 个新 AuditAction：PLUGIN_INSTALL/UNINSTALL/ENABLE/DISABLE/INVOKE；details 白名单含 plugin_id/version/data_categories/计数/原因，安装拒绝与解析失败也写 BLOCKED 审计；test_audit_never_contains_package_content 断言包正文与文件名不进审计）
- [x] 其他账户无法查看、启用、调用或卸载该账户上传的 SKILL，也不能借共享缓存绕过检查。
      （全部路由 scoped(account_id) 强制；跨账户查看/启停/卸载/演示 404（API+E2E 两账户隔离测试）；zip 对象存账户隔离对象库，无共享缓存）

## Verification

- [x] 建立合法声明包及脚本、可执行文件、符号链接、路径穿越、超限资源和损坏包夹具，验证安装检查失败闭锁。
      （tests/plugins/zip_builder.py 内存构造全部夹具 + test_checker.py 37 条矩阵：脚本/可执行 11 种、符号链接、穿越 5 种、越界引用 3 种、隐藏文件、损坏、超限、缺声明、非法标识）
- [x] 建立三个内置能力的端到端测试，证明默认安装状态与真实附件/文本处理结果一致。
      （E2E：三内置卡展示默认启用+启停；Documents 上传 md 真实解析结果（markdown-v1/章节/预览）；PDF 真实解析在单元层（PyMuPDF 生成真实 PDF）；humanizer 跳转聊天打开对话框走 Issue 28 既有合同）
- [x] 建立浏览器端到端测试，覆盖上传、预览、安装、启用、调用、停用、卸载、失败修复和重启恢复。
      （issue34-plugin-center.spec.ts 8 条：内置展示启停、上传预览确认安装→启停→卸载、坏包拒绝原因、缺版本拒绝、失败修复重装、两账户隔离、真实解析演示、纯键盘安装/调用/卸载）
- [x] 以两个账户验证插件清单、资源文件、授权、运行注册和审计记录完全隔离。
      （service 测试 test_account_isolation_for_all_operations + test_builtin_toggle_persists_per_account；API 测试跨账户 404；E2E 两账户隔离含 API 直调 404）
- [x] 在受支持桌面浏览器中仅用键盘完成一次本地 SKILL 安装、调用和卸载。
      （E2E 纯键盘测试：Tab/Enter 完成安装、Documents 演示调用、停用、卸载确认；配套 Dialog 焦点陷阱强化与隐藏 input 排除）

## Non-goals

- 不允许用户上传或执行 Python、JavaScript、Shell、二进制程序或其他任意代码。
- 本 Issue 不交付 MCP 受限进程和敏感操作授权，该纵向能力由 Issue 35 完成。
- 不建立开放公共插件商店、付费市场或自动更新机制。
- 不开发手机、平板、PWA 或触屏专用插件管理。

## Blocked by

- [04 — 建立电脑端设计基线与品牌资产](./04-establish-desktop-design-baseline-and-brand-assets.md)
- [12 — 交付 ChatGPT 式电脑端壳与侧栏](./12-deliver-chatgpt-desktop-shell-sidebar.md)
- [16 — 交付安全聊天附件](./16-deliver-secure-chat-attachments.md)
- [28 — 交付净室原创人味化 SKILL](./28-deliver-clean-room-humanizer-skill.md)

## Comments

- 2026-08-01：按已批准的 BridGes 改进计划发布。
- 2026-08-06：实现完成并提交。全量验证：1874 pytest（+82）、issue34 E2E 8 条全过、
  全量 E2E 228 通过（4 条失败均为既有：issue04/08 环境 flake、issue13 视频入口为
  Issue 32 遗留、issue30 朗读为并行 flake 串行通过；另修复 issue12 插件旧占位断言
  为真实页面契约+画像双空态既有断言）、mypy 230 文件 0 错误、改动区域 ruff 干净、
  npm typecheck/build 通过、openapi 同步通过。双轴 code-review 修复：AC8 审计补
  数据类别与失败（BLOCKED）事件、检查器清理死代码/死参数/空 plugin_id 必填/
  SKILL.md 大小写/frontmatter 解析防御、set_enabled 幻影返回与内置分支贯通、
  内置同名坏包不落失败卡、演示 10MB 死分支消除（API 与检查器上限同源）、失败卡
  空版本徽标、busy 禁用演示按钮、Dialog 焦点陷阱强化（隐藏 input 排除+焦点逃逸
  收回，修复键盘路径）、PDF 真实解析演示测试补 Verification 2。前端按 ui-ux-pro-max
  建议（Marketplace 卡片网格、确认删除、成功反馈）以 Issue 04 基线令牌实现。
