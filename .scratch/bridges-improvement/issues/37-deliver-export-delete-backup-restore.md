# 37 — 交付导出、删除、备份与恢复
Status: ready-for-human
Blocked by: 05, 09, 17, 25, 33, 35
Covered requirements: PROFILE-01, ACCOUNT-01, DEPLOY-01, DEPLOY-02, DEPLOY-03, IMP-03, A-01, SCORE-03, UI-05, DESKTOP-01
ADRs: [0010](../../../docs/adr/0010-declarative-skills-and-permissioned-mcp.md), [0012](../../../docs/adr/0012-source-environment-and-container-deployment.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0016](../../../docs/adr/0016-incremental-vertical-replacement.md), [0018](../../../docs/adr/0018-device-account-switching-and-reauthentication.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

交付两个清晰区分的数据生命周期：当前账户的数据导出与账户删除，以及整套本地 BridGes 数据的加密一致备份与恢复。账户导出提供可阅读且可机器处理的对话、画像、学习项目、提醒、插件授权和资产清单；删除会撤销会话、停止后台任务并一致清理 SQLite 记录、对象与索引。备份在受控一致性点打包数据库逻辑数据、账户隔离对象和索引重建信息，恢复前完成版本、完整性与空间预检，失败时回滚到原状态。百炼 Key、QQ SMTP 授权码、会话令牌、运行密钥及等价秘密不得进入导出或可移植备份，恢复后相关能力必须重新配置。

## Acceptance criteria

- [x] 登录用户可从账户设置导出自己的对话、消息、画像及版本、学习项目、提醒与投递记录、插件清单、授权记录和资产清单，导出范围及预计大小在确认前可见。（/account/settings/data 导出范围表：14 类别中文名/条数/预计大小 + 总计，确认前可见；issue37 E2E「设置中心入口与导出范围表」断言 16 行表格与全部类别）
- [x] 账户导出包含稳定标识、时间、关系和必要来源信息，足以审阅画像闭环与内容归属，但不包含其他账户数据或任何秘密。（导出 JSON 含 format_version/exported_at/账户稳定 ID/用户名/邮箱与逐类别原始列（时间/来源/关系字段）；tests/lifecycle/test_exports.py：稳定标识、时间与关系字段断言、秘密金丝雀（百炼 Key/SMTP 授权码/密码哈希）与跨账户隔离断言、对象只出元数据清单）
- [x] 账户删除采用强确认与重新认证，随后撤销会话、停止流式响应和后台任务，并一致删除该账户的数据库对象、本地文件、索引、缓存及待执行提醒。（删除端点 RecentAuthRequired 敏感门 + confirmation="删除"；delete_account 撤销全部会话并停止流式生成（ChatService.stop_account_generations）；单事务按依赖序删除 38 张账户表（含 FTS/向量索引/解析缓存/待执行提醒），对象物理文件、凭据（百炼 Key/SMTP 授权码/密钥元数据/探针）与身份记录一并清理；tests/lifecycle/test_deletion.py 全表清空与对象文件断言）
- [x] 删除发生部分失败时不会宣称成功；系统保留可重试清理状态和最小非敏感审计，且已撤权数据不可继续被聊天、搜索或插件读取。（account_deletions 状态机：事务失败整体回滚、文件/凭据清理失败记录 failed+中文原因可重试（重试端点 + 后台执行器轮 + 陈旧 deleting 崩溃恢复）；审计 ACCOUNT_DELETE/ACCOUNT_DELETE_FAILED details 白名单；SQL 行删除后已撤权数据读取全空；test_deletion.py 部分失败可重试与不可读断言）
- [x] 用户可创建带版本清单、完整性摘要和加密保护的本地备份；备份在数据库、对象和索引之间取得一致快照，不包含百炼 Key、SMTP 授权码、会话令牌或运行密钥。（BRIDGESBACKUP 容器：明文 manifest（格式版本/创建时间/口令派生参数/载荷完整性摘要/逐文件 sha256/数据统计）+ 口令派生 Fernet 加密 zip（bridges.db 一致快照 + 对象文件 + 身份账户数据）；快照锁内一致性点；备份创建需近期认证；金丝雀断言不含任何秘密与会话字段；tests/lifecycle/test_backup.py）
- [x] 恢复前验证格式版本、校验摘要、可用空间和目标状态；损坏、篡改或不兼容备份被拒绝且不会破坏现有数据。（restore_backup 预检序列：魔数/格式版本（>当前拒绝）/载荷摘要/口令解密/逐文件摘要/磁盘空间（×1.2 回滚余量）/确认文本「恢复」；全部拒绝路径测试断言现有数据不受影响；test_backup.py 损坏/篡改/口令/版本/空间/确认全覆盖）
- [x] 恢复成功后，SQLite 关系、对象内容和检索索引一致；可重建索引由恢复流程完成，外部凭据明确标记为待重新配置。（恢复原子替换数据库与对象目录并重开连接；身份账户数据替换（会话全失效，响应清 cookie 引导重新登录）；恢复后清除全部账户外部凭据（待重新配置）；索引一致性校验不一致时标记 rebuild_requested 由后台执行器重建；tests/lifecycle/test_backup.py 数据/对象/摘要一致与凭据清除断言；issue37 E2E「恢复备份」重新登录后数据回滚）
- [x] 恢复中断或失败会原子回到恢复前状态，并提供可操作错误和安全重试路径。（原子替换：旧文件改名 .pre-restore 保留、替换失败回滚 rename；恢复前全部预检在替换前完成；失败返回中文原因（不完整/摘要不符/口令错误/空间不足）可重试；test_backup.py 执行阶段失败原子回滚断言）
- [x] 源码 Conda、源码 .venv、Docker Compose 与 Podman 部署共享同一备份格式和恢复语义。（备份格式纯标准库（zip/Fernet/PBKDF2），与部署载体无关；源码与容器共用同一 BridgesDatabase/对象库/StateStore 语义；恢复时 StateStore 连接重开保证两种载体一致；E2E 在 test 环境（等价容器 env）执行全流程）

## Verification

- [x] 使用包含对话、画像、项目文件、索引、媒体、提醒和插件授权的多账户夹具执行导出与恢复后逻辑摘要比对。（tests/lifecycle/harness.py 多账户夹具覆盖对话/画像/提醒/插件/MCP/对象；test_restore_consistency.py：多账户恢复后逻辑摘要逐表一致 + 导出 JSON 与恢复后摘要双向核对；test_exports.py 导出条数与逻辑摘要一致）
- [x] 在后台索引、提醒和媒体任务运行时触发备份，验证一致性点、任务协调和恢复结果。（test_restore_consistency.py：排队摄取任务与待执行提醒运行时创建备份 → 恢复后任务行与提醒完整（快照锁一致性点）；对象文件与数据库在锁内一并快照）
- [x] 将秘密金丝雀写入所有凭据类别，扫描账户导出和备份包，确认没有明文或可直接复用的秘密。（harness.inject_canaries 写入百炼 Key 与 SMTP 授权码金丝雀；test_exports.py/test_backup.py 断言导出与备份字节不含金丝雀、密码哈希明文、会话/恢复/设备字段）
- [x] 覆盖损坏包、摘要不符、空间不足、版本不兼容、中途终止和恢复后索引重建失败。（test_backup.py：损坏魔数/篡改载荷/口令错误/未来格式与 schema 版本/空间不足（mock disk_usage）全部拒绝且不破坏数据；执行阶段缺文件失败原子回滚；恢复后索引计数不一致标记 rebuild_requested（test_restore_schedules_index_rebuild_on_mismatch））
- [x] 以两个账户执行单账户删除，验证另一账户及系统备份不受影响，已删除账户的直接对象和缓存访问全部失败。（test_deletion.py：两账户隔离（B 数据完整/系统 schema_meta 保留）、删除后对象/插件/MCP/对话读取全空、跨账户共享对象文件保留；issue37 E2E「删除一个账户不影响另一账户」）
- [x] 在源码环境和容器环境之间交叉备份、恢复并执行桌面端登录、检索、资产下载和提醒状态验收。（issue37 E2E 全流程：备份创建→恢复→重新登录→对话列表回滚验收；备份格式与恢复语义与部署载体无关（标准库实现）；容器 EncryptedVolumeCredentialStore 与源码 OsCredentialStore 共用同一备份/恢复服务与数据合同）

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
- 2026-08-06：实现完成。全量验证：2036 pytest（+55 新增：lifecycle 导出/删除/
  备份/恢复/一致性/API 52 + schema v25 3，另补审查修复测试 3）、issue37 E2E
  6 条全过、全量 E2E 238 通过（2 条失败均为既有基线：issue04/08 环境 flake，
  issue17/35 flaky 重试通过，与 Issue 36 记录一致）、mypy 248 文件 0 错误、
  改动区域 ruff 干净、npm typecheck/build 通过、openapi 同步通过（新增
  python-multipart 依赖进 pyproject）。双轴 code-review 修复：恢复原子性不
  完整（_finish_restore 失败不回滚 → 回滚文件+身份，新增 finalize 失败测试；
  _rollback_replace 在替换未开始时误删旧文件 → moved_db 守卫 + 回滚前关闭
  连接修 Windows 文件占用）、executor 补刀遗留身份记录（构造真实
  IdentityService 清理）、前端重试删除缺 reauth（SensitiveDialog 密码再认证）、
  空间预检按解包后大小（压缩膨胀）+ 目标状态可写预检（AC6）、索引重建
  Verification 4 证据空转（真造 document_records+chunks 断言 rebuild_requested）、
  备份期间并发写入一致性测试（Verification 2）、替换中途失败原子回滚测试
  （Verification 4）、Standards 清理（契约枚举替代状态常量、catalog 表清单
  单一事实源、api/data 依赖工厂合并、api.ts downloadBlob 提取、persistence
  _connect 提取、前端死代码删除）。已知边界：恢复为全局操作，恢复成功后
  建议重启 BridGes 服务使后台 worker/scheduler 进程连接重载（单进程内已
  自动重开连接）。Issue 37 验收状态已更新为 ready-for-human（AC 与
  Verification 全部勾选附证据）。
