# 01 — 恢复旧数据库的原子画像并阻止缺表后继续服务

**What to build:** 用户升级现有数据后，用户画像页面能够读取长期信息，安全恢复有来源的历史四维记录，支持逐条修改与删除；版本号与真实数据库结构不一致时能够被识别并按明确路径修复。

**Blocked by:** None — 可立即开始。

**Status:** ready-for-human

## 问题与证据

用户画像页面持续返回 500，与聊天轮数无关。实际运行库版本标记为 58，但原子画像条目表和迁移记录表不存在。调用真实画像仓库列表方法得到缺表异常。建表定义属于第 51 版迁移，现有升级逻辑不会对已标记为高版本的库重放旧迁移，启动完整性清单也漏掉画像结构。旧四维记录中有 2 条活动记录。

已确认的是结构漂移、读取失败及启动校验遗漏，不是已确认某次合并或人工操作造成了漂移。任务不能通过删除数据库、降低版本号、重放全部迁移或返回空列表掩盖错误来完成。

## 任务内容

1. 确认应用配置指向的权威数据库，输出不含正文与凭据的版本、必要表和索引检查结果。对运行库只读检查，制作一致性副本用于复现；不要把仓库内旧库当作修复对象。
2. 建立带“最新版本标记但缺少画像对象”的回归夹具，同时覆盖正常最新库、正常旧库和空库。通过真实仓库及 API 层复现用户页面的 500 路径。
3. 在当前主分支最新版本之上添加修复迁移。补齐原子画像必要表与索引，已有合法对象不得被清空；同名对象结构不兼容时明确失败，不能仅用存在性检查误报正常。
4. 将画像必要结构纳入启动完整性校验，并明确执行顺序：可安全修复的已知漂移先通过迁移恢复，恢复失败或未知不兼容结构阻止进入健康状态。保留稳定错误码及脱敏诊断信息。
5. 在事务与备份保护下，复用既有原子化迁移能力核对历史四维记录。逐条检查来源、状态、用户编辑优先和删除墓碑，符合条件才迁入；无法迁入的记录说明原因，不猜造画像。迁移按账户隔离并支持重复执行。
6. 验证自动提取能继续镜像到原子画像。已耗尽的旧提取任务仅在确认缺表是原因后，使用已有受控重试方式处理，不自动重跑全量聊天。页面应正确区分空画像、服务失败与成功列表。

## 验收标准

- [x] 缺表夹具修复前通过真实读取路径失败，修复后画像 API 返回 200。
- [x] 正常旧库、最新库、空库均通过对应迁移路径；重复启动和重复迁移不重复创建条目。
- [x] 缺失的表及索引补齐；不兼容对象或迁移失败有明确错误，不对外报告健康。
- [x] 历史记录逐条对账，说明迁入、跳过或拒绝原因；用户编辑不被覆盖，删除信息不复活。
- [x] 页面列表、行内编辑、删除和跨账户不可访问通过验证；新提取结果可见（接口与组件级：`tests/profiles` + `AtomicProfileCenter.test.tsx`；浏览器真实点击回归在 07）。
- [x] 迁移前备份可恢复；注入迁移失败时事务回滚，现有聊天、知识库与附件数据不丢失（回滚与备份在真实库副本上演练，见「失败回滚与备份」）。
- [x] 提供真实库副本演练结果；实际用户库的应用和备份核验交由 07 统一执行。

## 范围与协作

本票拥有数据库修复迁移和画像完整性检查，不重写画像提取算法，不开展全库迁移框架重构。涉及共享迁移版本时由本票执行者协调，合入前重新检查主分支版本号。

## 执行与验收记录

### 基线版本与环境

- 分支点 `main` = `6939da3`（== `origin/main`，树 `de7e392f`）；与上一票合并后的 `ace21d4` 相比只多 `.scratch/1/` 文档，代码树相同。
- 实现分支 `codex/01-profile-schema-recovery`，工作树 `.worktrees/01-profile-schema-recovery`；Python 用 conda `agent`。
- **从工作树运行必须带 `PYTHONPATH=src`**：环境里的 `bridges` 指向主仓 `src`，漏掉会跑到主仓代码上。

### 权威库只读核查（任务 1）

- 权威库按应用配置解析：`%LOCALAPPDATA%\BridGes\config.json` 的 `database_url` → `…\BridGes\data\bridges.db`；仓库根目录 `bridges.db` 是旧库，未作为修复对象。
- 修复前：`schema_meta.version=58`（等于当时 `SCHEMA_VERSION`）；`profile_items`、`profile_item_migrations`、`idx_profile_items_account_status`、`idx_profile_item_migrations_account` 全部缺失；旧完整性清单只覆盖 6 张核心表与 2 个索引 ⇒ 进程自认健康。
- 真实读取路径（`SqliteAtomicProfileRepository.list_items`，即页面 `GET /profiles/items` 的最内层调用）直接抛 `sqlite3.OperationalError: no such table: profile_items`；活动四维记录 2 条；exhausted 抽取 run 4 条、succeeded 111 条。
- 页面 500 的覆盖方式：仓库级夹具钉住缺表异常本身（任务 2 的「真实仓库」侧）；API 级则是两个夹具——可修复漂移在启动即被修复，`GET /profiles/items` 返回 200；不可修复或结构不兼容时启动门拦成 503 且不报告健康。修复迁移在启动阶段执行，所以修复后同一漂移在 API 层不再表现为 500，这正是任务 4 要求的「不健康就不服务」。
- 一致性副本用「只读连接 + SQLite 在线备份 API」生成（该库开着 WAL，`shutil.copy2` 会读到过期快照），原库全程只读。
- 原始输出：`.tmp/issue01/raw/structure-before-repair.json`（原库只读核查）、`raw/drill-real-copy.json`（副本演练）。

### 结构差异与成因

- 差异：缺 2 张画像表 + 2 个索引；本票另新增逐条对账台账 `profile_item_migration_records`（属于新对象，不是「补回」）。
- 成因（代码 + 结构证据；未声称锁定某次人工操作）：`51` 号迁移被 Issue 08（原子画像）与并行分支 Issue 11（paper_search）各自声明，合并时 paper_search 让位到 52、画像建表保留 51；`initialize()` 只重放 `range(current+1, SCHEMA_VERSION+1)`，所以「已由旧构建升到 51 的库」永久跳过画像建表。运行时数据目录的 `bridges.db.backup-before-v54-*` 已含 `messages.paper_search`，说明该库跑的是 paper_search 版 51。

### 迁移版本与执行顺序

- 迁移版本 **59**（`SCHEMA_VERSION` 58 → 59）；59 号迁移只做幂等补齐（全部 `IF NOT EXISTS`），建表语句与 51 一致，测试断言「漂移修复路径」与「正常 51 路径」的表列集合与索引 SQL 完全相同。
- 执行顺序：完整性预检（同名对象结构兼容性）→ 可修复的历史漂移经迁移补齐 → `verify_schema_integrity()` 复核。已到当前版本仍缺失、或同名对象列不齐 ⇒ 失败关闭，错误码 `database_schema_integrity`（缺失）与 `database_schema_object_incompatible`（结构不兼容），诊断只含对象名与列名，不含路径或正文。

### 历史记录对账（任务 5，真实库副本演练）

- 首次迁移：`migrated=2 duplicated=0 tombstoned=0 skipped=0`，两条来源记录的结论都是 `source_migrated`。
- 二次迁移：`migrated=0 duplicated=2`，`reconciliation_digest` 与首次相同（幂等），页面可见条目 2 条。
- 逐条原因码共 8 个：`source_migrated`、`source_text_empty`、`source_withdrawn_tombstone_written`、`source_record_already_linked`、`identity_suppressed_by_tombstone`、`user_item_kept_not_overwritten`、`identity_exists_item_linked`、`source_migration_failed`；前 7 个是正常判定，`source_migration_failed` 只在可重试报告里出现（整批已回滚，台账只留失败那一条）。用户编辑过的条目不再被回填改写，删除墓碑不再复活。
- 逐条明细落在新台账 `profile_item_migration_records`（只有来源 id、结论、原因码、条目 id，没有正文），与报告同事务写入，重开连接仍可复核；`GET/POST /profiles/items/migration` 都返回该明细（`AtomicProfileMigrationReport.reconciliation`，已同步 `openapi.json`）。

### 失败回滚与备份

- 注入非法语句后 `initialize()` 抛 `StorageError`，事务整体回滚：版本仍 58、画像表没有被建出来、业务计数不变；迁移前备份 `bridges.db.backup-before-v59-*`（版本 58、计数相同）放回正式路径后仍可继续升级。
- 副本演练前后业务计数完全不变：`accounts/conversations/messages/objects/four_dimension_records/extraction_runs = 1/59/184/8/2/115`；第二次启动不再产生备份。
- 原子化迁移前另落 `bridges.db.backup-before-atomic-profile-*` 快照（复用 `snapshot_lock` + `snapshot_to`，只保留最新一份，且先落新快照再清理旧副本），迁移在单事务内完成，任一条失败整体回滚。
- 可重试报告与台账必须自洽（评审修正，见「评审与修正」）：失败批次里已成功写入但随后回滚的条目不再按原结论入账——否则台账会出现指向不存在条目的 `source_migrated` 行。可重试报告的计数全部归零、逐条明细只留 `source_migration_failed` 那一条，重试重新判定每一条。
- 快照失败不再静默：降级为 `atomic_profile_migration_snapshot_failed` 告警日志（含目标文件名与错误类型），成功时记录 `atomic_profile_migration_started`（含快照文件名）；返回值由调用方消费。

### 已耗尽抽取任务（任务 6）

- 4 条 exhausted：3 条（2026-08-11）是模型输出合同/校验失败（`profile_extraction_contract_invalid`、pydantic `ModelCallResult` 校验错误），与缺表无关，属永久失败 ⇒ **不重排**。
- 1 条（2026-09-27、`source=local_rule`、`profile_extraction_unexpected`、attempts=3、`record_ids=[]`、`observed_count=0`、1 秒内耗尽）命中缺表指纹；最小复现（合成消息、同一组合、不修复结构）产出字段级一致的 run 行，且四维写入随镜像失败一起回滚 ⇒ 确认缺表是原因。
- 既有受控重试（`cleanup_exhausted_profile_extractions`）原本只匹配 `client_error_400`，无法处理这一条；本票把它改为显式错误码参数（默认值不变，未确认成因时不会碰这类行），并端到端验证「确认成因 → 受控重排 → `run_retry_tick` → 抽取成功且镜像进原子画像」，不重跑其他聊天。
- **真实库的应用与备份核验交由 07**：本票未改真实库，只提供副本演练结果与这条可执行入口。

### 测试命令及输出

| 命令 | 结果 |
| --- | --- |
| `PYTHONPATH=src pytest tests/storage/test_issue01_profile_schema_recovery.py tests/profiles/test_issue01_profile_recovery.py -q` | 15 passed |
| 反向验证：把失败路径临时改回「保留已回滚结论」的旧写法后单跑新回归用例 | 按预期失败（明细为 `['migrated','failed']`，期望 `['failed']`）；恢复修复后通过 ⇒ 该用例确实钉住此缺陷 |
| `PYTHONPATH=src pytest tests/storage tests/profiles tests/api tests/contracts -q` | 424 passed, 1 failed（预存在：`test_issue01_chat_profile_correction.py::test_chat_correction_uses_latest_record_and_is_idempotent`，未改动的 `main` 上同样失败） |
| `PYTHONPATH=src pytest tests/lifecycle -q` | 56 passed, 5 failed（5 项为上述预存在夹具失败，未改动基线上完全相同） |
| `PYTHONPATH=src pytest tests -q`（全量，见下节） | 251 failed / 3735 passed / 37 skipped，失败名称与基线双向 diff 为空 |
| `npx vitest run`（`apps/web`） | 22 files / 190 tests 全通过 |
| `npx tsc --noEmit`（`apps/web`） | 干净 |
| `PYTHONPATH=src pytest tests/profiles/test_profile_extraction_cleanup.py -q` | 1 passed（受控重试脚本默认行为未变） |
| `PYTHONPATH=src pytest tests/contracts -q` | 3 passed（`openapi.json` 已用 `scripts/regenerate_openapi.py` 同步） |
| `npx vitest run src/components/account/profile/AtomicProfileCenter.test.tsx` | 6 passed（新增「服务失败不渲染成空画像」用例） |
| `python .tmp/issue01/scripts/inspect_authoritative_db.py --copy-to … --json-out …` | 权威库只读核查 + 一致性副本 |
| `python .tmp/issue01/scripts/drill_real_copy.py` | 真实库副本演练（修复 / 对账 / 幂等 / 备份） |
| `ruff check`（改动文件） | 仅剩 `database.py:1671` 一条预存在超长行 |
| `ruff format --check`（改动文件） | 仓库既有文件同样不满足（`main` 上一致），本票不引入格式化改动 |
| `mypy`（改动文件） | 干净 |

环境提示：本机 conda `agent` 激活会把 `SSL_CERT_FILE` 指向不存在的 `envs/agent/ssl/cacert.pem`，导致 `httpx.Client()`（`create_app()` 内部创建）抛 `FileNotFoundError`；跑接口层测试前 `unset SSL_CERT_FILE`。该现象与本票改动无关（不激活时该变量为空），但会影响复现。

### 全量回归：分支跑 vs 基线跑

- 两次全量都在各自的工作树里跑：分支 `.worktrees/01-profile-schema-recovery`（挂载 `apps/web/node_modules`），基线 `.worktrees/issue01-baseline`（`6939da3` 的 detached 工作树，不带前端依赖）。命令一致、跑法一致：`PYTHONPATH=src pytest tests -q -p no:cacheprovider --basetemp=<仓外目录>`，均为一次跑完、0 error。
- 结果：分支 **251 failed / 3735 passed / 37 skipped**，基线 **251 failed / 3718 passed / 39 skipped**；失败用例名称双向 diff **仅分支 0 条、仅基线 0 条**。
- 收集数差 15 = 本票新增的 15 个用例（7 个库级 + 8 个画像/接口级）；通过数差 +17 = 15 个新用例 + 2 个 `NEEDS_WEB_BUILD` 用例（分支工作树有 `apps/web/node_modules`，这两个用例在分支上实际执行并通过，在基线工作树上被跳过），与跳过数 −2 相互抵消。
- 原始输出：`.tmp/issue01/raw/full-branch.txt`、`.tmp/issue01/raw/full-baseline.txt`；差值脚本 `.tmp/issue01/scripts/diff_full_runs.py`（只读两个输出文件，提取 `FAILED` 名称做双向差集）。
- 中间一轮（评审修正后、`catalog.py` 登记前）曾出现 **1 条仅分支失败**：`tests/lifecycle/test_v2_21_retired_history_lifecycle.py::test_account_catalog_covers_every_live_account_scoped_table`（守卫发现新增的 `profile_item_migration_records` 未进 `ACCOUNT_TABLES`，账户删除/导出会静默漏表）。补登记后重跑即 0 条；本记录里的全量数字是补登记之后的最终一轮。
- 预存在失败与本票无关，但值得单独记一笔：`tests/lifecycle/test_lifecycle_api.py` 的 5 个用例（导出预览、导出下载、删除账户、创建备份、备份还原）在**未改动的基线树上同样失败**，原因是它们的夹具只传 `state_store`、不覆盖数据库地址，于是落到真实数据目录，注册固定账号时拿到 409（该账号已存在）。它们在任何已存在真实数据的机器上都会失败；本票未改这些用例（属 Issue 37 的测试隔离问题），但**这条夹具路径会在通过时写真实库**，值得后续单独立票处理。

### 评审与修正（/code-review 两轴）

| 发现 | 轴 | 处置 |
| --- | --- | --- |
| 新增台账表未登记 `ACCOUNT_TABLES`（账户删除/导出会静默漏表） | 两轴评审未覆盖，由全量回归差值发现（分支多出 1 条失败） | **已修**：`lifecycle/catalog.py` 三处清单（删除顺序、导出类别、逻辑摘要）登记该表，`tests/lifecycle` 守卫用例恢复通过 |
| 失败批次已回滚的结论仍进台账（`source_migrated` 行指向不存在条目） | 两轴同时命中 | **已修**：可重试报告计数归零、只留失败那一条；新增回归用例并做反向验证（见上表） |
| `snapshot_before_migration()` 返回值无人消费、快照失败静默降级 | 两轴同时命中 | **已修**：调用方记录快照文件名，失败降级为告警日志（含错误类型） |
| `duplicated` 结论同时表达「重复、墓碑抑制、用户条目优先」三种语义 | 标准轴 | **改文档而非改契约**：枚举与 `openapi.json` 说明改为「来源或身份已存在，因此未新建条目，具体看原因码」；计数与明细同名这一性质不变 |
| `counts[entry.outcome.value] += 1` 用字符串元组初始化，缺 `failed` 键（将来 `_reconcile_record` 返回 FAILED 会 KeyError） | 标准轴 | **已修**：计数表按枚举成员生成 |
| `_report_from_row` 之外两处 getter 重复拼装对账明细 | 标准轴 | **已修**：明细在 `_report_from_row` 内一次拼装，两个读接口只剩一行 |
| 59 号迁移的 DDL 与 51 号逐字重复 | 标准轴 | **不采纳**：历史迁移已发布（改 51 的文本等于改历史迁移语义），且 59 号必须能独立重放；重复在此是「两条路径各自完整」的代价，由 `test_repaired_structure_matches_normal_upgrade_path` 钉住两者结构一致 |
| 快照失败后仍继续迁移（未做到「无还原点即拒绝」）、只保留最新一份快照 | 标准轴 | **部分采纳**：保留「无快照也继续」（单批迁移本身受事务保护，失败整体回滚；四维迁移的服务层 API 也未把备份设为前置），但补上告警日志与文档说明；快照清理仍是「先落新、再清旧」 |
| 与关联用例无关的一行被拼接到同一行（前端测试文件） | 标准轴 | **已修**：恢复原样 |
| 两个测试文件各有一份漂移夹具、`four_dimensions._repository` 私有访问 | 标准轴 | **不采纳**：跨文件 import 夹具会让两个文件互为依赖；私有访问处已注明用途（造旧数据），仅测试夹具使用 |

### 提交版本

- 实现提交：`6dd60d5`（修复迁移 59、完整性预检与稳定错误码、逐条对账与台账、快照、受控重试参数、15 个新用例）
- 评审修正提交：`a78bb9e`（台账与回滚事实一致、账户表登记、快照告警、原因码更名、文档对齐）
- 记录提交：`5a422cc`、回填提交 `2b32d16`
- 合并：先 `git merge main`（当时 main = `6b9b9c7`，含并行票 02 与 04）得 `0d79857`，**无冲突**（main 侧改动未触及本票任何文件，`SCHEMA_VERSION` 仍是 58、与本票的 59 不撞号）；随后 **no-ff 入 main `23ff326`**（合并树 `b162529b` == 分支树 `b162529b`，故分支上的全部验证结论对 main 适用），**main == origin/main `23ff326`**。

### 合并后验证与清理实证（2026-09-27）

- 合并后在 main 工作树复跑：`tests/storage/test_issue01_profile_schema_recovery.py tests/profiles/test_issue01_profile_recovery.py tests/contracts` **18 passed**；`tests/storage tests/profiles tests/api tests/contracts tests/lifecycle tests/ai` **655 passed / 6 failed**（6 条全是前述预存在失败：1 条 chat 画像纠错时钟、5 条 lifecycle 接口夹具）；`npx vitest run` **24 文件 / 202 例**通过、`tsc --noEmit` 干净；`scripts/regenerate_openapi.py` 复跑后 `openapi.json` 无差异。
- 清理实证：Issue 工作树 `.worktrees/01-profile-schema-recovery` 已删（`git worktree remove --force` 一次成功，目录未残留）；本地分支 `codex/01-profile-schema-recovery` 已删（`-d` 安全检查通过，4 个提交经合并提交可达）；`git worktree list` 只剩主仓与并行票 06 的工作树；`.git/worktrees/` 只有 `06-tieba-research`，`git worktree prune --dry-run -v` 无输出（无失效记录）。
- 一处与既往票不同的事实：本工作树的 `apps/web/node_modules` 清理时是**实体副本**（与主仓同名目录 inode 不同、`dir /AL` 查不到重解析点，均 361 条），因此按普通目录随工作树删除；主仓 `apps/web/node_modules` 清理后仍 361 条、完好。

### 未完成事项

- 真实用户库的升级应用、迁移前备份核验与浏览器端真实点击回归按本票范围交给 07 统一执行；本票只做了副本演练与接口／组件级验证。
- 3 条模型合同类 exhausted 抽取任务没有恢复路径（属永久失败，重排也不会成功），保持原状。
- 结构漂移的具体历史操作序列仍未完全还原（v51 两变体之间的中间迁移过程缺少台账），只能证明「51 号画像建表对该库不可达」这一必要条件与最终结构；07 若要追加取证需要更早的备份。

## Comments

暂无。
