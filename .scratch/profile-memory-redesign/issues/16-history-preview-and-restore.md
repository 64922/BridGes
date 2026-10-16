# 16 — 提供历史重整的只读预览与可恢复备份

**What to build:** 维护者能够确认实际使用的数据源，预览可重整的旧画像与聊天范围，并在隔离副本中验证备份确实可恢复，再决定是否执行重整。

**Blocked by:** [07 — 时间与当前状态](07-time-and-current-state.md)、[09 — 明确授权的敏感记忆](09-explicit-sensitive-memory.md)。

**Status:** ready-for-agent

**发布状态：** 2026-10-15 用户已确认拆分与依赖，正式发布；尚未实施。

**对应合同：** D07；T11、T17、T19。参见[定稿](../PRD.md)、[共同执行约束](../README.md)。

## 用户演示

维护者选择运行实例的数据源，获得只读预览：哪些人工条目会保留，哪些自动条目有证据，哪些历史因删除、不记录或缺乏授权而跳过。随后创建独立备份，在隔离实例恢复并核对结果。

## 依赖与实施范围

07 和 09 确定历史时间与授权语义。本票提供 17 的输入和恢复前提；预览本身不调用提取模型。

- 从实际运行配置确认权威数据库、schema 版本、实例与潜在写入者，不猜测根目录数据库就是生产数据。
- 形成带版本的预览清单，区分人工事实、可验证自动事实、缺失证据、删除/禁止记录范围、敏感信息和未授权材料。
- 保留消息原始时间、来源版本与范围；输出聚合统计和定位标识，避免将私人原文写入诊断日志。
- 备份不得覆盖唯一副本；记录一致性取得方式、校验结果及恢复所需版本。隔离恢复演练必须实际打开并验证数据。
- 检测预览后源数据变化；17 执行时必须重新校验，预览不是永久授权快照。

## 验收标准

- [x] 预览前后业务数据及 schema 不变，未发起模型请求；重复预览在同一快照上结果一致。
- [x] 数据源错误、版本不支持、来源缺失或不能取得一致备份时明确失败，不偷偷升级数据库或选用另一数据库。
- [x] 人工修改、删除屏障、记录开关及材料授权在预览中可核对，不将所有历史默认列为可提取。
- [x] 预览范围绑定账户与来源版本，不能跨账户引用或通过改参数绕过范围限制。
- [x] 备份实际恢复后，核对事实、证据、控制、删除屏障及必要的领域引用；只验证文件存在不算通过。
- [x] 预览后新增消息或修改来源能被识别，明确要求重算受影响范围。
- [x] 交付演练命令、预期结果、失败恢复及数据清理步骤；不存在“旧版迁移命令天然支持新模型”的假设。

## 验证与交付

在合成旧库及隔离副本演示只读预览、错误数据源拒绝、备份恢复与源版本变化。交付预览清单合同、备份恢复记录、重整范围判定和 17 接入说明。

## 明确不做

不执行生产数据重整，不停止用户运行实例，不把预览许可解释为恢复被遗忘内容的许可。

## Comments

2026-10-15：用户回复「按此发布」，确认本次拆分与依赖。已发布为 ready-for-agent，尚无实施记录。

2026-10-16：实施与验证完成（分支 `codex/issue-16-history-preview-and-restore`，基点 `fff98a9d`）。

- 交付：只读预览合同 `history_contracts.py`（清单版本 `profile-history-preview-v1`，只含计数、定位标识与摘要，不含正文）；只读读取边界与快照计算 `history_repository.py`（`state_digest` 绑定消息/条目/控制/反馈/迁移台账；条目 12 类：人工保留、自动可核验/部分证据/来源被删除（墓碑）/来源无法核验（材料类）/无证据、敏感已授权/未授权、凭据、撤回、被替代）；编排层 `history_preview.py`（`preview_history`、`verify_preview_manifest` 漂移报告、`create_history_backup`（新鲜度门 → 在线备份 → 隔离开演 → 恢复记录 `<target>.history-record.json`）、`verify_history_restore`（可重复演练））；CLI `BridGes profile-history preview|verify|backup|restore-verify`（JSON 输出；硬门失败 exit 2 + stderr 错误码）。
- 测试：新增 17 项（`tests/profiles/test_issue16_history_preview.py` 14 项、`tests/profiles/test_issue16_history_preview_cli.py` 3 项）；聚焦 `pytest tests/profiles tests/storage tests/lifecycle` 894 通过；全量对 main（同命令 `-n 4 --ignore=tests/career_plan`）327 failed / 5598 passed vs 5535 passed，逐条差集分支独有失败 0（main 独有 48 项为 speech/search/resources/security 环境性 setup 错误，重放 45 项转通过，其余为 06/08/09 已记录的 `test_api_boot` flaky）；`tests/career_plan` 收集错误两侧一致。
- 走查（确定性合成库，6 旅程全部通过）：只读预览可重复且库摘要/行计数不变、无抽取运行、清单不含原文；路径冲突/旧新 schema/未知账户显式拒绝且旧库读回仍 83；新增消息/修改来源/控制变化判过期并可重新预览；备份实际打开对账 43 字段、记录写出、可重复演练；过期清单拒绝备份、坏副本 `backup_drill_failed`/`backup_unverified`、原备份不受影响；暂停记录后 eligible 归零、材料类来源不默认可提取。
- 静态：`mypy src` 141=141、`ruff check .` 445=445 与 main 归一化集合一致（新增文件 0 诊断）；`pip check` 通过。
- 验证记录：`.scratch/profile-memory-redesign/validation/16-history-preview-and-restore/verification-record.md`（含演练命令、逐旅程预期、失败恢复与清理步骤、17 接入说明）；走查证据 `.../evidence/demo-walkthrough-20261016.txt`，脚本 `.../tools/walk_issue16_history_preview_demo.py`（可复跑）。
- 已知限制：材料类来源在 10 票接入前一律计入「来源无法核验」并列出待核验来源标识（不默认可提取），授权语义仍由 10 的合同定义；Windows 持锁时写入者探测为 `writer_state_unknown`（与 `writer_active` 同为硬门）；不执行生产重整、不停止运行实例、预览不构成恢复许可，详见验证记录 §7。

2026-10-16（评审修复）：两轴代码评审（Standards + Spec）后修复并复验：`history_preview.py`（原 653 行）拆为 `history_preview.py` + `history_backup.py`，`history_repository.py`（原 507 行）拆为 `history_repository.py` + `history_snapshot.py`（均低于 500 行）；`automatic._normalize` 提升为公共 `normalize_message_content`（消除跨模块私有依赖，匹配语义与自动抽取保持同一实现）；`ItemEvidenceSummary` 新增 `unverified_source_refs` 列出待重新核验的来源标识；删除未使用的 `HistoryPreviewManifest.save/load` 类方法；备份与独立恢复演练共用 `_ensure_reconciled` 硬门。复验：17 项新测试与 `tests/profiles tests/storage tests/lifecycle` 894 项通过；全量复跑 327 failed / 5598 passed / 39 skipped，差集仍为分支独有 0；`mypy src` 141=141、`ruff check .` 445=445 与 main 归一化一致；走查 6 旅程全部通过（证据已更新）。
