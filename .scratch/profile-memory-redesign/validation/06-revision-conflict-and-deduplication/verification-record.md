# 06 — 区分重复、纠错、变化与事实冲突：验证记录

**分支：** `codex/issue-06-revision-conflict-and-deduplication`（基点 `9323509a`，main）。
**环境：** conda `agent`（Python 3.11.15）；真实主模型 `qwen3.7-plus-2026-05-26`
（OS 凭据库 `credential_store`，本轮直连、未用代理）。
**证据：** 本文 + `baseline-stub-20261015T171408Z.{json,md}` +
`baseline-real-20261015T171448Z.{json,md}` + `run-lock-*.json` +
`evidence/demo-walkthrough-20261016.txt`（+`-model-outputs.json`）+
`tools/walk_revision_conflict_demo.py`（可复跑）。
**可复跑：**

    python scripts/run_issue01_profile_baseline.py --mode both \
        --output .scratch/profile-memory-redesign/validation/06-revision-conflict-and-deduplication
    python .scratch/profile-memory-redesign/validation/06-revision-conflict-and-deduplication/tools/walk_revision_conflict_demo.py \
        --out .scratch/profile-memory-redesign/validation/06-revision-conflict-and-deduplication/evidence/demo-walkthrough-<stamp>.txt

（真实模式需要凭据库中的 Qwen Key；网络受限时加 `--proxy http://127.0.0.1:7890`。
走查约 8 分钟，运行库在 `.tmp/issue06-demo-*`，脚本只录制聊天载荷片段，绝不记录凭据。）

## 1. 验收标准对照

| 验收标准（票据） | 证据 |
| --- | --- |
| 同一事实换说法只增加依据；「喜欢 Python」与「正在学 Python」不误合并 | `tests/profiles/test_issue06_revision_conflict.py::test_reworded_restatement_merges_evidence_into_one_item`、`::test_related_relations_do_not_auto_merge`；走查 [6b]「跑步条目 1 条，依据 1→2」 |
| 「说错了」与「现在变了」历史语义不同 | `::test_explicit_correction_does_not_keep_old_value_as_experience`（纠错不留历史）、`::test_state_change_keeps_old_value_as_real_history`、`::test_state_change_after_correction_keeps_corrected_value_as_history`（T07 先纠错后升学的连续场景）、`::test_commit_path_correction_marks_wrong_value_without_history_window`；走查 [2]/[3] 真实模型管道 |
| 多个爱好或可并行目标并存，没有明确取消依据不删旧目标 | `::test_parallel_hobbies_and_goals_coexist_until_explicit_change`；走查 [6b]/[6c] 确定性并存（真实模型观察项见 §2） |
| 手动编辑不被更高模型置信度或更晚自动消息覆盖 | `::test_automatic_commit_cannot_retire_user_edited_slot_value`、`::test_late_worker_commit_does_not_override_user_edit`；走查 [6]「用户编辑保留=True」「worker 自动说法未改写用户版本=True」 |
| 冲突未解决时不作确定判断，仅当前回答实际依赖该属性时澄清 | `::test_ambiguous_contradiction_is_kept_unresolved`、`::test_unresolved_conflict_is_not_used_as_definite_fact`、`::test_conflict_clarification_only_when_question_depends_on_slot`、`::test_user_edit_resolves_conflict_on_that_entry`；`tests/chat/test_issue06_profile_conflict_clarification.py`；走查 [5]（澄清问句引用双方原话、争议条目不进编号事实、无关问题不打扰） |
| 并发用户修改、重复提交及错误目标均有真实存储测试，不丢版本和依据 | `::test_late_worker_commit_does_not_override_user_edit`、`::test_duplicate_submission_is_idempotent_and_keeps_evidence`、`::test_stale_target_version_is_rejected_without_overwrite`（SQLite 真实库）；走查 [6] 过期版本 PATCH → 409 |
| 页面状态、采用结果与存储一致；冲突数据支持迁移、导出、删除和恢复 | 走查 [7] 全量视图；`::test_conflict_and_revision_data_survive_reopen`；`tests/storage/test_schema_v82_profile_revision_conflict.py`（迁移 82 列默认值/回填） |

## 2. 真实模型走查（最终代码，20261016）

1. 换说法：真实模型第二轮未重复提出（`succeeded_empty`，观察项）；确定性
   [6b] 证明归并进同一条、依据 1→2，且同一句里的「游泳」独立成条。
2. 纠错：先前抽取结果「我现在读大四。」落库后，聊天更正
   `local_rule succeeded_correction_written` 就地改写；纠错后含「大四」条目
   0 条——错误值不保留为真实经历。
3. 状态变化：「我现在升大四了。」经真实模型 `succeeded_written`，被更正的
   「大三」v3 以 `retired_reason=state_change` 替代并定格 `valid_until`，
   新值 active、`conflict_state=none`——T07「说错纠正后真实升学保留历史」
   经生产管道验证。
4. 并行目标：真实模型本轮因生成异常重发后未落库目标（观察项）；确定性
   [6c] 证明「我准备考研」与「我还打算找工作」并存互不替代。
5. 冲突：依赖专业的提问注入澄清（引用双方原话与「以哪个为准」），争议条目
   不作为编号事实进入切片；无关问题不注入澄清；页面视图两条均
   `conflict_state=unresolved`。
6. 权威与版本：worker 提交前页面编辑 → 重放不覆盖用户版本（正文保持
   「我改成每天早上学习两小时。」，依据不追加）；过期版本写回 →
   409「版本冲突，请刷新后重试。」。
7. 页面/存储一致：`GET /profiles/items` 中用户编辑条目 `来源=user`、争议
   条目 `unresolved`、其余 `none`，与仓库状态一致。

## 3. 自动化测试

- 新增 `tests/profiles/test_issue06_revision_conflict.py`（25 项）：换说法归并、
  关系不同不合并、纠错不留历史、状态变化保留历史（含先纠错后升学）、
  无标记矛盾不顶替会话更正、墓碑重开须带明确标记且来源时间不早于退役、
  并存、冲突不采用/依赖澄清/一侧被编辑后问句仍列双方、用户权威守卫、
  信号扩展（说错/记错、不是 A 是 B）。
- 新增 `tests/storage/test_schema_v82_profile_revision_conflict.py`（2 项）与
  `tests/chat/test_issue06_profile_conflict_clarification.py`（1 项）。
- 更新既有测试：`test_issue16_atomic_fact_identity.py`、`test_v2_08_atomic_profile.py`
  （升年级用例补充明确变化证据，与新分类语义对齐）。
- 先失败后通过（修复证据）：`test_state_change_after_correction_keeps_corrected_value_as_history`
  在修复前失败（`changed is None`——会话更正后的新值被墓碑拦截），补上
  「明确标记 + 来源时间不早于退役」重开条件后通过；并已由走查 [3] 在真实
  管道复证。
- 收口命令 `pytest tests/profiles tests/storage tests/contracts` → **738 通过**。
- 全量 pytest（xdist `-n 4 --ignore=tests/career_plan`，分支与 main 同命令同参数）：
  - 分支：**327 failed / 5512 passed / 37 skipped**（chat 目录 94 项，均为既有
    V1 退役与模块路由失败）。
  - main：**329 failed / 5482 passed / 37 skipped**。
  - 逐条差集：分支独有失败 **0**；main 独有 2 项为
    `tests/closeout/test_api_boot.py` 环境性 flaky（并发端口/路径碰撞，分支
    本轮通过）。

## 4. 静态检查

- `ruff check`（全部改动文件）：3 条均为既有问题（`signals.py:17 I001`、
  `database.py:1671 E501`、`test_v2_08_atomic_profile.py:8 I001`），main 同文件
  同项逐条一致；其余改动文件 0 问题。
- `mypy src`：141 errors（main 同命令同为 141，数量一致、无新增）。
- `pip check`：No broken requirements found。

## 5. 前端与契约

- `apps/web`：`npm run test:unit` 34 文件 **292 通过**（含冲突徽标用例）；
  `typecheck` 0 错误；`lint` 仅既有 `Composer.tsx` exhaustive-deps 警告；
  `build` 成功。
- `openapi.json`（319 paths）与 `packages/contracts/src/generated.ts` 已随契约
  （`retired_reason`、`conflict_state`、`AtomicProfileConflict` 相关字段）重新生成。

## 6. 基线语料对照（stub 与 real）

| 旅程 | 预期 | stub | real | 备注 |
| --- | --- | --- | --- | --- |
| t01-name / t01-pet / t01-habit | pass | pass | pass | 原子条目写入 |
| t02-psychology | pass | pass | pass | 无心理推断 |
| t03-time-limit | pass | pass | pass | 时间限定保留 |
| t04-cross-turn | pass（05） | pass | partial | 真实模型波动：`preference_captured=False`（抽取 `succeeded_empty`）；main 同刻复跑同样 partial，非本票回归 |
| positive-academic-control | pass | pass | pass | `succeeded_written`、active |
| recall-academic-control | known_failing | partial | partial | D11 召回缺口（与 04/05 相同） |

## 7. 语义与实现说明（含 code-review 处置）

- **权威分层**：页面编辑与明确「记住」写入 `write_origin=user`，是最高权威，
  自动提交不得退休，只能把新值标为未解决冲突；会话更正（`correct_fact`）是
  会话权威（`write_origin` 保持 automatic），可被更新的明确变更（如
  「现在升大四了」）替代并保留历史——T07 连续场景据此实现。
- **墓碑重开**：被更正/替代的事实仅当带明确变更标记、且来源时间不早于旧版本
  退役时允许重新进入；缺失来源时间视为无法证明，一律拦截（旧消息重放不得
  复活错误值）。
- **澄清组**：`pending_conflicts` 组内列出单值槽的全部活动说法——一侧被用户
  编辑消解自身标记后，澄清问句仍展示双方当前值，不会只引用单条却问
  「以哪个为准」。
- **更正两条路径的差异**为设计选择：普通更正就地改写、旧错误值不留历史；
  「纠正到已存在事实」并入既有条目并退休副本（去重合并语义，非错误值）。
- **并发/重试**：SQLite `BEGIN IMMEDIATE` 串行化写事务，提交边界内重读来源、
  目标版本与用户权威；失败重试由工单 15 的抽取重试队列（有界）承担，未新增
  额外 CAS 重试层。并发竞态以真实存储的顺序竞争用例覆盖（见 §1 第 6 行）。
- **信号扩展**：`_CORRECTION_MARKER` 增补说错/记错/写错/更正/纠正与
  「不是…（而是|是）」；`_STATE_CHANGE_MARKER` 收紧（「现在/目前」需带
  「了」才算变化，「我现在大二」是普通陈述）——「我不是不想学，只是没时间」
  仍为 FORBIDDEN（专项用例锁定）。

## 8. 剩余限制与边界

- 真实模型抽取有波动：走查 [1] 第二轮与 [4] 本轮未落库候选（观察项），由
  确定性 [6b]/[6c] 补证机制正确性；t04 real 的 partial 有 main 同证（§6）。
- T10 无真双线程竞争测试；以真实 SQLite 的顺序竞态用例 + 事务串行化覆盖。
- `tests/profiles/test_issue06_revision_conflict.py` 约 744 行，超过全局
  AGENTS「单文件 ≤500 行」建议；与仓库既有测试文件规模一致（多处 500–800 行），
  后续按主题拆分。
- 信号标记扩展会波及 03/12 纠错路由；已由全量回归差集 0 覆盖（§3）。
