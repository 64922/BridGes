# 37 — 交付导出、删除、备份与恢复
Status: ready-for-agent
Blocked by: 05, 09, 17, 25, 33, 35
Covered requirements: PROFILE-01, ACCOUNT-01, DEPLOY-01, DEPLOY-02, DEPLOY-03, IMP-03, A-01, SCORE-03, UI-05, DESKTOP-01
ADRs: [0010](../../../docs/adr/0010-declarative-skills-and-permissioned-mcp.md), [0012](../../../docs/adr/0012-source-environment-and-container-deployment.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0018](../../../docs/adr/0018-device-account-switching-and-reauthentication.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

交付两个清晰区分的数据生命周期：当前账户的数据导出与账户删除，以及整套本地 BridGes 数据的加密一致备份与恢复。账户导出提供可阅读且可机器处理的对话、画像、学习项目、提醒、插件授权和资产清单；删除会撤销会话、停止后台任务并一致清理 SQLite 记录、对象与索引。备份在受控一致性点打包数据库逻辑数据、账户隔离对象和索引重建信息，恢复前完成版本、完整性与空间预检，失败时回滚到原状态。百炼 Key、QQ SMTP 授权码、会话令牌、运行密钥及等价秘密不得进入导出或可移植备份，恢复后相关能力必须重新配置。

## Acceptance criteria

- [ ] 登录用户可从账户设置导出自己的对话、消息、画像及版本、学习项目、提醒与投递记录、插件清单、授权记录和资产清单，导出范围及预计大小在确认前可见。
- [ ] 账户导出包含稳定标识、时间、关系和必要来源信息，足以审阅画像闭环与内容归属，但不包含其他账户数据或任何秘密。
- [ ] 账户删除采用强确认与重新认证，随后撤销会话、停止流式响应和后台任务，并一致删除该账户的数据库对象、本地文件、索引、缓存及待执行提醒。
- [ ] 删除发生部分失败时不会宣称成功；系统保留可重试清理状态和最小非敏感审计，且已撤权数据不可继续被聊天、搜索或插件读取。
- [ ] 用户可创建带版本清单、完整性摘要和加密保护的本地备份；备份在数据库、对象和索引之间取得一致快照，不包含百炼 Key、SMTP 授权码、会话令牌或运行密钥。
- [ ] 恢复前验证格式版本、校验摘要、可用空间和目标状态；损坏、篡改或不兼容备份被拒绝且不会破坏现有数据。
- [ ] 恢复成功后，SQLite 关系、对象内容和检索索引一致；可重建索引由恢复流程完成，外部凭据明确标记为待重新配置。
- [ ] 恢复中断或失败会原子回到恢复前状态，并提供可操作错误和安全重试路径。
- [ ] 源码 Conda、源码 .venv、Docker Compose 与 Podman 部署共享同一备份格式和恢复语义。

## Verification

- [ ] 使用包含对话、画像、项目文件、索引、媒体、提醒和插件授权的多账户夹具执行导出与恢复后逻辑摘要比对。
- [ ] 在后台索引、提醒和媒体任务运行时触发备份，验证一致性点、任务协调和恢复结果。
- [ ] 将秘密金丝雀写入所有凭据类别，扫描账户导出和备份包，确认没有明文或可直接复用的秘密。
- [ ] 覆盖损坏包、摘要不符、空间不足、版本不兼容、中途终止和恢复后索引重建失败。
- [ ] 以两个账户执行单账户删除，验证另一账户及系统备份不受影响，已删除账户的直接对象和缓存访问全部失败。
- [ ] 在源码环境和容器环境之间交叉备份、恢复并执行桌面端登录、检索、资产下载和提醒状态验收。

## Non-goals

- 不提供云备份托管、跨设备自动同步、增量远程备份或多机灾备。
- 不把百炼 Key、QQ SMTP 授权码、会话令牌或内部运行密钥包装后继续导出。
- 不承诺恢复旧 Science Companion 数据库；本 effort 使用全新的 BridGes 数据合同。
- 不开发手机、平板、PWA 或原生备份客户端。

## Blocked by

- [05 — 建立干净 SQLite 与对象存储](./05-build-clean-sqlite-and-object-storage.md)
- [09 — 交付设备账户切换与重新认证](./09-deliver-device-account-switching-and-reauth.md)
- [17 — 交付文档摄取与版本化索引](./17-deliver-document-ingestion-and-versioned-index.md)
- [25 — 交付画像中心与静态头像](./25-deliver-profile-center-and-static-avatar.md)
- [33 — 交付 QQ SMTP 任务提醒](./33-deliver-qq-smtp-reminders.md)
- [35 — 交付显式授权 MCP 插件管理](./35-deliver-permissioned-mcp-plugin-management.md)

## Comments

- 2026-08-01：按已批准的 BridGes 改进计划发布。
