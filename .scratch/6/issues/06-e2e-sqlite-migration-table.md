# Issue 06：修复全新 E2E SQLite 缺少迁移会话表

Status: ready-for-agent

Type: task

Priority: P1

User stories: US-E2E-DB-01、US-RELEASE-01、US-CHAT-RELOAD-01

## 已验证现状与根因

- 在执行真实浏览器回归时，独立故障 `sqlite3.OperationalError: no such table: learning_project_migration_conversations` 会阻断完整 E2E 套件。这与 skip-link、DDG 或 Qwen 业务逻辑无关，但会让相关真实用户旅程无法可靠验收。
- `src/bridges/storage/database.py` 当前 schema 定义中确实包含 `learning_project_migration_conversations` 及其账户/项目索引；`src/bridges/chat/repository.py` 的 `get_conversation()`、`list_conversations()`、`legacy_project_name()` 会无条件查询该表。因此运行中的数据库若未执行到对应迁移，任何会话读取都可能直接 500。
- `apps/web/playwright.config.ts` 为 E2E 后端指定独立 `.e2e-data/bridges.db`。当前启动/复用路径没有建立一个可验证的不变量：“API 开始接流量前，该数据库已经初始化到代码支持的 `SCHEMA_VERSION` 且核心表存在”。新库、残留旧库、并发 web server 或复用已启动服务时可能绕过/错用初始化证据。
- 单元迁移测试能证明 `BridgesDatabase.initialize()` 在受控调用中创建表，却不能证明 Playwright 实际启动的后端使用了同一个数据库 URL、执行了初始化并完成后才报告 ready。E2E 缺表说明这条跨进程启动链路缺少反馈环。
- 不能在 repository 查询处捕获“no such table”并返回空 legacy project name；那会隐藏 schema 漂移，可能让更严重的数据/迁移问题以“正常空结果”继续运行。

### 上下文指针

- `src/bridges/storage/database.py`：`SCHEMA_VERSION`、`MIGRATIONS`、`BridgesDatabase.initialize()` 以及 `learning_project_migration_conversations` 表/索引定义。
- `src/bridges/chat/repository.py:196-234`：会话读取对迁移审计表的子查询和直接查询。
- `src/bridges/learning_projects/migration.py`：该表的写入、读取及迁移审计语义。
- `apps/web/playwright.config.ts`、`apps/web/playwright.closeout.config.ts`：E2E 数据目录、数据库 URL 与 web server 启动顺序。
- `tests/storage/test_database_migration.py`、`tests/learning_projects/test_schema_v33.py`、`tests/learning_projects/test_migration.py`：schema 与迁移测试。
- `apps/web/e2e/issue03-atomic-first-turn.spec.ts`：被缺表阻断的会话创建/读取真实旅程之一。

## What to build

1. 明确唯一启动契约：所有 API/worker/E2E 进程在构造会话 repository 和报告 readiness 前，必须对配置指向的同一个 SQLite 数据库执行 `BridgesDatabase.initialize()`，并校验返回版本等于当前 `SCHEMA_VERSION`。
2. 修正 Playwright 常规与 closeout 配置的 E2E 数据库生命周期，使后端收到规范化、唯一的绝对 SQLite URL；新 run 使用隔离目录，复用服务时必须先核对其实际数据库路径/run ID，不能误连上一次或开发环境数据库。
3. 为 E2E web server 增加 database-ready 等待条件。仅端口可连接或基础 HTTP 200 不足以开始测试；readiness 必须证明 schema 版本正确且核心契约表（至少 `conversations`、`learning_project_migration_conversations`）可查询。
4. 覆盖三种数据库状态：完全空的新文件、低于当前版本的旧 schema、已经是当前版本的数据库。初始化必须事务化、幂等；并发启动不得留下“版本已更新但表未创建”或索引缺失的半迁移状态。
5. 对“metadata 声称当前版本但核心表缺失”的损坏库执行失败关闭，输出稳定的 schema integrity 错误并阻止服务 ready。不要静默创建单表后继续，因为这类状态可能还有其他缺失对象。
6. 在 E2E teardown/下次 run 之间建立清晰策略：测试拥有的临时数据库可以按现有清理规则回收；失败时保留可诊断的 schema 版本、表清单和服务日志，但不把含账户数据的数据库当作普通 CI artifact 长期上传。
7. 添加一个最小真实跨进程 smoke：从不存在的 E2E DB 启动后端，等待 database-ready，注册账户、创建首轮会话、读取/列出会话并刷新页面，证明真实 repository 查询不会再触发缺表。

## 非目标

- 不删除 `learning_project_migration_conversations` 子查询，不改变 legacy project migration 的业务语义。
- 不在 repository 中吞掉 `OperationalError`、自动返回空值或运行临时 `CREATE TABLE IF NOT EXISTS`。
- 不降级/重置生产数据库，不删除用户已有 E2E/开发数据来规避迁移。
- 不修改聊天、skip-link、DDG 或 Qwen 功能实现。
- 不要求每个 Playwright worker 拥有独立 API 数据库；只要求同一测试 run 的生命周期和并发语义明确、可重复。

## Acceptance criteria

- [ ] 从完全不存在的 E2E SQLite 文件启动时，API 报告 ready 前数据库已达到当前 `SCHEMA_VERSION`，`learning_project_migration_conversations` 表和索引存在。
- [ ] 从早于该表引入版本的代表性旧数据库启动时，迁移完成且原有账户/会话数据保留；会话列表和详情可读。
- [ ] 对当前版本数据库重复初始化至少三次无额外数据变更、无重复对象错误，schema 保持完整。
- [ ] 两个进程/线程同时尝试初始化同一测试数据库时，要么安全串行并最终 ready，要么一个得到稳定可重试的初始化错误；不得出现半迁移和缺表后继续服务。
- [ ] 数据库 metadata 已是当前版本但缺少核心表/索引时，readiness 失败并返回稳定 `database_schema_integrity`（或等价）错误，服务不接受会话流量。
- [ ] 常规 Playwright 与 closeout Playwright 配置都使用本 run 的规范化绝对数据库路径；测试能证明后端报告的路径指纹/run ID 与期望一致，但不公开完整敏感路径。
- [ ] web server 端口提前开放但 schema 尚未 ready 时，Playwright 不开始用户测试；初始化失败时启动明确失败，而不是在首个会话请求中报 500。
- [ ] 新库真实 E2E smoke 可完成注册、首轮创建、会话列表、会话详情和页面刷新，日志中不存在 `no such table`。
- [ ] 现有 `tests/storage/test_database_migration.py`、schema v33/当前版和 learning-project migration 测试全部通过。
- [ ] 诊断和 CI artifact 不包含账户密码、邮件正文、Qwen Key 或完整数据库转储。

## Test plan

1. 在 storage 测试中从空路径创建数据库，断言版本、表、索引和关键外部查询；重复 `initialize()` 验证幂等。
2. 使用仓库现有旧 schema fixture（或最小受控旧版本建库）迁移到当前版本，预置账户/会话并验证数据不丢失。
3. 构造 metadata 为当前版本但删除/遗漏迁移会话表的损坏 fixture，断言初始化/readiness 失败关闭，且 repository 不被构造为可服务状态。
4. 添加并发初始化测试，用 barrier 同时启动两个 initializer，最终检查 schema 完整性和 metadata 单一版本。
5. 为 Playwright webServer 配置添加契约测试/脚本测试，验证数据库 URL 为本 run 的绝对路径、后端 database-ready 后才开始前端测试。
6. 运行不复用既有 server 的真实浏览器 smoke：清空本测试拥有的隔离 run 目录，启动完整栈并执行注册、首轮消息、列表、详情、刷新。

建议回归命令：

```powershell
python -m pytest tests/storage/test_database_migration.py tests/learning_projects/test_schema_v33.py tests/learning_projects/test_migration.py tests/chat/test_chat_service.py -q -p no:cacheprovider --basetemp=$env:TEMP\bridges-issue06
cd apps/web
npx playwright test e2e/issue03-atomic-first-turn.spec.ts --project=chromium
```

执行 E2E 时必须关闭“复用任意已运行开发 server”的隐式路径，或显式验证复用服务属于本 run；最终命令可按仓库脚本约定调整。

## Observability & rollback

- 启动日志记录数据库 path 指纹、目标/实际 schema version、migration start/end、耗时、完整性校验结果和 run ID；不记录完整路径中的用户名、连接凭据或表数据。
- readiness 暴露 `database_schema_ready`、schema version 与稳定错误码，不返回表内容。检测到 `no such table` 时记录缺失对象名和版本，不输出 SQL 参数。
- E2E 失败可保留脱敏的 `PRAGMA user_version`/metadata 版本与 `sqlite_master` 对象名称清单，默认不上传完整 DB。
- 如果新的 readiness 造成启动兼容性问题，可以回滚 Playwright 等待接入，但必须保留 API 在 repository 构造前初始化及完整性失败关闭；不得回滚为吞掉缺表或自动清空数据库。

## Blocked by

无。

## Comments

- 2026-08-13：完整 E2E 回归已被 `no such table: learning_project_migration_conversations` 独立阻断；该问题与本轮其余功能缺陷解耦，可并行修复。
- 2026-08-13：目标是让迁移在真实 E2E 启动链路中可证明完成，而不是在会话查询处为缺表提供静默兼容。
