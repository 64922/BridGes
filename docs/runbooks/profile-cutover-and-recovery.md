# 画像四维路径退出、切换与恢复运行手册

适用画像工单 19、ADR-0043 与 `schema=89+` 的本地 SQLite 权威库。本文给出
实际命令、前置状态、校验和失败定位；**代码交付、数据迁移、实际部署是三个
不同证据，本文通过不代表已经操作真实用户实例或完成生产发布。**

## 1. 范围与硬边界

- 原子画像条目是长期记忆唯一权威。自动抽取、提交、切片和页面不再把四类
  维度当门槛；四维表只保留旧外部路由、存量迁移来源与历史读取。
- 本票不删除旧四维存量记录，也不把新主题强行投影回四类。旧记录只读保留，
  由迁移台账和审计说明其用途。
- 回滚不是 schema 降级：用切换前的**已验证备份**作底，把当前库切换后的
  画像状态合并到新目标库，再按本文替换正式库。旧备份不能直接覆盖正式库。
- 恢复合并范围是画像域：原子条目、反馈、使用控制、停止记录屏障、消息墓碑、
  迁移台账、重试记账与历史重整台账；并清空概览、进行中事项和召回索引。
  **聊天消息、文件对象、学习记录等领域数据不在本工具合并范围**，整实例回滚
  仍需使用整库备份与对应领域恢复流程。
- 以切换前备份为界，备份中已存在且仍为 `pending`/`running` 的抽取运行与任务
  不会进入恢复库；切换后新增的活动任务保留。旧外部队列若仍投递这些任务，
  领取方会因任务不存在而完成消息，不再执行旧代抽取。
- 历史重整的运行台账会随画像域恢复，但重整工作副本不在恢复范围；备份中
  未完成的重整不得继续执行，应从当前候选版本重新发起。
- 恢复目标是整库副本，会合并当前库全部账户。切换窗口内不得新建或删除账户；
  `account_set_changed` 会拒绝继续。
- 恢复命令只读当前库，不覆盖正式库。目标路径必须不存在，且不能是当前库或
  备份文件本身。

## 2. 前置状态

以下命令在 conda `agent` 环境执行（先 `conda activate agent`）。

1. 候选提交已通过本票要求的全量检查与前端构建；工单 18 未达标项已回传到
   工单 20，不能把本票当作补齐效果缺口。
2. 已获得维护窗口与操作授权；API、后台执行器、提醒调度器和 Web 写入已停止，
   没有活动写入者。
3. 记录实例路径、候选提交、schema 版本、账户清单、备份路径和操作人。
4. 在本机或隔离环境先执行一次演练脚本：
   ```powershell
   python scripts/run_issue19_cutover_drill.py --strict
   ```
   确认输出 `passed: true`；证据写入
   `.scratch/profile-memory-redesign/validation/19-cutover-retirement-and-recovery/`。
5. 后端检查（以仓库当前配置为准）：
   ```powershell
   python -m pip check
   python -m pytest -q
   python -m mypy src
   ruff check .
   ```
   前端在 `apps/web` 下：
   ```powershell
   npm run lint
   npm run typecheck
   npm run test:unit
   npm run build
   ```
   既有失败必须与 `main` 基线对照并记录，不能用本票改动掩盖。

## 3. 切换前审计

对每个账户执行只读审计：

```powershell
BridGes profile-replay inspect --db <权威库路径>
BridGes profile-cutover audit --account <账户标识> --db <权威库路径>
```

必须满足：

- `schema_current=true`，`sqlite_integrity=ok`；
- `account_present=true`；
- `withdrawn_items_have_no_body=true`（墓碑正文必须为空）；
- `atomic_identity_complete=true`（原子条目身份键存在）；
- 记录 `legacy_records`、`atomic_items`、`atomic_tombstones`、
  `pending_retry_tasks`、`migration_runs` 的实际计数。

审计失败时保持只读，不切换。`withdrawn_items_have_no_body` 失败说明墓碑仍
保留正文，必须由维护者评估并用受控修复流程处理；不要直接用 SQL 修改正式库。

## 4. 创建已验证备份

停止所有写入者后执行：

```powershell
BridGes profile-replay backup --target <备份目录>/cutover-pre.db --db <权威库路径>
```

`profile-replay backup` 会获取维护锁、创建 SQLite 在线备份、写入 manifest，
并做完整性探针。确认 manifest 与备份文件在同一目录、摘要和 schema 可读；
备份文件保持只读，不要用编辑器或 `sqlite3` 手工修改。

## 5. 切换

1. 部署包含本票 `profile-cutover` 命令的候选版本。
2. 启动 API/worker/scheduler，确认启动日志没有 schema 完整性失败。
3. 重新执行第 3 节审计；确认所有账户通过。
4. 冒烟检查：
   - 打开画像页，能读取原子条目、概览、进行中事项与学习引用；
   - 发起一轮普通对话，确认画像说明与采用记录正常；
   - 手动记住、修改、删除一条开放事实，确认下一轮不复活；
   - 关闭“长期画像使用”后确认回答不再注入画像正文，再恢复开关；
   - 删除一条消息来源后确认相关条目依据不再展示。
5. 检查 worker：待处理提取任务能推进或按墓碑/控制/版本原因耗尽；日志中
   不应出现新的四维写入路径。静态审计由
   `tests/profiles/test_issue19_cutover_retirement.py` 的
   “活跃调用链”测试持续覆盖。

## 6. 切换后验证

- 再执行一次第 3 节审计，并保留 JSON 输出。
- 运行确定性演练脚本（不接触正式库）：
  ```powershell
  python scripts/run_issue19_cutover_drill.py --strict
  ```
  重点检查：
  - `retry_after_tombstone_stays_deleted=true`；
  - `open_fact_written_to_atomic_only=true`；
  - `recovery_passed=true`；
  - `reupgrade_audit_passed=true`；
  - `post_cutover_changes_survive=true`；
  - `deletion_barrier_survives=true`；
  - `controls_survive=true`；
  - `legacy_records_are_not_rewritten=true`；
  - `old_extraction_queue_not_resumed=true`。

## 7. 故障回滚

触发条件：候选版本出现无法在维护窗口内修复的画像写入、读取或 schema
问题。回滚前再次停止所有写入者，保留当前库和现场日志。

1. 确认使用第 4 节的已验证备份，且备份 manifest 未被移动或修改。
2. 生成恢复目标（**新路径，不能指向正式库**）：
   ```powershell
   BridGes profile-cutover recover `
     --backup <备份目录>/cutover-pre.db `
     --target <恢复目录>/bridges-recovered.db `
     --db <权威库路径>
   ```
   默认输出全部账户报告；只想看指定账户报告时追加
   `--account <账户标识>`，但实际合并仍覆盖全部账户。
3. 输出必须 `passed=true`，并逐项确认：
   - `current_items_preserved`：切换后新增/修改的条目都在目标库；
   - `backup_only_items_retained`：备份中、当前库已物理缺失的条目被找回；
   - `controls_match_current`、`privacy_blocks_match_current`：控制与屏障
     以当前库为准；
   - `tombstones_preserved`、`migration_ledger_preserved`：删除屏障和迁移
     台账保留；
   - `extraction_state_matches_current`：切换前活动队列未复活，切换后活动
     运行/任务未丢失；
   - `derived_views_invalidated`：概览、事项上下文、召回索引已清空。
4. 对恢复目标再做一次只读审计：
   ```powershell
   BridGes profile-cutover audit --account <账户标识> --db <恢复目录>/bridges-recovered.db
   ```
5. 替换正式库前确认服务已停止，并处理 SQLite sidecar：
   ```powershell
   Move-Item -LiteralPath <权威库路径> -Destination <备份目录>/bridges-pre-rollback.db
   Move-Item -LiteralPath <权威库路径>-wal -Destination <备份目录>/bridges-pre-rollback.db-wal -ErrorAction SilentlyContinue
   Move-Item -LiteralPath <权威库路径>-shm -Destination <备份目录>/bridges-pre-rollback.db-shm -ErrorAction SilentlyContinue
   Copy-Item -LiteralPath <恢复目录>/bridges-recovered.db -Destination <权威库路径>
   ```
   不要删除旧库；它是回滚证据。若部署环境是 Linux，使用对应 `mv`/`cp`，
   但顺序和“先停写入、保留旧库、再替换”的约束不变。
6. 启动服务，执行第 3、5、6 节检查。若恢复目标核对失败，**不要替换正式库**；
   保持当前服务停止或恢复原库，记录失败 check_id。

## 8. 重升级

回滚后的目标库仍是当前 schema；重升级步骤：

1. 使用包含本票的候选版本启动，先执行：
   ```powershell
   BridGes profile-replay inspect --db <权威库路径>
   BridGes profile-cutover audit --account <账户标识> --db <权威库路径>
   ```
2. 如果目标库 schema 低于当前程序（例如恢复的是更早的整库备份），只在新副本
   上执行显式升级：
   ```powershell
   BridGes profile-replay schema-upgrade --db <副本路径>
   ```
   然后把升级后的副本重新做成已验证备份，再走第 7 节恢复流程。**不支持**
   直接把 schema 元数据改成旧版本，也不支持对正式库做降级迁移。
3. 重升级后重新验证：新增事实、人工修订、控制、消息墓碑、迁移台账和派生
   视图重建结果一致；再次运行第 6 节演练脚本。
4. 若重升级后出现旧事实复活、人工修改被覆盖或墓碑正文回流，立即停止写入，
   按第 7 节回到最近已验证目标，并保留失败证据。

## 9. 失败定位

| 错误码/现象 | 含义 | 处置 |
| --- | --- | --- |
| `writer_active` | 仍有活动写入者 | 停止 API/worker/scheduler，取得维护锁后重试 |
| `schema_version_mismatch` | 当前程序与库 schema 不一致 | 使用匹配版本；不要手改 `schema_meta` |
| `database_missing` / `current_database_missing` | 路径错误或库未挂载 | 核对 `--db` 与运行目录，不要新建空库 |
| `target_exists` | 恢复目标已存在 | 选择新的目标路径；不覆盖旧恢复结果 |
| `target_parent_missing` | 目标目录不存在 | 显式创建目标目录后重试 |
| `backup_unverified` / `backup_digest` 不一致 | 备份缺失、manifest 不匹配或文件被改动 | 重新创建已验证备份，不使用可疑备份 |
| `backup_schema_invalid` / `backup_schema_mismatch` | 备份 schema 与当前库不同 | 在副本上显式升级后重新备份，再恢复 |
| `account_missing` | 账户标识或库不对 | 核对账户清单与数据库路径 |
| `account_set_changed` | 切换窗口内新建/删除账户 | 恢复该账户或重新制作备份；不得强行继续 |
| `cutover_recovery_unverified` | 目标库合并后核对失败，目标已删除 | 查看失败 check_id；不要替换正式库 |
| `withdrawn_items_have_no_body=false` | 墓碑仍保留正文 | 停止切换，交维护者按受控修复流程处理 |
| `atomic_identity_complete=false` | 原子条目身份键缺失 | 停止切换，先核对迁移/重整结果 |
| 派生视图未清空 | 恢复后仍读到旧概览/索引 | 说明恢复未完成；不要启动服务 |

普通日志和验证记录不得保存画像正文、消息原文、凭据或真实用户材料；报告只
保留条目/账户标识、计数、摘要和检查结论。

## 10. 交付证据与边界

每次真实切换或回滚至少保存：

- 候选提交、schema 版本、账户清单和操作时间；
- 第 3 节审计 JSON、第 4 节备份 manifest 与摘要；
- 第 7 节恢复报告 JSON、第 8 节重升级审计 JSON；
- 执行过的命令、执行人、失败 check_id 和最终决定；
- 是否真的替换了正式库、是否启动服务和是否完成用户侧冒烟。

本手册和代码通过检查不等于已生产部署。真实用户实例的停启、迁移和部署必须
按既有授权和操作流程执行；无法满足恢复目标时保留阻塞，不宣称“可回滚”。
