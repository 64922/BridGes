# 16 — 历史重整只读预览与可恢复备份：验证记录

**分支：** `codex/issue-16-history-preview-and-restore`（基点 `fff98a9d`，main）。
**环境：** conda `agent`（Python 3.11.15）；全量测试带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`
（本项目已记录的沙箱删除 shim 规避）；走查为确定性走查（真实 SQLite 实例），
不调用外部模型、不读取任何凭据与真实数据。
**证据：** 本文 + `evidence/demo-walkthrough-20261016.txt` +
`tools/walk_issue16_history_preview_demo.py`（可复跑）。
**可复跑：**

    python .scratch/profile-memory-redesign/validation/16-history-preview-and-restore/tools/walk_issue16_history_preview_demo.py \
        --out .scratch/profile-memory-redesign/validation/16-history-preview-and-restore/evidence/demo-walkthrough-<stamp>.txt

（走查约 4 秒，运行库在 `.tmp/issue16-demo-*`；脚本只使用临时库，不读取或
记录任何真实数据；运行目录复核后按 §7 清理。）

## 1. 验收标准对照

| 验收标准（票据） | 证据 |
| --- | --- |
| 预览前后业务数据及 schema 不变，未发起模型请求；重复预览在同一快照上结果一致 | `test_issue16_history_preview.py::test_preview_is_read_only_repeatable_and_never_leaks_text`（库文件 sha256、8 张业务表行计数、抽取运行 0 条、两次清单逐字段相等）；预览路径不发起任何模型请求（不调用模型网关，仅只读 SQL；内容规范化复用自动抽取的公共函数 `normalize_message_content`）；走查 [1] |
| 数据源错误、版本不支持、来源缺失或不能取得一致备份时明确失败，不偷偷升级数据库或选用另一数据库 | `::test_preview_rejects_conflicting_data_source`（配置 vs 显式路径 `database_path_conflict`）；`::test_preview_rejects_old_and_newer_schema_without_upgrade`（83/999 拒绝，旧库读回仍 83）；`::test_preview_rejects_unknown_account`；`::test_backup_requires_fresh_preview_and_quiesced_writer`（写入者持锁 `writer_active`/`writer_state_unknown` 显式失败）；备份不覆盖唯一副本（`::test_backup_refuses_overwrite_and_source_target`）；走查 [2][5] |
| 人工修改、删除屏障、记录开关及材料授权在预览中可核对，不将所有历史默认列为可提取 | 条目分类 12 类计数 + 定位标识：人工保留、自动可核验、部分证据、来源被删除（墓碑）、来源无法核验（非聊天/材料类）、无证据、敏感已授权/未授权、凭据、撤回、被替代（`::test_preview_classifies_items_chat_scope_controls_and_barriers`）；`unverified_source_refs` 列出待重新核验的来源标识（材料类不默认可提取，供 17 按合同逐项核验）；记录开关与用途开关（`::test_preview_honours_recording_off_and_usage_switch`）；`scope_note` 明示材料类来源必须按各自合同重新核验；走查 [1][6] |
| 预览范围绑定账户与来源版本，不能跨账户引用或通过改参数绕过范围限制 | `::test_preview_is_account_scoped_and_digest_binds_account`（Alice/Bob 条目与会话互不可见、摘要不同）；`::test_tampered_manifest_account_fails_closed`（篡改清单账户在执行端硬门失败）；CLI 无扩大范围的参数，范围只由清单账户与当前快照决定 |
| 备份实际恢复后，核对事实、证据、控制、删除屏障及必要的领域引用；只验证文件存在不算通过 | `::test_backup_creates_verified_record_with_isolated_restore_drill`（隔离副本实际打开：integrity ok、state_digest 一致、43 个对账字段全配平；记录写 `<target>.history-record.json`）；`::test_restore_drill_detects_drifted_backup_rows`（内容不配平 → `backup_drill_failed`）；只查文件存在的旁路不存在（恢复演练必须打开并计算摘要）；走查 [4][5] |
| 预览后新增消息或修改来源能被识别，明确要求重算受影响范围 | `::test_verify_detects_new_message_source_edit_and_control_change`（新增消息 / 修改消息内容 / 控制变化均判过期，deltas 给出范围）；`verify --require-fresh` 过期时退出码 2（`test_issue16_history_preview_cli.py::test_cli_missing_account_and_stale_manifest_fail_explicitly`）；`backup` 拒绝过期清单（`::test_cli_backup_rejects_stale_manifest`、`::test_backup_requires_fresh_preview_and_quiesced_writer`）；走查 [3][5] |
| 交付演练命令、预期结果、失败恢复及数据清理步骤；不假设旧迁移命令天然支持新模型 | §7 演练手册（CLI 命令、逐旅程预期、失败恢复、清理）；旧 schema 一律 `schema_upgrade_required` 拒绝且不隐式迁移（走查 [2] 实测读回仍 83），本票不提供“迁移即支持重整”的任何路径 |

## 2. 确定性走查（最终代码，20261016T050022Z，全部通过）

合成旧库：Alice 主账户（人工/自动/材料来源/部分证据/墓碑来源/无证据/敏感
已授权与未授权/凭据/撤回/被替代共 11 条条目 + 8 条消息含墓碑、未完成、
内容禁止、撤回）与 Bob 隔离账户；另有反馈 1 条、内容禁止 1 条、墓碑 1 条。

1. **只读预览**：重复预览逐字段一致；库文件 sha256 与 5 张业务表行计数不变、
   抽取运行 0 条；条目 12 类分类、定位标识与待核验来源标识符合合成库；
   聊天范围可用 2/6（墓碑/未完成/禁止记录/撤回各 1）；控制与领域引用可
   核对；清单不含 8 段私人原文中的任何一段；Bob 与 Alice 互不可见。
2. **显式拒绝**：配置与显式路径冲突、旧 schema（读回仍 83）、未来 schema、
   未知账户全部硬门失败。
3. **漂移检测**：未变化为新鲜；新增消息 `user_messages 6→7`、
   `eligible 2→3` 并给出重算范围；修改来源内容、控制变化均判过期；
   重新预览后新清单重新新鲜。
4. **备份与隔离开演**：备份 1,695,744 字节、schema 84；备份文件 +
   `.manifest.json` + `.history-record.json` 齐备；演练实际打开
   （integrity ok、state_digest 一致、43 个对账字段全配平）；
   `quiesced_sqlite_online_backup` 与恢复所需版本已记录；演练工作目录清空；
   同一备份可重复演练。
5. **失败恢复**：过期清单拒绝创建备份且不写出任何文件；内容漂移的备份
   演练显式失败 `backup_drill_failed`；缺 manifest 的孤儿副本拒绝恢复
   `backup_unverified`；原始备份摘要不变且仍可再次通过演练。
6. **记录开关与来源范围**：账号级暂停记录后 `eligible=0`、4 条可读消息全部
   计入 `skipped_recording_off`；用途开关与版本号可见；材料类（非聊天）
   来源条目落入 `automatic_unverified_source`，`scope_note` 拒绝默认提取。

## 3. 自动化测试

- 新增 `tests/profiles/test_issue16_history_preview.py`（14 项）：只读与
  隐私、12 类分类与定位、记录/用途开关、账户绑定与摘要绑定、未知账户、
  旧/新 schema 拒绝不升级、路径冲突、来源漂移（新增/修改/控制）、备份
  记录与隔离开演、拒绝覆盖与 source==target、`preview_stale` 与写入者
  显式失败、漂移备份演练失败、清单往返与版本门、篡改清单失败关闭。
- 新增 `tests/profiles/test_issue16_history_preview_cli.py`（3 项）：
  CLI 全流程 preview→verify→backup→restore-verify；缺失账户/过期清单
  退出码 2 与 stderr JSON；backup 拒绝过期清单。
- 聚焦复跑：
  - `pytest tests/profiles/test_issue16_history_preview.py tests/profiles/test_issue16_history_preview_cli.py -q` → **17 passed**。
  - `pytest tests/profiles tests/storage tests/lifecycle -q -p no:xdist` → **894 passed**（评审修复后复跑 152.59s）。
  - `pytest tests/profiles/test_profile_v2_safe_replay.py tests/integration/test_cli_contract.py` → 11 passed（CLI 合同含新子命令登记，全批 28 passed）。
- 全量 pytest（分支与 main 同命令同参数
  `python -m pytest -q -n 4 --ignore=tests/career_plan`，与 09 票记录同一跑法）：
  - 分支：**327 failed / 5598 passed / 39 skipped / 0 errors**（8:27；评审修复后复跑 7:27，结果与差集一致）。
  - main（基点 `fff98a9d`）：**327 failed / 5535 passed / 37 skipped / 48 errors**（8:52）。
  - 失败/错误节点差集：**分支独有 0**；main 独有 48 项全部为
    tests/speech、tests/search、tests/resources、tests/security、
    tests/closeout 的环境性 setup 错误。单独重放该批：main 3 failed /
    68 passed（其中 `tests/closeout/test_api_boot.py` 两项为本项目 06/08/09
    票已记录的既有 flaky，`tests/resources` 一项网络禁项为两侧同现的
    顺序敏感 flaky）、分支 1 failed / 70 passed（同一 resources flaky，
    对应 48 项中 45 项重放转通过）。通过数差 5598−5535 = 48（main 环境
    错误）+17（本票新增）−2（跳过差）。
  - `tests/career_plan` 收集错误（`CareerBackgroundLoader` ImportError）两侧
    一致，与本票无关。

## 4. 静态检查

- `mypy src`：分支与 main 同为 **141 errors / 23 files**，按路径归一化后
  诊断集合双向 diff 为空（本票新增源文件 0 错误）。
- `ruff check .`：分支与 main 同为 **445 errors**，归一化集合完全一致
  （本票新增源文件、测试与走查工具 0 诊断）。
- `pip check`：No broken requirements found。

## 5. 接口与实现摘要

- 新模块 `history_contracts.py`：`HISTORY_PREVIEW_VERSION =
  "profile-history-preview-v1"`；清单只含计数、定位标识与摘要（无正文）。
  结构：`ItemEvidenceSummary`（12 类计数 + 按类定位标识 + 待重新核验的
  来源标识 `unverified_source_refs`）、
  `ChatScopeSummary`（可用范围、时间窗、会话、6 类跳过原因）、
  `ControlsSummary`（记录/用途开关与版本、内容禁止、墓碑计数）、
  `DomainRefsSummary`（反馈、旧记录引用与未解析数、迁移台账）、
  `HistoryPreviewManifest`（账户、来源路径/schema/来源摘要/`state_digest`/
  `writer_state`/范围说明）；未知 `manifest_version` 读取时显式失败。
- 新模块 `history_repository.py`：只读 SQL 边界与记录视图（不复用会
  初始化/写入的仓库），提供账户、条目、消息、控制、反馈与领域引用的
  只读查询；损坏 JSON 字段按空处理（证据不足方向，绝不伪造）。
- 新模块 `history_snapshot.py`：账户快照计算。`state_digest` 逐消息/条目/
  控制/反馈/迁移台账计算（消息正文只取 sha256、引文只取摘要），任一来源
  变化都会改变摘要；条目分类：`manual_preserved`、`automatic_verifiable /
  partial_evidence / deleted_evidence（墓碑来源）/ unverified_source
  （非聊天/材料类）/ missing_evidence`、`sensitive_authorized /
  unauthorized`、`credentials_flagged`、`withdrawn`、`superseded`；
  `unverified_source_refs` 汇总该类条目的来源标识。
- 新模块 `history_preview.py`：`preview_history`（只读事务内形成快照，
  不隐式迁移）；`verify_preview_manifest`（漂移报告 + `requires_recompute`
  + 受影响范围）；`save_preview_manifest` / `load_preview_manifest`
  （JSON 往返与版本门）。
- 新模块 `history_backup.py`：`create_history_backup`（先校验清单新鲜度 →
  `create_verified_backup` → 隔离开演 → 写
  `<target>.history-record.json`，失败也写记录）；`verify_history_restore`
  （对已有备份重复演练）；`_run_restore_drill`（临时副本 → integrity_check
  → 实际打开 → `state_digest` + 43 字段逐项对账）；两入口共用
  `_ensure_reconciled` 硬门，坏备份不会被当作可用。
- 新 CLI `bridges/cli/profile_history.py`：`BridGes profile-history
  preview|verify|backup|restore-verify`；全部输出 JSON，硬门失败 stderr
  `{"error": {"code", "message"}}` + 退出码 2；`verify --require-fresh`
  过期时退出码 2。错误码：`account_missing`、`schema_upgrade_required`、
  `schema_version_unsupported`、`schema_version_missing`、
  `database_path_conflict`、`database_missing` / `database_not_file`、
  `preview_stale`、`backup_drill_failed`、`backup_unverified`、
  `manifest_invalid` / `manifest_version_unsupported`、
  `backup_target_exists` / `backup_target_conflict`、`writer_active` /
  `writer_state_unknown`（Windows 持锁时探测为后者，同为显式失败）。
- 复用：`replay.py` 的权威来源解析、在线备份与 manifest、只读连接、
  schema 读取、完整性检查、写入者探测；`automatic.py` 的内容规范化由
  私有 `_normalize` 提升为公共 `normalize_message_content`（本包内 19 处
  调用同步更新），预览与自动抽取使用同一函数，避免匹配语义漂移；本模块
  不是旧 `profile-auto-v2` 回放命令，也不是工单 17 的执行入口。
- **17 接入说明（本票边界）：** 17 执行前必须 `verify_preview_manifest`
  并以 `requires_recompute` 决定是否重算；预览不是永久授权，不得把清单
  当作被遗忘内容的恢复许可；执行时应先 `create_history_backup` 留恢复
  前提（记录文件含恢复所需版本）；凭据类条目（`credentials_flagged`）
  永不进入可提取集合；`withdrawn`/`superseded`/墓碑来源（
  `automatic_deleted_evidence`）不得复活；材料类来源（
  `automatic_unverified_source`）必须在 17 按工单 10 的授权合同重新核验
  后才可提取。按清单 `account_id` 锁定范围，禁止扩大或跨账户引用。

## 6. 演练手册（命令、预期、失败恢复、清理）

CLI（仓根执行，worktree 内须先 `$env:PYTHONPATH='src'`）：

    python -m bridges.cli.main profile-history preview --account <账户> --out <目录>/preview.json
    python -m bridges.cli.main profile-history verify --manifest <目录>/preview.json --require-fresh
    python -m bridges.cli.main profile-history backup --manifest <目录>/preview.json \
        --target <目录>/backup/history.db --work-dir <目录>/drill
    python -m bridges.cli.main profile-history restore-verify --manifest <目录>/preview.json \
        --backup <目录>/backup/history.db --out <目录>/restore.json

预期：四条命令输出 JSON 并退出码 0；`preview.json` 只含计数与标识；备份
命令写出 `history.db`、`history.db.manifest.json`、
`history.db.history-record.json`，其中 `restore.reconciliation_passed=true`、
`restore.opened=true`。任一步硬门失败：退出码 2，stderr 输出错误码 JSON。

失败恢复：

- `preview_stale`：来源在预览后变化。重新 `preview` 生成新清单，再执行；
  旧清单不得继续用于备份或 17 执行。
- `backup_drill_failed` / `backup_unverified`：该备份不可信，不得使用；
  原始备份与来源均未被覆盖。检查来源是否被并发写入或备份被修改，
  重新预览后重建备份。
- `writer_active` / `writer_state_unknown`：数据目录写入者尚未静止
  （Windows 持锁时探测为 unknown），此为本设计的显式失败；待写入者停止
  重试，不改进绕过。
- `schema_upgrade_required`：库低于当前程序版本。按项目迁移流程显式
  升级后再预览；预览与备份都不会隐式迁移。
- `database_path_conflict`：运行配置与显式路径不一致。确认权威实例后
  只保留一个来源，禁止猜测。

清理：走查/演练产物都在临时目录（CLI 自定 `<目录>` 同上）：

    Remove-Item -LiteralPath <运行目录> -Recurse -Force

删除前确认没有待复核的恢复记录；备份与记录文件仅含合成数据时可一并删除。

## 7. 剩余限制与边界

- 预览按当前 schema 可读数据计算：材料类（非聊天）来源在 10 票接入前
  一律计入 `automatic_unverified_source` 并列出待核验来源标识
  （`unverified_source_refs`）；材料授权的具体语义仍由 10 的合同定义，
  预览只保证它们不默认可提取。
- 来源绑定语义：清单与校验以账户内容状态 `state_digest` 为绑定依据；来源
  文件路径与文件级摘要记录在清单中供审计。内容完全相同的副本（如异地
  恢复的实例）被视为同一逻辑来源而通过校验；选用另一个数据库由运行时
  唯一权威解析（配置与显式路径冲突即拒绝）在入口处防止，预览/校验不
  提供任何换库参数。
- `writer_state` 在 Windows 持锁时无法区分持续写入者，返回
  `writer_state_unknown` 显式失败（与 Linux 的 `writer_active` 同为硬门）。
- 走查为确定性合成库；真实用户库的规模、WAL 大小未做性能基准。
- 本票不执行生产数据重整、不停止用户运行实例、不把预览许可解释为恢复
  被遗忘内容的许可（票据「明确不做」）。
- 演练临时副本以 `shutil.copyfile` 创建（不复制 `-wal`）：备份由在线备份
  产生，WAL 已合并；若未来备份方式改为不 checkpoint 的文件拷贝，须同步
  更新演练复制方式。
