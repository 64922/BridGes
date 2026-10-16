# 17 — 可恢复地重整旧画像并补提取合法历史：验证记录

**分支：** `codex/issue-17-resumable-history-rebuild`（基点 `87e114f5`，与当时 main 同点）。
**环境：** conda `agent`（Python 3.11.15）；全量测试带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`
（项目已记录的沙箱删除 shim 规避）；走查为确定性走查（真实 SQLite 实例，
规则抽取器，不调用外部模型、不读取任何凭据与真实数据）。
**证据：** 本文 + `evidence/demo-walkthrough-20261017.txt`（27 项断言全部通过）+
`tools/walk_issue17_history_rebuild_demo.py`（可复跑）。
**可复跑：**

    python .scratch/profile-memory-redesign/validation/17-resumable-history-rebuild/tools/walk_issue17_history_rebuild_demo.py \
        --out .scratch/profile-memory-redesign/validation/17-resumable-history-rebuild/evidence/demo-walkthrough-<stamp>.txt

（走查约 6 秒，工作区在 `.tmp/issue17-demo-*`；脚本只使用临时库并保留运行
目录供复核，复核后按 §6 清理。）

## 1. 验收标准对照

| 验收标准（票据） | 证据 |
| --- | --- |
| 同一批次重复执行和进程崩溃后恢复，最终有效事实及证据关系一致，无重复生效 | `test_issue17_history_rebuild.py::test_resume_mid_flight_is_idempotent`（`max_ticks=1` 中断后重入至完成；每个来源恰 1 个成功运行、恰 1 条事实）；`::test_start_builds_isolated_copy_and_seeds_scope`（重复启动幂等、窗口冲突拒绝）；幂等键 =（账户, 消息, 抽取器版本, 来源哈希）由在线调度与队列恢复共用（`automatic.py::_recover_run` / `ensure_task_queued`）；走查 [2] |
| 重整期间人工修改、删除、停止记录和来源修订均能阻止迟到提案；不得复活被遗忘内容 | 门控唯一实现 `history_snapshot.py::classify_history_message`（预览与重整共用）：墓碑、撤回（按来源标识与身份键）、内容禁止、停止回合、空消息在入队前拦截；提交前复核沿用在线屏障（`source_invalidated`/`privacy_blocked`/`version_unavailable`）。`reconcile` 的 `manual_items_preserved`（正文与编辑时间摘要）、`withdrawn_not_revived`、`tombstone_barrier`（墓碑消息在重整期间出现成功抽取即失败）。走查 [1][4]；`test_issue17_history_rebuild_reconcile.py::test_reconcile_detects_manual_tamper` |
| 原始消息时间与事实适用时间保留，过期状态不会因本次重整获得新鲜度 | 重整复用在线提交语义：明示时间与有效期按**来源消息时间**锚解析——提交路径携带 `source_at`（来源消息原始时间，`commit.py:192-210`），`atomic.py::commit_fact` 以 `anchor = source_at` 调 `parse_validity_window(text, anchor)`（`atomic.py:3684`、`:3721`）；条目 `created_at` 是本次写入时间（`atomic.py:3849`），`first_stable_recorded_at` 仅用于旧四维迁移路径 `_item_from_record`（`atomic.py:1379`），不参与补提取提交。补提取条目按来源时间获得适用期，不获得“今天”的新鲜度；恢复与摘要按原时间参与比较。模块边界见 §5 |
| 未授权材料和敏感历史按既定合同跳过，不因为存在旧标签就自动获得授权 | 重整只播种聊天来源窗口内的用户消息；材料类来源（`automatic_unverified_source`）不进入窗口、不重新提取（预览 `unverified_source_refs` 语义保持不变），材料类运行存在时条目记 `material_run_present` 跳过；敏感/凭据分类沿用在线敏感度合同（预览 `credentials_flagged` 不进入可提取集合）。走查 [1] 只覆盖聊天窗口 |
| 单项失败有可追踪状态，统计分母和失败数真实；不能用“处理完成”掩盖未提取范围 | 台账逐条 `status/skip_reason/last_error/committed_count`（`profile_rebuild_items`），计数器由逐条行重算（`refresh_counters`）；重试耗尽（claim `failed` 且无 `next_retry_at`）如实记为 `failed`，屏障（来源失效/隐私/版本）记为 `skipped` 而非失败（`test_issue17_history_rebuild.py::test_retry_exhaustion_marks_failed_not_success`、`::test_privacy_barrier_skip_is_not_failure`）；未终态不允许对账（`rebuild_not_settled`）。走查 [2][4] |
| 完成后界面、检索和概览一致，派生失败可重建且不泄漏旧事实 | 重整写入走在线提交路径（自动触发索引/概览失效与重建，同组合根接线）；对账 `derived`：概览失效→重建为 `current/empty` 且两次读取一致、召回索引陈旧行按版本/哈希过滤只记警告（0 陈旧为通过态）。`test_issue17_history_rebuild_reconcile.py::test_reconcile_passes_and_persists_report` |
| 对账涵盖人工条目、证据、控制、删除屏障、派生依赖、账户清理和导出；执行恢复演练而非仅备份 | `history_rebuild_reconcile.py::reconcile_rebuild` 八段：facts（人工摘要/撤回不复活/事实键不重复/墓碑迟到成功）、evidence（来源可解析且非墓碑、台账与运行终态一致；既有缺证据按基线标记、仅新增悬挂引用失败）、controls（与基线一致）、derived、export（逐类别逐表 `export_rows` + 审计类别登记）、deletion（副本上 `delete_account_rows` 后账户表残留为 0）、restore（工作副本重新预览→`create_history_backup`→`verify_history_restore`+逐表逻辑摘要一致）、source_drift（权威来源前进只记警告）。报告写 `reconciliation.json`，仅通过时写 `reconciled_at` |
| 实际用户库的执行按现有操作授权与运行手册单独确认，不因工单发布自动执行 | CLI 只处理工作副本与自身产物（`profile-rebuild start/run/status/reconcile/cleanup`）；`start` 要求新鲜预览清单并先建立已验证备份（恢复前提）；真实用户库的切换按 §6 运行手册单独确认，工单发布不触发任何自动执行 |

## 2. 确定性走查（最终代码，20261016T163922Z，27/27 通过）

合成旧库：人工条目 1、可核验自动 1、缺证据 1、撤回 1、被替代 1；消息 9 条
用户 + 1 条助手，含停止回合、空消息、墓碑、内容禁止、撤回来源与一条窗口外
旧消息（南京，2026-01-01）。

1. **启动与隔离**：过期清单（来源新增消息后）拒绝启动且不建立工作目录；
   恢复来源后启动得到与预览逐项一致的分母（窗口总数 9、可用 4、跳过 5 及
   五类原因）；工作目录含 `rebuild.json`/`preview.json`/`history.db`/
   `working/bridges.db`；重复启动幂等；同目录换窗口拒绝（`work_dir_conflict`）；
   源库文件摘要与 10 张业务表计数不变、零抽取运行。
2. **有界推进与崩溃恢复**：`max_ticks=1` 只消耗 1 次模型调用并暂停；重入
   1 次后收敛（抽取 4、写入事实 2、失败 0、跳过 5、无待定/在途）；终态后
   重复运行 `ticks=0`；每个来源恰 1 个成功运行与 1 条事实（幂等键）。
3. **窗口范围**：`--start-at` 只播种窗口内 8 条消息（可用 3），窗口外旧
   消息零行；台账记录窗口下界；不同窗口派生不同 `rebuild_id`。
4. **版本与屏障**：篡改台账抽取器版本后恢复被拒（`rebuild_version_mismatch`）；
   模拟重试耗尽与屏障：重试耗尽记 `failed`（不冒充成功）、屏障记
   `version_unavailable` 跳过（不算失败）；**中途墓碑**：在途消息在工作
   副本落墓碑后，提交前复核拒绝迟到写入，记 `source_invalidated` 跳过、
   零失败、零落地事实；三者后批次均收敛。
5. **完整对账与演练**：`passed=True`（人工 1/1 完好、既有缺证据按标记、
   导出 93 表、账户删除演练零残留、备份与恢复演练通过、逻辑摘要一致）；
   演练只在副本执行（源库计数不变）；改写人工条目后对账失败
   （`manual_items_preserved`）；权威来源前进只记警告。
6. **清理语义**：未收敛拒绝清理；完成后删除工作副本与台账、保留已验证
   备份/清单/元数据（可审计、可恢复）；重复清理幂等；`--purge` 删除恢复
   前提后状态查询显式失败（`rebuild_metadata_missing`）。

## 3. 自动化测试

- 新增 `tests/profiles/test_issue17_history_rebuild.py`（10 项）：启动隔离与
  分母一致性、过期清单拒绝、幂等与窗口冲突、有界 run 与恢复直至收敛、
  崩溃重入幂等（运行/事实唯一）、重试耗尽如实失败、屏障跳过不算失败、
  中途墓碑阻止迟到提案（提交前复核拒绝写入且零落地）、版本不符拒恢复、
  清理守卫与 purge、批次标识绑定。
- 新增 `tests/profiles/test_issue17_history_rebuild_reconcile.py`（5 项）：
  未收敛拒绝对账、完整对账通过并写盘（八段明细 + 源库只读 + `reconciled_at`）、
  人工篡改检出、来源前进警告（通过）、无权威来源降级警告。
- 新增 `tests/profiles/test_issue17_history_rebuild_cli.py`（3 项）：
  start→run（限额）→run（收敛）→status→reconcile→cleanup --purge 全流程；
  错误 JSON + 退出码 2（work_dir_missing、window_invalid）；对账失败退出码 2。
- 新增 `tests/storage/test_schema_v89_profile_rebuild.py`（2 项）：v88→v89
  升级建表与重复初始化幂等；台账登记（REQUIRED_TABLES、导出类别
  `profile_history_rebuild`、逻辑摘要、删除顺序先于 `profile_items`）。
- 聚焦复跑（修复轮后）：
  - `pytest tests/profiles/test_issue17_history_rebuild.py -q` → **10 passed**。
  - `pytest tests/profiles -q` → **799 passed**（含 04/16 等既有合同与全部
    新增）。
  - `pytest tests/profiles/test_issue16_history_preview*.py tests/profiles/test_issue17* tests/storage/test_schema_v88*.py tests/storage/test_schema_v89*.py tests/storage/test_database_migration.py -q` → **58 passed**。
- 全量 pytest（分支与 main 同命令同参数 `python -m pytest -q --ignore=tests/career_plan`）：
  - 分支（修复轮后，本票 20 项）：**328 failed / 5732 passed / 39 skipped**（24:24）。
    失败集合经 `--lf --collect-only` 复核：`tests/profiles`／`tests/storage`／
    CLI 相关 0 命中（无本票回归）；相对上轮 327F 的 +1 属既有环境性 flaky
    家族（含并发/检索等，main 本轮同族 329F）。
  - main（同点 `87e114f5`）：**329 failed / 5713 passed / 37 skipped**（25:19）。
  - 失败集合：worktree 记录失败全部为 main 已有（唯一“分支独有”条目是
    重构中改名的旧测试缓存项，现已拆分为两项并通过）；另对 main 抽查
    `tests/mcp/test_mcp_service.py`、`tests/orchestration/test_issue37_verified_synthesis.py`、
    `tests/plugins/test_plugin_api.py`、`tests/learning_projects/test_learning_projects_api.py`
    → **86 failed**，与本分支同族（环境性既有失败，含 MCP/插件/编排的
    setup 问题）。通过数差 5732−5713 = 19（本票新增 20 项；1 项既有用例
    本轮 flaky 失败，属 main 同族环境性问题，与本票无关）。
  - `tests/career_plan` 收集错误（`CareerBackgroundLoader` ImportError）在
    main 检出同样复现（`D:\BridGes` 直跑），与本票无关。

## 4. 静态检查

- `mypy src`：分支 **141 errors / 23 files（712 源文件）**；main 同为
  **141 errors / 23 files（702）**——本票 10 个新源文件 0 错误、修改文件
  0 新增（诊断集合含文件路径一致）。
- `ruff check .`：分支与 main 同为 **453 errors**；分支独有诊断修复后为 0
  （首轮含走查脚本 5 条，已清理；行号位移差异不计）。本票新增/修改文件
  0 诊断。
- `pip check`：No broken requirements found。

## 5. 接口与实现摘要

- 模块布局（拆分为 10 个源文件，均 ≤ 500 行，职责单向依赖：contracts ←
  records ← repository / pipeline ← runtime ← drive，engine 与 reconcile 在上层）：
  - `history_rebuild_contracts.py`：`HISTORY_REBUILD_POLICY_VERSION =
    "profile-history-rebuild-policy-v1"`、`HISTORY_REBUILD_STATE_VERSION =
    "profile-history-rebuild-state-v1"`、工作目录文件名常量；`ProfileRebuildError`；
    `rebuild_id_for(account, state_digest, policy_version, preview_version,
    window)` = `profile-rebuild-<sha256 前 32>`；`RebuildWindow`
    （`messages.created_at` 半开区间、损坏时间戳不落入有界窗口）；词表
    `RebuildStatus`/`RebuildItemStatus`/`RebuildSkipReason`（门控原因与
    预览 `chat_scope` 计数键一一对应；屏障错误码
    `profile_extraction_source_invalidated` / `privacy_blocked` /
    `version_unavailable` 映射为跳过）；`now_text`/`text_digest`/`json_mapping`。
  - `history_rebuild_records.py`：记录数据类 `RebuildRunRecord`/
    `RebuildItemRecord`/`RebuildProgress`/`RebuildSeedItem`/`ManualItemDigest`/
    `RebuildBaseline`（人工条目摘要 = id + version + `user_edited_at` + 正文
    摘要；撤回条目与身份键、墓碑标识）。
  - `history_rebuild_repository.py`：`profile_rebuild_*` 台账 CRUD、幂等
    `seed_items`、`refresh_counters`（分母/失败数由逐条行重算）、
    `require_run`、模块级 `build_progress`。
  - `history_rebuild_pipeline.py`：在线管道只读视图（`ExtractionRunSnapshot`/
    `ExtractionTaskSnapshot`/`QueueClaimSnapshot`；`read_extraction_run`/
    `read_task`/`read_claim`/`count_pending_tasks`（在线背压同一口径）/
    `count_tombstoned_successes_since`）。
  - `history_rebuild_runtime.py`：工作区/元数据（`RebuildMetadata`、
    `write_metadata`、`load_workspace`、`open_working_database`）与
    `build_runtime` 按组合根同一接线（复用 `app_support` 的三个读取适配器；
    `ExtractorFactory` 由调用方注入模型适配，测试为确定性规则、生产为网关，
    `rebuild_extractor_version()` 与适配器实现无关；召回索引为读时同步
    派生数据，不挂载召回读端口）。
  - `history_rebuild_drive.py`：有界驱动 `run_rebuild`（调度 → `run_retry_tick`
    至多 `max_ticks` 次 → 再调度 → 同步终态；空转回执与在线共享
    `RETRY_TICK_IDLE_OUTPUTS` 常量，禁止字面量漂移；`remaining==0` 才
    `completed`；版本不符 `rebuild_version_mismatch` 拒绝恢复）。
  - `history_rebuild.py`：启动/进度/清理。`start_rebuild` 崩溃恢复顺序：
    批次标识与 `rebuild.json`（journal）先于一切数据产物 → 清单新鲜度
    （失败时清除未产物的目录状态）→ 备份完整性以 `<backup>.manifest.json`
    为 journal（缺失即视为未完成、重新创建）→ 备份 → 工作副本 →
    仅在台账运行行缺失时重算基线/幂等播种/写运行行（运行行已存在则只
    报告状态）；`rebuild_status`；`cleanup_rebuild`（未收敛拒绝，`--purge`
    删除恢复前提）。
  - `history_rebuild_reconcile.py` / `history_rebuild_drills.py`：八段对账
    （facts/evidence/controls/derived/export/source_drift 在 reconcile；
    deletion/export/restore 演练在 drills）；报告结构含
    `passed`/`errors`/`warnings`/`state_version`/各段明细，写
    `reconciliation.json`（`REBUILD_REPORT_FILENAME`），仅通过时写
    `reconciled_at`。
- 新 CLI `bridges/cli/profile_rebuild.py`：`BridGes profile-rebuild
  start|run|status|reconcile|cleanup`；JSON 输出；硬门失败 stderr
  `{"error": {"code", "message"}}` + 退出码 2；对账未通过退出码 2。错误码：
  `work_dir_missing`、`rebuild_metadata_missing` / `rebuild_metadata_invalid`、
  `work_dir_conflict`、`preview_stale`、`working_copy_missing` /
  `working_copy_mismatch`、`account_missing`、`rebuild_not_found`、
  `rebuild_not_settled`、`rebuild_version_mismatch`、`max_ticks_invalid`、
  `window_invalid`、`working_copy_failed`、`schema_version_missing`、
  `model_gateway_unavailable`。
- 迁移与目录登记：`SCHEMA_VERSION=89`（`profile_rebuild_runs` 含窗口、
  版本绑定、基线 JSON 与计数；`profile_rebuild_items` PK
  (rebuild_id, message_id)）；`REQUIRED_TABLES` 登记两表；`ACCOUNT_TABLES`
  子表先于运行行、先于 `profile_items`；导出类别 `profile_history_rebuild`
  （"画像历史重整审计"，runs/items）；`logical_summary` 增两表（随删除与
  备份恢复；报表不含正文）。
- 复用与去重：`history_snapshot.py` 的门控抽取为公共
  `classify_history_message`（预览与重整唯一实现，计数键与跳过原因对齐）；
  `automatic.py` 的启动恢复抽取 `_recover_run` 并新增
  `ensure_task_queued`（重整经在线调度入口入队，不绕过背压）；专用队列
  `PROFILE_REBUILD_QUEUE`；空转回执共享 `RETRY_TICK_IDLE_OUTPUTS` 常量。
  前置合同：16 的 `verify_preview_manifest` / `create_history_backup` /
  `verify_history_restore` / `inspect_database`（由 16 的私有
  `_inspect_database` 改名公开，调用点已同步）。
- 数据边界：台账只存标识、计数与摘要（消息/条目正文不进台账与报告）；
  工作副本与已验证备份分离；演练一律在副本上执行。

## 6. 演练手册（命令、预期、失败恢复、清理）

CLI（仓根执行，worktree 内须先 `$env:PYTHONPATH='src'`）：

    python -m bridges.cli.main profile-rebuild start --manifest <目录>/preview.json \
        --work-dir <工作目录> [--start-at <ISO>] [--end-at <ISO>] [--db <权威库>]
    python -m bridges.cli.main profile-rebuild run --work-dir <工作目录> [--max-ticks N]
    python -m bridges.cli.main profile-rebuild status --work-dir <工作目录>
    python -m bridges.cli.main profile-rebuild reconcile --work-dir <工作目录> \
        [--drill-dir <目录>] [--db <权威库>]
    python -m bridges.cli.main profile-rebuild cleanup --work-dir <工作目录> [--purge] [--force]

预期：`start` 输出 `reused`、备份记录与进度（分母/可用/跳过原因与预览一致）；
`run` 输出 `ticks`（≤ `max_ticks`）、`completed`、`reached_budget` 与进度；
`status` 输出版本绑定与 `reconciled_at`；`reconcile` 输出八段明细与
`passed`（未通过退出码 2）；`cleanup` 默认保留恢复前提。中断后重复 `run`
即恢复，不重复生效。

失败恢复：

- `preview_stale`：来源在预览后变化。`start` 在任何数据产物之前失败并
  清除自身留下的定位标记（工作目录回到干净状态），重新 `preview` 生成
  新清单后可直接在同目录重试；启动在备份/副本阶段中断的情形按恢复顺序
  继续（半成品备份以完整性 manifest 缺失识别并重建）。
- `rebuild_version_mismatch`：策略/抽取器版本已变。不得静默继续，重新
  预览启动新批次（旧台账可用 `cleanup --force` 放弃）。
- `rebuild_not_settled`：仍有待定/在途条目。继续 `run` 推进或显式
  `cleanup --force` 放弃。
- `working_copy_missing` / `working_copy_mismatch`：工作副本缺失或与清单
  不一致（被并发修改）。从同批次备份重建工作副本不在本工具内自动执行，
  显式失败以保护既有进度；重新预览启动新批次。
- 失败条目（`failed`）：查看 `status` 的 `last_error` 与逐条 `skip_reason`；
  修复原因后重新 `run`（重试预算由在线队列语义管理）。
- 真实用户库切换：按现有操作授权流程单独确认，本工具不自动切换。

清理：走查/演练产物在 `.tmp/issue17-demo-<stamp>` 与所选工作目录：

    Remove-Item -LiteralPath <运行目录> -Recurse -Force

工作目录在 `cleanup --purge` 前保留 `history.db`（已验证备份）、
`preview.json` 与 `rebuild.json` 供审计与恢复；确认无需复核后再删除。

## 7. 剩余限制与边界

- 走查为确定性合成库 + 规则抽取器；生产网关装配经 CLI `_extractor_factory`
  与组合根同接线但未走查（无真实 Key）。真实用户库的规模、WAL 大小与
  模型成本未做基准。
- 对账 evidence 段对**基线已分类**的既有降级（缺证据/悬挂来源/部分证据）
  按“标记处理结果”对待（`known_unverified_refs`），仅新增悬挂引用失败；
  这是对 16 票「缺失证据明确标记」语义的持续承接。
- 召回索引为**读时同步**派生数据（无写时推送路径）：重整提交不会逐条
  推送索引；陈旧行不参与检索（版本/哈希过滤），对账 `derived` 段对陈旧行
  如实计数并只记警告，切换后首次召回复位。
- 元数据 `rebuild.json` 绑定批次标识与版本展示；恢复门槛以台账运行行为
  权威（篡改元数据不改变恢复判定），两者同时被篡改属于运行环境被破坏的
  场景，由审计而非静默兼容处理。
- 背压：单账户待处理任务上限 `PROFILE_EXTRACTION_MAX_PENDING_PER_ACCOUNT=32`；
  被拦截条目保持 `pending` 且 `run` 处于 `paused`（note 说明），不绕过
  在线背压、不丢弃。
- 恢复演练的副本以 `shutil.copyfile` 创建（不复制 `-wal` 文件）：源为
  在线备份（WAL 已合并）；若未来备份方式改为不 checkpoint 的文件拷贝，
  须同步更新演练复制方式（同 16 票记录）。
- 工单「明确不做」保持：不批量恢复删除信息、不直接套用旧迁移程序、不为
  旧四类标签增加双写兼容路径。
