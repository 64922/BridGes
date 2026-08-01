# 41 — 退役旧实现并通过发布门
Status: ready-for-agent
Blocked by: 01, 02, 06, 24, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40
Covered requirements: UI-01, UI-02, UI-03, UI-04, UI-05, CHAT-05, CHAT-07, CHAT-08, CHAT-10, TASK-01, EXT-01, EXT-02, EXT-03, ACCOUNT-01, DEPLOY-01, DEPLOY-02, DEPLOY-03, MODEL-01, MODEL-02, MODEL-03, IMP-01, IMP-02, IMP-03, A-01, A-02, B-01, BONUS-01, BONUS-02, BONUS-03, SCORE-01, SCORE-02, SCORE-03, DESKTOP-01
ADRs: [0004](../../../docs/adr/0004-per-account-qq-smtp-reminders.md), [0006](../../../docs/adr/0006-one-model-snapshot-per-capability.md), [0007](../../../docs/adr/0007-wan-video-generation-exception.md), [0009](../../../docs/adr/0009-fixed-model-and-provider-matrix.md), [0010](../../../docs/adr/0010-declarative-skills-and-permissioned-mcp.md), [0011](../../../docs/adr/0011-clean-room-humanizer-and-license-boundary.md), [0012](../../../docs/adr/0012-source-environment-and-container-deployment.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0019](../../../docs/adr/0019-limited-reminder-catch-up.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

在所有替代纵向切片通过后，收缩并删除旧 Science Companion 普通用户工作台、空壳页面、硬编码演示状态、失效路由、无 handler 操作和生产 Stub，只保留经 BridGes 正式路径实际使用且通过测试的工程骨架。随后以同一锁定源码分别完成 Conda、.venv、Docker Compose 和 Podman 的电脑端发布验收：统一执行 BridGes start，自动迁移、启动 Web/API/后台执行器/调度器、提供健康状态并可干净停止；用户不创建 .env，账户秘密只从受保护设置进入。更新当前 README、安装、运行、备份、安全、能力矩阵和故障排查文档，但不修改旧 .scratch/science-companion-plan Wayfinder 产物。

## Acceptance criteria

- [ ] 普通用户正式导航和路由只保留 BridGes 聊天优先产品面；旧项目制教学工作台、画像/设置空壳、旧健康展示入口和无替代价值的旧组件已删除或不可达。
- [ ] 正式页面不存在 Science Companion 品牌、旧术语、占位文案、固定 T00x 状态、示例任务、无 handler 按钮或指向已删除页面的链接。
- [ ] 生产配置不注册 Stub、固定样例或静默模型降级；未配置密钥、网络失败或供应商不可用时，对应能力明确停用并显示真实错误。
- [ ] Qwen、Embedding、ASR、TTS、图片、Wan、DuckDuckGo、arXiv、QQ SMTP、SKILL 与 MCP 都通过真实探测或明确的不可用合同，用户不能切换固定模型。
- [ ] 下载源码后，Conda 与 .venv 两条路径均可按锁定依赖安装并从项目目录执行 BridGes start，不要求最终用户必须安装 Conda。
- [ ] Docker Compose 与 Podman 均从同一源码和锁定依赖构建，使用同一迁移、数据目录、健康检查、后台任务和停止语义。
- [ ] 四种部署均不要求创建 .env；首次启动生成非账户运行秘密，百炼 Key 与 QQ SMTP 授权码只在登录后的受保护设置中配置。
- [ ] BridGes start 能编排 Web、API、后台执行器和提醒调度器，重复启动不会产生重复任务，停止后无僵尸进程或损坏中的写入。
- [ ] 1280×720、1440×900、1920×1080 电脑端黄金路径覆盖注册登录、密钥配置、聊天、两种模式、知识库、学习项目、提醒、插件、画像、搜索、媒体、导出和恢复。
- [ ] 安全攻击矩阵与 A/B 科学评测达到锁定发布阈值；任何高风险安全失败、事实门失败、账户串号或生产 Stub 都阻止发布。
- [ ] 当前 README 和运行文档准确描述 BridGes start、Conda/.venv、Docker/Podman、零 .env、账户秘密、数据备份、能力不可用和电脑端支持边界。
- [ ] 旧 .scratch/science-companion-plan、旧 tickets.md 及其研究原型保持原样，只作为只读历史，不被改写成 BridGes 文档。

## Verification

- [ ] 在四个干净部署环境分别从锁定源码完成安装、首次启动、迁移、健康检查、黄金路径、停止和第二次启动。
- [ ] 在开发使用的 Conda agent 环境运行完整测试；另在全新 .venv 中重复关键合同，证明最终用户不依赖 Conda 专有行为。
- [ ] 运行后端、契约、桌面 Playwright、安全、账户隔离、备份恢复和可复现评测套件，并保存发布候选报告。
- [ ] 静态扫描正式运行代码和构建产物，确认不存在空壳文案、硬编码成功、生产 Stub、旧普通用户路由和可用的旧品牌入口。
- [ ] 断开网络、撤销百炼 Key、使 SMTP 授权失效并令 MCP 崩溃，验证页面显示真实停用/恢复状态而非假成功。
- [ ] 从四种部署各完成一次真实联网能力抽样，并核对固定模型、供应商、授权、披露审计和账户隔离。
- [ ] 检查版本控制差异，确认旧 Wayfinder 文档未被修改，新增 README 与发布文档链接均有效。

## Non-goals

- 不在发布收口阶段新增未规划产品功能、模型、供应商或插件市场。
- 不开发 Windows 原生安装包、云托管、多机高可用、手机、平板、PWA 或原生移动应用。
- 不重写、删除或“更新”旧 .scratch/science-companion-plan 与 tickets.md 历史产物。
- 不以关闭测试、保留假数据或跳过真实联网探测换取发布通过。

## Blocked by

- [01 — 轮换泄露密钥并冻结旧实现](./01-rotate-compromised-key-and-freeze-legacy.md)
- [02 — 稳定隔离测试基线](./02-stabilize-isolated-test-baseline.md)
- [06 — 交付统一源码与容器运行时](./06-deliver-unified-source-and-container-runtime.md)
- [24 — 交付统一电脑端搜索](./24-deliver-unified-desktop-search.md)
- [29 — 交付生涯规划助手](./29-deliver-career-planning-assistant.md)
- [30 — 交付听写与单条回答朗读](./30-deliver-asr-dictation-and-tts-readaloud.md)
- [31 — 交付图片生成与编辑](./31-deliver-image-generation-and-editing.md)
- [32 — 交付视频生成](./32-deliver-video-generation.md)
- [33 — 交付 QQ SMTP 任务提醒](./33-deliver-qq-smtp-reminders.md)
- [34 — 交付 SKILL 插件中心](./34-deliver-skill-plugin-center.md)
- [35 — 交付显式授权 MCP 插件管理](./35-deliver-permissioned-mcp-plugin-management.md)
- [36 — 集成聊天工具、学习项目与插件选择](./36-integrate-chat-tools-project-and-plugin-selectors.md)
- [37 — 交付导出、删除、备份与恢复](./37-deliver-export-delete-backup-restore.md)
- [38 — 完成电脑端视觉、可访问性与页面状态](./38-complete-desktop-visual-accessibility-and-page-states.md)
- [39 — 加固安全、隐私与账户隔离](./39-harden-security-privacy-and-account-isolation.md)
- [40 — 建立可复现 A/B 科学评测](./40-build-reproducible-ab-science-evaluation.md)

## Comments

- 2026-08-01：按已批准的 BridGes 改进计划发布。
