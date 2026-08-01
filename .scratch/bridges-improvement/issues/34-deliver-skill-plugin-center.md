# 34 — 交付 SKILL 插件中心
Status: ready-for-agent
Blocked by: 04, 12, 16, 28
Covered requirements: EXT-01, EXT-02, EXT-03, IMP-02, IMP-03, B-01, SCORE-02, SCORE-03, UI-05, DESKTOP-01
ADRs: [0010](../../../docs/adr/0010-declarative-skills-and-permissioned-mcp.md), [0011](../../../docs/adr/0011-clean-room-humanizer-and-license-boundary.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

交付完整的电脑端插件中心，并先把内置及用户上传的声明式 SKILL 做成可运行的端到端能力。页面清楚区分内置、已安装、已停用和安装失败状态；PDF、Documents 与原创 bridges-humanizer 随应用发布并默认安装，展示固定版本、来源、能力和授权状态。用户可上传只含 SKILL.md、静态参考资料、模板及资源的声明式包，在安装前完成安全检查和内容预览，并可按账户启用、停用和卸载用户包。插件卡片必须对应真实运行注册与审计状态，不能只是目录展示或无 handler 按钮。

## Acceptance criteria

- [ ] 插件中心完整呈现内置、已安装、已停用和失败四类状态，并为加载、空、错误、权限不足、安装中、成功及恢复提供成品页面状态。
- [ ] PDF、Documents 和 bridges-humanizer 以只读内置包随应用安装，展示固定版本、能力、来源与授权；账户可启停，但不能篡改或卸载内置包内容。
- [ ] 三个默认能力均有可演示真实路径：PDF 与 Documents 能处理受支持附件，bridges-humanizer 能按其既有合同生成结果，而非返回固定样例。
- [ ] 用户上传包只允许声明式 SKILL.md、静态参考、模板和资源；脚本、可执行文件、符号链接、路径穿越、越界引用和不受支持文件被拒绝并说明具体原因。
- [ ] 安装确认前展示包名、固定版本、内容清单、声明能力、来源和将接收的数据类别；用户取消时不留下半安装状态。
- [ ] 用户包可按当前账户启用、停用和卸载；状态、固定版本及审计记录在刷新、重登和应用重启后保持一致。
- [ ] 安装失败进入可恢复失败状态，不污染运行注册表；修正包后可重新检查和安装。
- [ ] 每次安装、启停、卸载和调用都记录账户、版本、授权数据类别、时间与结果，但不复制秘密或完整私人正文。
- [ ] 其他账户无法查看、启用、调用或卸载该账户上传的 SKILL，也不能借共享缓存绕过检查。

## Verification

- [ ] 建立合法声明包及脚本、可执行文件、符号链接、路径穿越、超限资源和损坏包夹具，验证安装检查失败闭锁。
- [ ] 建立三个内置能力的端到端测试，证明默认安装状态与真实附件/文本处理结果一致。
- [ ] 建立浏览器端到端测试，覆盖上传、预览、安装、启用、调用、停用、卸载、失败修复和重启恢复。
- [ ] 以两个账户验证插件清单、资源文件、授权、运行注册和审计记录完全隔离。
- [ ] 在受支持桌面浏览器中仅用键盘完成一次本地 SKILL 安装、调用和卸载。

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
