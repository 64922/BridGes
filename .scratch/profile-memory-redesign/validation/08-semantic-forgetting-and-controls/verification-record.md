# 08 — 阻断忘掉信息和关闭个性化后的间接回流：验证记录

**分支：** `codex/issue-08-semantic-forgetting-and-controls`（基点 `14cea5f6`，main）。
**环境：** conda `agent`（Python 3.11.15）；走查为确定性走查（本机规则 + 真实
SQLite 存储 + 真实聊天管线），不调用外部模型、不读取凭据。
**证据：** 本文 + `evidence/demo-walkthrough-20261016.txt` +
`tools/walk_issue08_forgetting_demo.py`（可复跑）。
**可复跑：**

    python .scratch/profile-memory-redesign/validation/08-semantic-forgetting-and-controls/tools/walk_issue08_forgetting_demo.py \
        --out .scratch/profile-memory-redesign/validation/08-semantic-forgetting-and-controls/evidence/demo-walkthrough-<stamp>.txt

（走查约 10 秒，运行库在 `.tmp/issue08-demo-*`；脚本只使用临时库，不读取或
记录任何凭据与真实数据。）

## 1. 验收标准对照

| 验收标准（票据） | 证据 |
| --- | --- |
| 普通同义新提及和历史重整不恢复删除事实，不确定近义暂缓处理 | 既有接缝：`tests/profiles/test_issue18_profile_validity_and_revocation.py::test_delete_then_replay_and_near_synonym_stay_revoked`、`::test_edit_identity_change_suppresses_old_value`；走查 [1]「忘掉跑步 → 我喜欢慢跑暂缓写入、活动条目 0」 |
| 历史可按用户明确查看历史用途读取，但不因此用于普通个性化 | 本轮不用/关闭使用的控制语义进入模型合同（「不得从旧对话、摘要或旧回答中的个人信息进行间接个性化；只保留当前任务续接必要的目标、来源和明确条件」）：`tests/chat/test_issue08_turn_control_and_pre_send_controls.py`、`tests/chat/test_issue07_profile_usage_control.py`；普通提及不误判为控制：`tests/profiles/test_issue08_semantic_forgetting.py::test_non_directive_text_not_recognized` |
| 尚未发送的旧切片遇删除/关闭使用时失效，已经发送的内容不虚报可收回 | `tests/chat/test_issue08_turn_control_and_pre_send_controls.py::test_usage_disabled_between_compile_and_send_invalidates_slice`、`::test_usage_toggle_between_compile_and_send_invalidates_stale_slice`（控制序号变化使旧切片失效）；既有撤回竞态：`tests/chat/test_v2_08_profile_immediate_effect.py::test_revocation_between_compile_and_call_blocks_stale_slice`；走查 [3] |
| 恢复只解除指定范围，其他墓碑和来源抑制继续有效 | `tests/profiles/test_issue08_semantic_forgetting.py::test_remember_restores_only_specified_scope`；走查 [2] |
| 关闭记录期间不自动保存，重新开启不自动补录；本轮新自述及必要任务前文不被一刀切丢弃 | `::test_turn_control_is_active_management_while_recording_stopped`（控制指令是主动管理，不因关闭记录被墓碑吞掉）；关闭记录不补录为既有 07 接缝（`tests/profiles/test_issue07_profile_controls.py`、`tests/chat/test_issue07_profile_usage_control.py`）；限制指令保留任务续接条件与本轮新自述（同一批聊天测试断言） |
| 账号删除后迟到 worker 不重建内容，跨账户 ID 和来源访问拒绝 | `::test_stale_worker_task_after_account_deletion_writes_nothing`（入口守卫）、`::test_commit_recheck_fails_closed_when_deleted_mid_attempt`（模型调用期间删除 → 提交前复核失败关闭）、`::test_other_account_worker_is_unaffected_by_deletion`（同库另一账户不受影响）；跨账户隔离既有：`test_issue18_profile_validity_and_revocation.py::test_atomic_profile_is_account_scoped`；走查 [4] |
| 日志和审计提供原因与引用，不泄露被删除正文或敏感值 | `::test_remember_forget_delete_audit_is_text_free`（记住/忘掉/删除三类审计：原因 + 条目引用 + 计数，序列化后不含正文） |

## 2. 确定性走查（最终代码，20261016）

1. **忘掉 + 同义提及**：`记住：我喜欢跑步` → `忘掉「跑步」`（forgotten，命中 1）
   → 同义新提及「我喜欢慢跑」暂缓写入（零写入），活动条目 0。
2. **恢复范围**：删除「我喜欢跑步」「我住在南昌」两条后，明确重新记住
   「我喜欢跑步」只恢复该条；未指定方向的「我住在南昌」来源重放仍被墓碑拦截。
3. **本轮不用（真实回合）**：相关提问正常注入切片 → 「这轮先不用我的画像，
   帮我安排复习计划」当轮切片未注入、披露 OFF、信息保留、控制语义进入模型
   合同 → 下一轮恢复注入；编译后发送前关闭使用（在上下文说明落库时注入竞态）
   → 旧切片未进入发送载荷、披露如实降级为条目数 0。
4. **账号删除 + 迟到 worker**：失败重试留下 PENDING 任务（含来源快照记账）→
   删除记录落地并删行 → 握着删除前任务对象的迟到 worker 结论 `exhausted`，
   运行记账 0 行、任务记账 0 行、画像条目 0 条。

## 3. 自动化测试

- 新增 `tests/profiles/test_issue08_semantic_forgetting.py`（16 项）：控制指令
  识别（正/负样本）、控制消息终态登记与重放幂等、关闭记录期间控制仍生效、
  恢复限定范围、审计不含正文、账号删除三道守卫（入口/提交前/终结记账）与
  跨账户隔离。
- 新增 `tests/chat/test_issue08_turn_control_and_pre_send_controls.py`（3 项）：
  本轮不用只影响当前回合；发送前关闭使用失效旧切片；关闭后重开的控制序号
  变化同样使旧切片失效。
- 收口命令 `pytest tests/profiles tests/contracts tests/storage tests/lifecycle`
  → **831 通过**。
- 全量 pytest（xdist `-n 4 --ignore=tests/career_plan`，分支与 main 同命令同参数）：
  - 分支：**327 failed / 5529 passed / 39 skipped**（chat 目录 94 项为既有 V1
    退役与模块路由失败）。
  - main：**329 failed / 5510 passed / 37 skipped**。
  - 逐条差集：分支独有失败 **0**；main 独有 2 项为
    `tests/closeout/test_api_boot.py` 环境性 flaky（并发端口/路径碰撞，与 06
    票记录的同一现象一致；本次分支运行中该项转为跳过）。
  - 通过数 +19 = 本票新增测试（16 + 3）。

## 4. 静态检查

- `ruff check`（全部改动文件 + 新增测试文件）：新增 0 问题；`automatic.py`
  13 条 E501 与 main 同文件同项逐条一致（既有长 SQL/常量行）。
- `mypy src`：141 errors（main 同命令同为 141，数量一致、无新增）。
- `pip check`：No broken requirements found。

## 5. 接口与实现摘要

- 契约：`ProfileExtractionOutcome.SUCCEEDED_TURN_CONTROL = "succeeded_turn_control"`
  （本地规则终态，不进入自动抽取；重放幂等）。
- 自动画像：`parse_turn_profile_directive`（确定性识别「本轮/这次不用画像/
  记忆/长期信息/个人信息」）；`AutomaticProfileRepository.account_exists`
  新增端口（内存替身恒真；SQLite 以「账户行缺失且存在该账户删除记录」判定，
  覆盖删除进程中途且不误判未建行的库）；迟到守卫覆盖 `_run_retry_task` 入口、
  RUNNING 记账前、异常路径、终结记账、纠正分支与提交前复核
  （`profile_extraction_account_deleted`，与删除事务串行）。
- 聊天：`_compile_profile_slice` 增加「本轮不用」off 路径（披露 OFF、不编译、
  不删除）；历史限制指令与切片编译共用同一确定性识别（按当前用户消息，不
  依赖生成入口截断标识）；采用快照的 run config 冻结
  `profile_usage_control_version`，`_profile_slice_still_current` 发送前核对
  使用开关与版本（版本变化即失效重编译）。
- 原子画像：记住/忘掉/删除写审计（`profile_remembered` / `profile_forgotten` /
  `profile_item_deleted`），只记原因、条目引用与计数，不含正文。

## 6. 剩余限制与边界

- 模型侧对「【本轮长期信息使用限制】」的遵从度未做真实模型评测：本票控制为
  确定性注入（信息不进入本轮画像材料），提示词约束的遵从属 18 票真实闭环
  验收范畴；本记录不宣称模型一定不利用历史原文。
- RUNNING 记账的“检查→保存”间隙为非事务（为保持 04 票「单次提取只开一个
  提交边界」合同，未引入第二个业务事务）：若删除恰好发生在该间隙，task/run
  记账可能短暂重建；画像内容写入仍被提交前复核在同一事务内挡住，迟到失败
  记账也被守卫拦下。已知窗口极小，暂不为此增加事务边界，留下此记录供后续
  评估。
- 「未发送的旧切片遇关闭使用失效」以采用快照（原子路径）为对象；非原子旧
  画像路径没有撤回版本概念，行为与 07/18 既有接缝一致。
