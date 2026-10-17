# 四维路径退出与切换恢复演练（2026-10-17T06:09:25+00:00）

- 代码提交：d0845b813b3a58a87cc9833bacf89591be1d137f
- 运行命令：`python scripts/run_issue19_cutover_drill.py --strict`
- 环境：Python 3.11.15 / Windows-10-10.0.26200-SP0 / conda `agent`
- 运行目录：.tmp\issue19-cutover-20261017T060922Z-c1eb9e
- 备份：profile-replay-backup-3f7fc2f3edcd0ebff4e3c6fbeda80685
- 恢复核对：通过

## 判定

- [通过] retry_after_tombstone_stays_deleted：墓碑后的待处理任务被耗尽，未写入新条目
- [通过] open_fact_written_to_atomic_only：开放事实写入原子条目，四维记录计数未增加
- [通过] recovery_passed：恢复库核对全部通过
- [通过] reupgrade_audit_passed：恢复库重升级审计通过
- [通过] post_cutover_changes_survive：新增事实、人工修订与备份独有事实都在恢复库中
- [通过] deletion_barrier_survives：删除条目的墓碑状态和空正文都保留
- [通过] controls_survive：切换后的画像使用控制保留
- [通过] legacy_records_are_not_rewritten：四维存量记录只读保留，没有新增写入
- [通过] old_extraction_queue_not_resumed：切换前活动运行/任务已移除，切换后新增运行/任务保留
- [通过] profile_queue_claims_rebuilt：画像队列领取行只保留切换后活动任务

## 候选版本检查

- `pytest tests/profiles -q`：811 passed（85.31s）。
- `pytest -q --ignore=tests/career_plan --tb=no -rf`：328 failed / 5752 passed / 37 skipped（1611.18s）。
  - 失败集中在 MCP、插件、编排、职业/资源模块、退役路由等既有基线族；本票改动的 `tests/profiles` 全绿。
  - `tests/integration/test_issue16_profile_contract.py::test_four_dimension_public_contract_keeps_only_read_modify_withdraw` 已在基线 `56f158ad` 单独复现失败，不是本票引入。
  - `tests/career_plan` 因基线 `CareerBackgroundLoader` 导入失败而排除；该目录在基线同样无法收集。
  - 工单 17 的旧全量基线为 328 failed / 5732 passed / 39 skipped；本候选新增 20 个测试通过、2 个跳过差异，失败数一致，未发现本票新增失败。
- `pytest tests/integration -q`：3 failed / 277 passed / 26 skipped；失败为 gq08 golden path、issue16 四维合同、media retirement 的既有基线项。
- 前端 `npm run lint`、`npm run typecheck`、`npm run test:unit`、`npm run build` 全部通过；单测 37 个文件 / 320 个用例。
- `pip check` 通过；`ruff check` 本次改动文件通过；`mypy src` 维持基线 141 errors，本票改动未新增类型错误。

## 边界

- 本演练使用合成数据库，不包含真实用户数据。
- 覆盖审计、已验证备份、切换后写入、整库画像域恢复、重升级审计、旧队列边界、墓碑/控制保留和四维存量只读。
- 不覆盖旧生产队列实例的重试、真实模型调用或整库聊天/文件/学习记录回滚。
- 重升级是恢复库的 schema/合同审计与 `BridgesDatabase.initialize()` 启动检查，不重放全部历史 schema 迁移脚本。
- 退出审计是只读数据前提检查加源码/前端合同测试，不是对全部提示词、检索与后台路径的运行时枚举。
