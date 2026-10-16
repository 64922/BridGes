# 12 — 根据反馈纠正当前回答与必要记忆：验证记录

**分支：** `codex/issue-12-feedback-correction-loop`（基点 `main@631a4952`）
**环境：** conda `agent`（Python 3.11.15）；确定性走查（本机规则 + 真实
SQLite 聊天仓库 + 真实画像服务，不调用外部模型，生成用确定性替身回答）。
**证据：** 本文 + `evidence/demo-walkthrough-20261016.txt` +
`tools/walk_issue12_feedback_demo.py`（可复跑，27 项检查全部通过）。
**可复跑：**

    python .scratch/profile-memory-redesign/validation/12-feedback-correction-loop/tools/walk_issue12_feedback_demo.py \
        --out .scratch/profile-memory-redesign/validation/12-feedback-correction-loop/evidence/demo-walkthrough-<stamp>.txt

（走查约 10 秒，运行库在 `.tmp/issue12-demo-*`；脚本只使用临时库，不读取或
记录任何凭据与真实数据。）

## 1. 验收标准对照

| 验收标准（票据） | 证据 |
| --- | --- |
| 明确身份纠错定位对应事实，当前与后续回答均不沿用旧结论 | `test_issue12_feedback_router.py::test_fact_correction_locates_adopted_item_and_new_value`（从原回答采用快照定位条目与替代值）；`test_issue12_feedback_correction_loop.py::test_identity_correction_rewrites_answer_and_memory`（定点改写 + 后续回合不沿用旧值）；`::test_corrected_fact_is_used_in_new_conversation`（全新会话复问正确）；`test_issue12_feedback_control.py::test_located_fact_correction_rewrites_and_suppresses_old_value`（抑制键落库）；走查 [1] |
| 「今天简短」不改变长期偏好，「以后先给结论」正确持久化 | `test_issue12_feedback_control.py::test_long_term_preference_is_written_and_session_request_is_not`；`test_issue12_feedback_correction_loop.py::test_session_request_and_expression_never_write_long_term`、`::test_long_term_preference_is_written_and_used_this_turn`（当轮提示块已含偏好）；走查 [2][3] |
| 「解释太难」调整表达，不自动产生能力低或掌握不足标签 | `test_issue12_feedback_router.py::test_expression_complaint_does_not_route_as_fact`（不路由为事实因果）；`test_issue12_feedback_control.py::test_expression_complaint_and_unclear_write_nothing`（零写入）；`test_issue12_feedback_correction_loop.py::test_session_request_and_expression_never_write_long_term`（提示块只要求换表达）；走查 [2] |
| 「不是这个意思」无法定位时不猜测批量修改，不扩大敏感授权 | `test_issue12_feedback_router.py::test_vague_negation_only_requests_clarification`、`::test_blame_without_object_requests_clarification`、`::test_correction_target_not_found_without_replacement_stays_unresolved`；`test_issue12_feedback_control.py::test_sensitive_replacement_does_not_bypass_purpose_gate`（订正正文不绕过用途门，`source_snapshot` 置空）；走查 [4][7] |
| 保存失败仍可依据本轮明确事实改答并诚实反馈；改答失败不回滚已成功纠正或假称完成 | `test_issue12_feedback_correction_loop.py::test_memory_failure_still_answers_honestly`（FAILED + 不声称已更新 + 旧条目保留）、`::test_generation_failure_does_not_roll_back_correction`；`test_issue12_feedback_control.py::test_stale_version_is_reported_as_failed`、`::test_deleted_target_is_reported_not_guessed`；走查 [5][6][7] |
| 重复反馈与生成重试不重复写记忆或执行工具副作用，删除竞态受相同守卫约束 | `test_issue12_feedback_correction_loop.py::test_repeat_of_identical_feedback_is_idempotent`（版本不递增）、`::test_retry_reuses_feedback_relation_without_rewriting_memory`（重试复用终态记账）；`test_issue12_feedback_control.py::test_feedback_replay_does_not_write_again`；删除/版本竞态即上门禁测试；走查 [6][7] |
| 反馈与运行关系支持重启、读取、导出和账号删除，聊天状态与错误反馈完整 | `test_issue12_feedback_correction_loop.py::test_feedback_relation_survives_restart`（重启后投影可读 kind/memory_status）、`::test_feedback_relation_is_exported_without_user_text`（导出含脱敏关系元数据、无用户原文）；`lifecycle/catalog.py` 继承账户删除即清；走查 [1][6] |

## 2. 确定性走查（最终代码，20261016）

1. **身份纠错**：seed「我是研究生」→ 第一轮采用；「你记错了，我不是研究生，
   我是本科生。」→ 提示块 + 「记忆结果：本次修改已写入长期记忆」；条目集合
   变为 `{我是本科生}`；运行元数据含 kind/memory_status/original_message_id/
   original_run_id/original_slice_id；同会话后续与全新会话复问均只见新值。
2. **本轮要求/表达投诉**：`turn_request` 与 `expression_quality` 均
   `not_applicable` 且条目 0 条；表达投诉当轮提示块含「用更简单、更通俗的表达」。
3. **长期偏好**：「以后都先给结论。」写入且正文保留原话，当轮载荷已包含。
4. **指向不明**：「不是这个意思。」只请用户澄清、`clarification_required`、零写入。
5. **保存失败注入**（`correct_item` 抛错）：提示块仍给出修正回答，含「不得声称
   已经记住或已经更新画像」；元数据 FAILED；旧条目保留。
6. **生成失败与幂等**：失败回答不回滚纠正且投影保留 memory_status=WRITTEN；
   重复同一反馈版本不递增；重试渲染同一提示块与结果、版本保持不变。
7. **删除竞态与过期版本**：目标删除后处理 → `no_active_record`、零写入；
   版本过期 → `failed`、原条目不变。

## 3. 自动化测试

- 新增 `tests/profiles/test_issue12_feedback_router.py`（13 项）：六类路由、
  否定/替代值抽取、年级状态兜底仅限无对象时、无前置回答不路由、普通陈述
  不误触。
- 新增 `tests/profiles/test_issue12_feedback_control.py`（9 项）：定点改写与
  抑制、重放不重写、状态变化无定位按原句记住、偏好写入/本轮要求零写入、
  单否否定不猜测、删除与过期版本如实报告、敏感订正不绕门。
- 新增 `tests/chat/test_issue12_feedback_correction_loop.py`（12 项）：真实
  ChatService + SQLite 仓库端到端：改答、元数据与投影、失败注入、重试幂等、
  新会话复问、重启读回、导出脱敏。
- 新测试聚焦：`pytest` 三文件 **34 passed**（20261016，本工作树）。
- 责任票行为变化（`tests/evaluation/test_profile_baseline_journey_runner.py`）：
  t04 第二轮「以后先给我结论，再展开解释。」属工单 12 的明确长期偏好反馈，
  由确定性路由同步持久化并终结该消息的普通抽取（不再产生网关提取调用），
  原 `test_cross_turn_gateway_call_carries_real_conversation_and_window` 的
  “两次提取 run 均携带窗口”断言随之更新为：网关调用（首轮）携带真实会话、
  第二轮记账为 `succeeded_feedback` 且偏好已写入、窗口证据由生产邻居读取
  函数的应用级探测给出（`test_cross_turn_preference_feedback_and_window_evidence`）；
  `profile_journey_evidence.py` 的 t04 `neighbor_context_present` 同步接受
  探测证据，旅程 target/baseline_observation 更新为责任票 05/12。
  工单 05 的载荷级窗口机制仍由 `tests/profiles/test_issue05_multi_turn_evidence.py`
  独立覆盖（本票未改语义，全部通过）。
- 契约制品：`openapi.json` 用 `scripts/regenerate_openapi.py` 重生成（+146 行，
  纯新增反馈 schema），`packages/contracts/src/generated.ts` 由
  `openapi-typescript 7.13.0` 同步（+69 行）；`tests/contracts/test_openapi_sync.py`
  2 项通过。
- 全量（同命令 `-n 4 --ignore=tests/career_plan`）：
  - 分支：**328 failed / 5667 passed / 39 skipped**；main 基线（同命令）：
    **328 failed / 5635 passed / 37 skipped**（后续复核 main 再跑为
    329 / 5634 / 37）。
  - 逐条差集（分支失败集 vs main 失败集）：**分支独有失败 0**。观察到的差异
    均为环境/既有 flaky：`test_arxiv_worker_reliability.py::test_handshake_timeout_maps_to_arxiv_handshake`
    在两次 main/分支运行间互换；main 复核轮次多出的
    `tests/closeout/test_api_boot.py` 2 项系与并发运行同库冲突所致；另
    `test_secret_scan.py`（`scripts\is...`）两侧同为既有失败。
  - 跳过 +2 = worktree 无 `apps/web` 构建产物的 runtime 守卫差异（既有环境
    差异，非代码差异）。

## 4. 静态检查

- `ruff check`（全部改动文件与新增测试/走查脚本）：新增 0 问题
  （`automatic.py` 13 条 E501 为 main 同文件既有项）。
- `mypy src`：141 errors（main 同命令同为 141，无新增；唯一涉及改动文件的
  `turn.py:5611` 为既有项相对 main `:5500` 的行号平移）。
- `pip check`：No broken requirements found。

## 5. 接口与实现摘要

- 新契约 `contracts/feedback_correction.py`：`FeedbackCorrectionKind`
  （fact_correction/state_change/long_term_preference/turn_request/
  expression_quality/unclear）、`FeedbackAdjustment`、
  `FeedbackMemoryKind`/`FeedbackMemoryStatus`、`FeedbackCorrectionRoute`
  （含 `has_replacement`：否定旧值本身不算新值）、`FeedbackCorrectionResult`
  （含 `context_metadata()`）、`FeedbackCorrectionMetadata`（运行配置与投影
  读取合同）、`FeedbackCorrectionProjection`。
- 新模块 `profiles/feedback.py`：`route_feedback`（确定性六类路由；无前置
  回答或普通陈述不路由；指向不明只澄清）+ `AdoptedFeedbackTarget`（从原回答
  采用快照构造）。反馈路由在同步控制里先于既有 CORRECTION 分类处理，避免
  同一句反馈被二次“按维度改最近条目”。
- `profiles/atomic.py`：新增 `correct_item`（ACTIVE + expected_version 守卫、
  文本一致幂等 no-op、identity 变化清 quotes 与写抑制键、维度提示解析身份；
  目标不可用时抛 `AtomicProfileError`）。
- `profiles/automatic.py`：`process_synchronous_controls(..., feedback_route=...)`
  分支（位于终态重放保护之后、普通抽取之前）：定位命中 → 定点改写；未定位
  但状态变化/明确替代值 → 按用户原句 `remember`（先做收敛幂等检查）；单纯
  否定未定位 → UNRESOLVED；长期偏好 → 显式 `remember`；本轮要求/表达投诉 →
  零写入；指向不明 → 只澄清。敏感门 `_feedback_sensitive_block` 防止订正
  正文绕过用途门（CREDENTIAL/PURPOSE_REQUIRED），命中时运行快照置空。
  反馈消息终结该消息的普通异步抽取（重放复用同一终态记账）。
- 契约补充：`ProfileExtractionOutcome.SUCCEEDED_FEEDBACK`/`FEEDBACK_FAILED`、
  `ProfilePreprocessResult.feedback`；`ChatMessageProjection.feedback_correction`。
- 聊天：`service_profiles.py`（准入路由 `_feedback_route_for_turn`、快照
  持久化合并、fail-open 如实 FAILED、切片目标读取）、
  `service_generation_admission.py`（随运行配置写关系快照）、
  `service_retry.py`（克隆反馈关系、不重跑记忆）、
  `service_projections.py`（投影读取）。
- `chat/turn.py`：`profile_feedback_context` 提示块（修正回答直接重写上一轮；
  kind/adjustment/memory 文案分写入与未写入两类），接入日常与 study 路径；
  修正回合召回查询用「原问题 + 反馈文本」；`payload_budget` 新增
  `MaterialCategory.FEEDBACK_CORRECTION`（REQUIRED，缺失顺序
  memory→correction→feedback→profile）。
- `lifecycle/catalog.py`：`generation_runs` 导出增加脱敏 `feedback_correction`
  元数据（无用户原文与条目正文），随账户删除。

## 6. 剩余限制与边界

- 「这个专业以后先给结论」这类**带跨轮指代**的偏好反馈按用户原句原样写入，
  指代消解交由模型在修正回合理解；不重写用户措辞（不伪造事实）。
- 反馈路由是确定性规则：措辞非常规的反馈（如复杂嵌套否定）可能不路由而回落
  既有抽取/纠正路径，属保守取向；质量长跑由 18 票负责。
- 反馈消息不再进入普通异步抽取：同一条消息里附带的**额外新事实**（如
  「我不是研究生，我养了猫」）当前只处理反馈部分，混合新事实的拆分属后续
  票次范围。
- 导出为运行关系元数据（类别/状态/原回答与切片 ID），不含用户原文；
  若未来需要导出恢复反馈上下文，需另行评估隐私边界。
