# 17 — 可恢复地重整旧画像并补提取合法历史

**What to build:** 在已验证的备份与授权范围内，重整旧画像和合规历史消息；维护者能查看进度、暂停并恢复，用户继续保有人工修改、删除和最新信息的优先权。

**Blocked by:** [10 — 指定材料](10-requested-material-memory.md)、[15 — 概览与主题浏览](15-overview-and-topic-browsing.md)、[16 — 历史预览与恢复](16-history-preview-and-restore.md)。

**Status:** ready-for-agent

**发布状态：** 2026-10-15 用户已确认拆分与依赖，正式发布；尚未实施。

**对应合同：** D07；T07、T08、T10、T11、T18、T19。参见[定稿](../PRD.md)、[共同执行约束](../README.md)。

## 用户演示

维护者在隔离副本启动预览通过的重整，运行中中断并恢复；查看迁入、修订、去重、跳过及失败数量。用户后来明确修改的偏好保持不变，已删除内容不再出现，概览与检索随有效结果更新。

## 依赖与实施范围

10 确定材料范围，15 提供派生视图，16 提供预览与恢复依据。重用在线提取、验证和提交语义，不建立独立宽松的迁移写入通道。

- 先在隔离副本实现受控执行，依据预览版本重新校验源与控制；来源时间决定事实时间，迁移时间仅记录操作时间。
- 对旧人工条目保留权威，对旧自动条目核查来源；旧四类标签不能充当事实证据。缺失证据明确标记处理结果，不静默伪造来源。
- 历史补提取使用有界来源窗口，按账户、来源版本和策略版本记录进度、幂等标识及失败状态。
- 模型请求在写事务外，提交前再次检查删除屏障、人工修订、版本和记录权限；源变化使旧提案失效。
- 失败不当作成功消费水位，重试有界且可定位；中断恢复不会重复事实或覆盖新信息。
- 新写入触发索引和概览失效/重建；迁移状态自身具有清理、导出、账户删除及恢复语义。

## 验收标准

- [x] 同一批次重复执行和进程崩溃后恢复，最终有效事实及证据关系一致，无重复生效。
- [x] 重整期间人工修改、删除、停止记录和来源修订均能阻止迟到提案；不得复活被遗忘内容。
- [x] 原始消息时间与事实适用时间保留，过期状态不会因本次重整获得新鲜度。
- [x] 未授权材料和敏感历史按既定合同跳过，不因为存在旧标签就自动获得授权。
- [x] 单项失败有可追踪状态，统计分母和失败数真实；不能用“处理完成”掩盖未提取范围。
- [x] 完成后界面、检索和概览一致，派生失败可重建且不泄漏旧事实。
- [x] 对账涵盖人工条目、证据、控制、删除屏障、派生依赖、账户清理和导出；执行恢复演练而非仅备份。
- [x] 实际用户库的执行按现有操作授权与运行手册单独确认，不因工单发布自动执行。

## 验证与交付

使用混合旧数据夹具，演示预览、执行、中断、并发修改、恢复和对账。交付真实运行记录、未处理范围、策略版本、可恢复检查点及维护者手册。

## 明确不做

不批量恢复删除信息，不直接套用旧迁移程序，不为旧四类增加双写兼容路径。

## Comments

2026-10-15：用户回复「按此发布」，确认本次拆分与依赖。已发布为 ready-for-agent，尚无实施记录。

2026-10-16：实施与验证完成（分支 `codex/issue-17-resumable-history-rebuild`，基点 `87e114f5`）。

- 执行模型：隔离工作目录（`rebuild.json`/`preview.json`/已验证备份 `history.db`/工作副本 `working/bridges.db`）内做受控重整；启动先按 16 的 `verify_preview_manifest` 校验新鲜度、`create_history_backup` 留恢复前提，副本重算 `state_digest` 与清单配对后才播种。批次标识 `rebuild_id` 由（账户, 状态摘要, 策略版本, 预览版本, 窗口）确定性派生（`profile-rebuild-*`），幂等键沿用在线（账户, 消息, 抽取器版本, 来源哈希）。
- 复用在线语义：经 `schedule_message_extraction` + `ensure_task_queued`（新增公共方法，`_recover_run` 由启动恢复抽取）进入专用队列 `profile-history-rebuild`，`run_retry_tick` 领取执行，不调用跨账户的 `recover_inflight_runs`，不绕过账户背压（上限 32，拦截条目保持 pending 并如实暂停）。模型调用在写事务外、提交前复核沿用在线屏障；条目 `created_at` 仍为来源稳定记录时间。
- 台账：迁移 89 新增 `profile_rebuild_runs`（版本绑定/窗口/基线 JSON/计数）与 `profile_rebuild_items`（逐条状态、跳过原因、来源哈希、运行与提交标识；只存标识与摘要，不存正文）；计数器由逐条行重算；登记 `REQUIRED_TABLES`、账户删除顺序（先于 `profile_items`）、导出类别 `profile_history_rebuild`（画像历史重整审计）与逻辑摘要。
- 对账与演练（`reconcile_rebuild` 八段）：人工条目摘要（id/version/编辑时间/正文摘要）与撤回不复活、事实键不重复、墓碑迟到成功检测；证据可解析且非墓碑（既有缺证据按 16 基线标记、仅新增悬挂引用失败）、台账与运行终态一致；控制与基线一致；概览失效重建一致、召回索引陈旧仅警告；逐类别逐表导出 + 审计类别登记；副本上账户删除零残留；工作副本重新预览→备份→恢复演练 + 逐表逻辑摘要一致；权威来源前进只记警告。报告写 `reconciliation.json`，仅通过写 `reconciled_at`；未收敛拒绝对账。
- CLI：`BridGes profile-rebuild start|run|status|reconcile|cleanup`（JSON 输出；硬门失败 stderr 错误码 JSON + 退出码 2；`run` 收 `--max-ticks`（模型调用上限，空闲 tick 提前收敛）；`--start-at/--end-at` 有界窗口；`run`/`reconcile` 装配 `ExtractorFactory`：测试环境规则抽取器、生产网关）。
- 测试：新增 19 项（`tests/profiles/test_issue17_history_rebuild.py` 9、`_reconcile.py` 5、`_cli.py` 3、`tests/storage/test_schema_v89_profile_rebuild.py` 2）；聚焦 `pytest tests/profiles -q` 798 通过、17/16/88/89 组合 57 通过；全量与 main 同命令（`-q --ignore=tests/career_plan`）327 failed / 5732 passed / 39 skipped vs main 329 failed / 5713 passed / 37 skipped，失败集合为 main 既有（含 MCP/插件/编排等环境性失败，抽查 86 failed 同族），`tests/career_plan` 收集错误两侧一致。
- 静态：`mypy src` 141=141（本票新文件 0 错误）；`ruff check .` 453=453（本票文件 0 诊断）；`pip check` 通过。
- 走查（确定性合成库，25/25 通过）：.scratch/profile-memory-redesign/validation/17-resumable-history-rebuild/{verification-record.md,evidence/demo-walkthrough-20261017.txt,tools/walk_issue17_history_rebuild_demo.py}。
- 已知限制（详见验证记录 §7）：走查为规则抽取器确定性验证（生产网关未实测）；既有缺证据按标记对待、仅新增悬挂引用失败；`rebuild.json` 与台账同时被篡改属环境破坏场景；背压拦截不丢弃；真实用户库切换按运行手册单独确认。
