# 11 — 按任务召回记忆并追踪回答用途：验证记录

**分支：** `codex/issue-11-hybrid-purpose-recall`（基点 `fff98a9d`，main；验证后
已并入 `main@5709a0f6` 的 16 票改动，合并无冲突）。
**环境：** conda `agent`（Python 3.11.15）；确定性走查（本机规则 + 真实
SQLite 存储 + 真实原子画像服务，不调用外部模型）+ 真实向量探针
（development 模式 + OS 凭据库，仅第 6 小节；无凭据/网络不稳时如实跳过而不
判失败）。
**证据：** 本文 + `evidence/demo-walkthrough-20261016.txt` +
`tools/walk_issue11_hybrid_recall_demo.py`（可复跑）。
**可复跑：**

    python .scratch/profile-memory-redesign/validation/11-hybrid-purpose-recall/tools/walk_issue11_hybrid_recall_demo.py \
        --out .scratch/profile-memory-redesign/validation/11-hybrid-purpose-recall/evidence/demo-walkthrough-<stamp>.txt \
        [--proxy http://127.0.0.1:7890] [--no-real]

（走查约 10 秒，运行库在 `.tmp/issue11-demo-*`；脚本只使用临时库，不读取或
记录任何凭据与真实数据。）

## 1. 验收标准对照

| 验收标准（票据） | 证据 |
| --- | --- |
| 同义提问可以找到事实，半小时限制和解释顺序实际改变回答 | `test_issue11_hybrid_purpose_recall.py::test_semantic_channel_recalls_paraphrase_and_keeps_unrelated_out`（「帮我安排晚上怎么学」→ 召回「每晚只能抽出半小时」）；`::test_relation_slot_keyword_recalls_academic_fact`（属性问句命中）；真实探针（本文 §2 第 6 小节，同义对余弦 0.6264/0.5737 ≥ 0.55）；评测 journey `recall-academic-control` 由 `known_failing` 转 `pass`（`tests/evaluation/test_profile_baseline_journey_runner.py::test_recall_academic_control_adopts_stored_fact`）；走查 [1][2][6] |
| 冲突、过期目标、无关爱好和越用途敏感事实不进入确定性回答 | `::test_semantic_similarity_does_not_bypass_purpose_gate`（越用途敏感）、`::test_semantic_similarity_does_not_bypass_expired_gate`（过期）、`::test_semantic_similarity_does_not_bypass_conflict_gate`（冲突）、`::test_semantic_channel_recalls_paraphrase_and_keeps_unrelated_out`（无关爱好排除原因「与当前问题无关」）；走查 [2][6] |
| 本轮明确要求覆盖长期默认但不改写长期记忆，新主题事实同样可被召回 | `::test_explicit_request_still_overrides_semantic_default`（覆盖且版本不变）；`::test_semantic_channel_recalls_paraphrase_and_keeps_unrelated_out`（新主题约束经语义进入候选）；走查 [2] |
| 采用记录对应真正发送的上下文，不记录一套而使用另一套 | `tests/chat/test_issue11_recall_adoption_record.py::test_trimmed_adoption_record_matches_sent_block`（预算整条裁剪后记录=载荷）；`::test_final_gate_drop_rewrites_adoption_record_only`（最终门整体未采用 → 记录改写为未采用、冻结快照保留）；`test_improvement22_unified_profile_expression.py::test_chat_policy_failure_drops_profile_and_updates_disclosure`（两键一并清除）；走查 [5] |
| 删除、改用途、关闭使用和账户切换同时约束关键词、向量、缓存及发送前切片 | `::test_deleted_item_leaves_index_and_rebuild_does_not_resurrect`（删除即移除索引行）；`::test_index_is_account_isolated`（账户隔离）；发送前撤回竞态沿用 `test_improvement22::test_delete_between_compilation_and_send_drops_adopted_rules`、改用途/关闭使用沿用 07/08/09 既有测试（本票未改语义，全部通过）；走查 [4] |
| 索引重建和故障恢复不复活旧条目，派生数据纳入导出边界说明及账户删除 | `tests/storage/test_schema_v85_profile_recall_index.py::test_recall_index_is_derived_account_scoped_and_backup_visible`（不进导出类别与逻辑摘要、备份含行、删除清空、删除顺序先于 `profile_items`）；`::test_deleted_item_leaves_index_and_rebuild_does_not_resurrect`（墓碑重建不复活）；`lifecycle/catalog.py` 模块 docstring 与登记注释说明派生数据边界 |
| 不默认发送完整概览，预算不足优先舍弃低相关条目而非删关键条件 | `::test_budget_prefers_rule_constraint_over_semantic_candidates`（tier0 约束保留、语义候选超额整条排除）；既有切片预算测试（`test_improvement19::test_long_item_is_excluded_whole_when_budget_is_tight` 等）继续通过 |

## 2. 确定性走查（最终代码，20261016）

1. **关系槽位召回**（不装配语义索引）：记住「我是计算机专业的大二学生」与
   无关爱好；「你记得我在读什么专业吗？」按关键词通道采用学业事实
   （`recall_basis=keyword`），爱好排除原因「与当前问题无关」。
2. **语义通道（确定性替身）**：同义改写召回（basis 含 semantic）；用途门禁
   （花粉过敏不对「学习计划」采用）、时效门禁（过期不作当前约束）、本轮明确
   要求覆盖长期默认均优先于语义相似；覆盖不改长期值（版本不变）。
3. **显式降级**：未装配向量端口时降级原因
   `语义召回暂不可用：未配置向量化端口` 进入切片；关键词通道照常采用；未
   命中原因标注为「语义召回暂不可用，关键词与任务规则未命中」而非普通无关。
4. **索引生命周期**：删除后索引行 0 且不再采用；全量重建只从当前有效条目
   重算，墓碑不复活；另一账户零索引行、零候选。
5. **预算整条裁剪**：`adopted_profile_block_within_budget(remaining_tokens=0)`
   后发送条目为空、记录同步排除（正文不截断）；冻结的完整编译快照保留原始
   结果供重试复用与核对。
6. **真实向量探针**（development + OS 凭据库，走查脚本内置）：
   - 同义对「帮我安排晚上怎么学 ↔ 我在学线性代数，每晚只能抽出半小时」
     余弦 **0.6264**；「你记得我在读什么专业吗？↔ 我是计算机专业的大二学生」
     余弦 **0.5737**（均 ≥ `PROFILE_RECALL_MIN_SIMILARITY=0.55`）。
   - 无关对「解释贝叶斯定理 ↔ 我平时喜欢跑步」余弦 **0.1746**、「帮我安排
     晚上怎么学 ↔ 我喜欢吃辣」余弦 **0.2488**（均 < 阈值，不被召回）。
   - 生产组合（`create_app` 注入的真实召回索引）端到端：同义改写经语义通道
     召回（basis 含 semantic、无降级），无关爱好不被召回。
   - 成本：单批 8 条文本真实向量调用耗时 **0.39 秒**；每回合编译至多 1 次查询
     向量调用（索引增量同步只在条目变化时批量补嵌，批次上限 10），无当前问题
     时 0 次调用（`::test_compile_without_question_skips_embedding_calls`）。

## 3. 自动化测试

- 新增 `tests/profiles/test_issue11_hybrid_purpose_recall.py`（13 项）：关系槽位
  召回、语义改写召回与无关排除、用途/时效/冲突门禁优先、本轮明确要求覆盖、
  条目数预算取舍、降级（故障注入）与降级未命中原因、删除/重建不复活、账户
  隔离、合同变化重建不混用旧向量（新合同向量方向改变可观察）、无问题零嵌入。
- 新增 `tests/chat/test_issue11_recall_adoption_record.py`（4 项）：预算裁剪后
  记录=载荷；最终门整体未采用 → 记录改写为「最终输入预算不足，本轮未采用」
  且冻结快照保留；降级披露含「语义召回暂不可用」+ 审计 `recall_degraded`；
  降级且未命中时披露不得说成「没有与你当前任务匹配」。
- 新增 `tests/storage/test_schema_v85_profile_recall_index.py`（2 项）：v84 →
  v85 建表建索引、列齐全、重复初始化幂等；派生索引不进导出/逻辑摘要、备份
  可见、删除清空、ACCOUNT_TABLES 顺序先于 `profile_items`。
- 既有测试更新（责任票行为变化）：`tests/evaluation/test_profile_baseline_journey_runner.py`
  （`recall-academic-control` 由 known_failing 转 pass，改名断言采用条目与
  basis）；`tests/chat/test_improvement19_purpose_aware_slice.py`（编译策略版本
  1.1 → 1.2）；`tests/chat/test_improvement22_unified_profile_expression.py`
  （策略失败同时清除 `adopted_profile_slice_compiled`）。
- 聚焦（合并 main@5709a0f6 后）：`pytest tests/profiles tests/storage` →
  **830 通过**；`pytest tests/chat/{test_issue11_recall_adoption_record,
  test_improvement19_purpose_aware_slice, test_improvement22_unified_profile_expression,
  test_issue07_review_history_injection, test_issue08_turn_control_and_pre_send_controls}
  tests/evaluation/test_profile_baseline_journey_runner.py` → **44 通过**。
- 全量（与 main 同命令 `-n 4 --ignore=tests/career_plan`；分支为合并后代码）：
  - 分支：**327 failed / 5617 passed / 39 skipped**；main@5709a0f6：
    **329 failed / 5615 passed / 37 skipped**。
  - 逐条差集：**分支独有失败 0**。main 独有失败 2 项为
    `tests/closeout/test_api_boot.py` 环境性 flaky（06/08/09 记录同现象）；
  跳过 +2 = worktree 无 `apps/web` 构建产物，`tests/runtime/test_runtime_contract.py`
   2 项按守卫跳过（main 存在旧构建故实际执行且通过），属环境差异非代码差异。
  - 顺序全量对照（合并 main 前，`--continue-on-collection-errors`）：分支
    342 failed / 5708 passed / 39 skipped vs main 345 / 5705 / 37，分支独有
    失败同为 0；4 项 `tests/career_plan` 收集错误两侧一致（既有）。

## 4. 静态检查

- `ruff check`（全部改动文件 + 新增测试/走查脚本）：本票新增 0 问题；
  `database.py:1671` 1 条 E501 与 main 同文件同项一致（既有长 SQL）。
- `mypy src`：141 errors（main 同命令同为 141，无新增；唯一涉及改动文件的
  `turn.py:5460` 为 main `:5436` 既有项的行号平移）。
- `pip check`：No broken requirements found。

## 5. 接口与实现摘要

- 新模块 `src/bridges/profiles/recall.py`：`ProfileRecallContract`（模型/维度/
  规范化/召回策略版本四元合同，`contract_hash` 决定重建）、
  `ProfileRecallIndex`（SQLite 派生的版本化向量索引：`recall`/`rebuild`/
  `_sync_entries`/`_embed_items`/`_search`；阈值 0.55、嵌入批次 10；合同变化
  时旧版本退休重建、正文变化或版本落后不参与检索）、稳定降级常量
  （`RECALL_DEGRADED_PREFIX`/`NO_EMBEDDING`/`INDEX_ERROR`）、
  `ProfileRecallOutcome`（相似度命中/降级原因/索引版本/同步计数）。
- 契约 `contracts/profile_adoption.py`：`AdoptedProfileItem.recall_basis`
  （task_rules/keyword/semantic/review_history 的组合）；`AdoptedProfileSlice`
  增加 `recall_degradation_reason`、`recall_index_version`；编译策略版本
  `purpose-slice-1.2`（接受 1.1 重试复用）。
- 原子画像 `profiles/atomic.py`：关系槽位关键词通道（GRADE/MAJOR/IDENTITY，
  仅让条目成为候选、不改变任何门禁）；语义命中只作候选来源，真伪/用途/时效/
  冲突/低可靠度/本轮明确要求门禁全部照旧；降级时未命中原因如实标注；
  `set_recall_index` 注入端口；`is_adopted_slice_current` 接受 1.1/1.2。
- 聊天 `chat/turn.py`：`adopted_profile_slice`（实际发送：预算整条裁剪后的
  结果）与 `adopted_profile_slice_compiled`（冻结的完整编译快照）拆分保存；
  最终载荷门整体未采用时改写发送记录为「最终输入预算不足，本轮未采用」；
  复用路径与发送前核对优先读冻结快照（回落发送态兼容升级前运行）；
  重试复制两键；策略失败两键一并清除；披露在降级时如实说明（有条目：追加
  「本轮语义召回暂不可用，仅使用了关键词与任务规则能确认的信息」；无条目：
  「本轮语义召回暂不可用，只检查了关键词与任务规则能确认的信息」而不是
  「没有与你当前任务匹配」）；审计新增 `recall_degraded`/`recall_index_version`。
- 组合根 `api/app_setup_knowledge.py`：复用同一全局固定 Embedding 端口构造
  `ProfileRecallIndex` 并注入原子画像服务（主模型切换不影响本索引）。
- 存储 `storage/database.py`：迁移 85 建 `profile_recall_index_versions` /
  `profile_recall_index_entries`（+账户索引），`SCHEMA_VERSION` 84 → 85；
  `REQUIRED_TABLES`/`REQUIRED_INDEXES`/`REQUIRED_TABLE_COLUMNS` 登记。
- 生命周期 `lifecycle/catalog.py`：两表进 `ACCOUNT_TABLES`（先于
  `profile_items` 删除）；派生数据不进导出类别与逻辑摘要（边界见模块
  docstring），随账户删除与备份快照。
- 模型端口 `ai/ports.py`：新增 `EmbeddingOperation.PROFILE_RECALL` 审计操作；
  评测 `evaluation/profile_journeys.py`：`recall-academic-control` 目标由
  `known_failing` 转 `pass`（责任票 11/15）。

## 6. 剩余限制与边界

- 真实同义对「你记得我在读什么专业吗？↔ 我是计算机专业的大二学生」余弦
  0.5737 距阈值 0.55 仅约 0.024：更远的口语改写可能漏召（宁可漏用的保守
  选择）；语义质量与阈值校准的长跑评测由 18 票（真实模型评测）负责，本票
  只锁定管线结构与显式降级。
- 阈值/编码替身只验证管线行为；真实探针为单批次抽样（8 条文本），未覆盖
  长文本、口语化与跨主题批次的分布稳健性。
- 语义命中不区分条目状态：被替代条目也进入索引（供 07 回顾用途的语义召回），
  其采用仍受对应门禁约束；若未来扩大回顾用途范围需同步复核索引过滤。
- 增量同步以（条目版本, 正文哈希）判定失效，仅在编译时机发生；长期不用的
  账户索引行停留在上次快照（读取时仍按当前条目核对，不会读到旧正文）。
- 升级前旧运行配置只有发送态记录（无冻结快照键）：重试复用回落读取发送态
  （兼容路径，`_stored_adopted_slice` 优先键顺序），新运行一律写两键。
- 派生数据按设计不进导出（仅随备份快照与账户删除）；如未来需要「可读的
  召回足迹」导出，应新增独立类别而不是直接导出向量行。
