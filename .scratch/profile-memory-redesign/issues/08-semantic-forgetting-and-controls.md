# 08 — 阻断忘掉信息和关闭个性化后的间接回流

**What to build:** 忘掉或关闭个性化后，旧聊天、同义新提及、后台任务和派生内容不能绕路再次使用该信息；明确恢复只恢复指定范围。

**Blocked by:** [06 — 事实身份与修订](06-revision-conflict-and-deduplication.md)。

**Status:** ready-for-agent

**发布状态：** 2026-10-15 用户已确认拆分与依赖，正式发布；尚未实施。

**对应合同：** D04、D07、D08；T10、T11、T17、T19。参见[定稿](../PRD.md)、[共同执行约束](../README.md)。

## 用户演示

用户忘掉一项偏好，随后换说法提及、触发原文回补或重启 worker，系统都不会恢复；明确重新记住指定内容后，只恢复该范围。

## 依赖与实施范围

06 提供同一事实及不确定近义的判断基础；本票扩展 03 已具备的基础删除和开关。

- 将现有撤回语义应用到开放事实，覆盖原文回补、任务派生内容、缓存和已有索引，保留必要抑制凭据而非重新保存私人正文。
- 分开落实自动记录、长期使用、本轮不用；明确主动管理不被记录开关阻挡，重新开启不追溯授权禁录内容。
- 建立派生失效和发送前控制/撤回版本检查；后续新增概览、索引必须自行接入并测试，不能仅依赖异步通知。
- 明确恢复绑定新依据、事实及范围，不自动重放其他历史；账号删除或来源撤回使迟到任务失效。

## 验收标准

- [x] 普通同义新提及和历史重整不恢复删除事实，不确定近义暂缓处理。
- [x] 历史可按用户明确查看历史用途读取，但不因此用于普通个性化。
- [x] 尚未发送的旧切片遇删除/关闭使用时失效，已经发送的内容不虚报可收回。
- [x] 恢复只解除指定范围，其他墓碑和来源抑制继续有效。
- [x] 关闭记录期间不自动保存，重新开启不自动补录；本轮新自述及必要任务前文不被一刀切丢弃。
- [x] 账号删除后迟到 worker 不重建内容，跨账户 ID 和来源访问拒绝。
- [x] 日志和审计提供原因与引用，不泄露被删除正文或敏感值。

## 验证与交付

原文/摘要/任务接缝回归、发送前竞态、同义恢复和控制切换测试。交付撤回版本、来源过滤、明确恢复与派生失效合同；11、15、17 必须验证自己的实际接入。

## 明确不做

不承诺回收已发到外部模型的信息，不删除用户仍有权查看的原聊天，不把任务续接当作间接个性化绕过方式。

## Comments

2026-10-15：用户回复「按此发布」，确认本次拆分与依赖。已发布为 ready-for-agent，尚无实施记录。

2026-10-16：实施与验证完成（分支 `codex/issue-08-semantic-forgetting-and-controls`，基点 `14cea5f6`）。

- 交付：契约 `ProfileExtractionOutcome.SUCCEEDED_TURN_CONTROL`；自动画像新增 `parse_turn_profile_directive` 与 `AutomaticProfileRepository.account_exists` 端口，迟到守卫覆盖 `_run_retry_task` 入口、RUNNING 记账前、异常路径、终结记账、纠正分支与提交前复核（`profile_extraction_account_deleted`，与删除事务串行）；聊天「本轮不用」off 路径（披露 OFF、不编译、不删除）、历史限制指令按当前用户消息确定性识别、run config 冻结 `profile_usage_control_version` 并在发送前核对使用开关与版本；原子画像记住/忘掉/删除审计（原因 + 条目引用 + 计数，不含正文）。
- 测试：新增 19 项（`tests/profiles/test_issue08_semantic_forgetting.py` 16 项、`tests/chat/test_issue08_turn_control_and_pre_send_controls.py` 3 项）；`pytest tests/profiles tests/contracts tests/storage tests/lifecycle` 831 通过；全量对 main（327 vs 329 failed）逐条差集分支独有失败 0（另 2 项 main 独有为既有 flaky `test_api_boot`）。
- 走查（确定性，真实 SQLite + 真实聊天管线）：忘掉后同义新提及不恢复、恢复只解除指定范围、本轮不用与发送前关闭使用失效旧切片、账号删除后迟到 worker 不重建记账与内容——全部检查通过。
- 验证记录：`.scratch/profile-memory-redesign/validation/08-semantic-forgetting-and-controls/verification-record.md`；走查证据 `.../evidence/demo-walkthrough-20261016.txt`，脚本 `.../tools/walk_issue08_forgetting_demo.py`（可复跑）。
- 已知限制：模型侧对历史使用限制指令的遵从未做真实模型评测（属 18 票范畴）；RUNNING 记账的检查→保存间隙为非事务的极小竞态窗口（内容写入仍被提交前复核挡住），详见验证记录 §6。
