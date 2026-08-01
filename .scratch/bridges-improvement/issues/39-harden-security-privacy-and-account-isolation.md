# 39 — 加固安全、隐私与账户隔离
Status: ready-for-agent
Blocked by: 09, 20, 27, 35, 37, 38
Covered requirements: ACCOUNT-01, EXT-01, EXT-03, DEPLOY-03, MODEL-03, IMP-03, BONUS-02, SCORE-02, SCORE-03, UI-05, DESKTOP-01
ADRs: [0010](../../../docs/adr/0010-declarative-skills-and-permissioned-mcp.md), [0012](../../../docs/adr/0012-source-environment-and-container-deployment.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0018](../../../docs/adr/0018-device-account-switching-and-reauthentication.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

围绕已经贯通的正式产品路径建立可重复攻击矩阵并关闭高风险缺陷。范围覆盖 Cookie 与会话、CSRF、跨账户缓存和后台串号、上传与解包路径、日志与错误脱敏、Qwen/搜索最小云披露、备份恢复边界，以及 SKILL/MCP 的权限提升与秘密读取。加固必须贯穿桌面页面、API、持久化、后台任务、对象下载、模型调用和扩展进程；失败时默认拒绝并给用户可恢复说明，而不是只隐藏前端入口或记录告警后继续执行。

## Acceptance criteria

- [ ] 会话 Cookie 根据实际传输环境设置正确的 HttpOnly、SameSite、路径、生命周期和安全属性；登出、撤销、过期及无效 Cookie 会被清除且不会形成登录重定向循环。
- [ ] 所有改变账户、数据、权限、插件、提醒和资产状态的浏览器请求具有有效 CSRF 防护和来源校验，跨站表单与脚本请求不能成功。
- [ ] 切换账户会停止旧流式响应、播放、上传和后台订阅，清空页面缓存、搜索建议、选择状态与本地临时数据，并要求敏感操作重新认证。
- [ ] 所有上传、归档解包、SKILL 安装、备份恢复和资产路径都拒绝路径穿越、绝对路径、符号链接逃逸、名称混淆和越界覆盖。
- [ ] 日志、遥测、错误响应和审计不包含密码、百炼 Key、SMTP 授权码、会话令牌、加密密钥、完整私人正文或未经必要处理的模型请求。
- [ ] Qwen 仅接收当前任务授权的最小会话、画像和材料切片；DuckDuckGo 只接收本地去身份关键词，每次披露有可审计类别和授权快照。
- [ ] 每个后台任务、调度器作业、媒体任务、索引任务和缓存条目都绑定稳定账户与对象范围，账户撤权或删除后不能继续产出可见结果。
- [ ] SKILL 和 MCP 无法读取秘密、未授权文件、其他账户对象或未声明网络域名，也无法通过子进程、环境继承、链接、重定向或提示注入提升权限。
- [ ] 直接对象标识、下载地址、对话标识、项目标识、任务标识和审计标识均执行服务端账户授权，猜测标识不能读取或推断其他账户状态。
- [ ] 所有发现的高危和中危用例均有回归测试、修复证据和剩余风险说明；发布门对未关闭高风险项失败闭锁。

## Verification

- [ ] 建立 Cookie 固定、失效会话、CSRF、登录重定向、退出失败和账户切换并发攻击测试。
- [ ] 使用两个以上账户并发执行聊天、搜索、上传、媒体、提醒、画像反馈、导出和插件调用，验证页面、缓存、任务和结果不串号。
- [ ] 使用恶意文件名、归档、符号链接、备份和 SKILL/MCP 夹具执行路径逃逸与覆盖测试。
- [ ] 向模型、搜索、日志和审计注入秘密金丝雀，自动扫描所有出站请求及落盘记录，确认未发生泄露。
- [ ] 执行 MCP 网络、文件、命令、子进程和敏感操作越权测试，并验证权限撤回即时生效。
- [ ] 形成可复现安全报告，列出攻击步骤、预期拒绝、实际证据、修复回归及仍接受的明确风险。

## Non-goals

- 不声称替代独立第三方渗透测试、形式化验证或多机云平台安全审计。
- 不为未纳入首版的 PostgreSQL、Redis、S3、Kubernetes、移动端或 PWA 建立攻击矩阵。
- 不通过降低功能、关闭全部扩展或保存更多敏感正文来换取表面通过。

## Blocked by

- [09 — 交付设备账户切换与重新认证](./09-deliver-device-account-switching-and-reauth.md)
- [20 — 交付分层检索与引用](./20-deliver-layered-retrieval-and-citations.md)
- [27 — 交付画像切片披露与反馈闭环](./27-deliver-profile-slices-disclosure-and-feedback-loop.md)
- [35 — 交付显式授权 MCP 插件管理](./35-deliver-permissioned-mcp-plugin-management.md)
- [37 — 交付导出、删除、备份与恢复](./37-deliver-export-delete-backup-restore.md)
- [38 — 完成电脑端视觉、可访问性与页面状态](./38-complete-desktop-visual-accessibility-and-page-states.md)

## Comments

- 2026-08-01：按已批准的 BridGes 改进计划发布。
