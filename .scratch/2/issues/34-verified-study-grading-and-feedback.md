# 34 — 幂等判定作答并执行一次覆盖反馈

**What to build:** 学生按冻结要点得到正确、不完整或错误判定及简短解释；反馈后继续未问题，不自动补救或要求重答。

**Blocked by:** 33 — 出题前冻结并核验覆盖计划与评分要点

**Status:** ready-for-agent

**优先级：** P0

## 背景与需求

系统批改失败不能成为学生错误；判定、反馈与下一题呈现需要独立可恢复提交边界。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/study-workflow.md](../../../docs/workflow/study-workflow.md)。
- [docs/workflow/discussion.md](../../../docs/workflow/discussion.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。

## 任务内容

1. 区分作答、解释请求、暂停、补页等输入；只将合法当前题答案关联题 ID/范围版本/来源消息。
2. 逐项核对预先要点，记录命中/缺失/矛盾及书页证据；等价表述不扣分。争议、计算疑点或核心冲突触发独立复核。
3. 模型失败、非法结构或必要复核失败保留当前题，不推进游标/掌握判断，不记学生错答；已提交合法判定不因重试重生成。
4. 按预期小节/题版本、租约/停止/权限守卫幂等提交判定、反馈产物和可重放事件；提交后中断保留合法结果，迟到未过守卫结果不得覆盖。
5. 正确给必要肯定，不完整/错/不知道给答案与简短解释，再进入下一道未问题；不追加补救/迁移题，不要求补答原题。
6. 呈现、收答案、已判定分别记录，下一题呈现有自己的提交边界；最后一题判定先保存，36 总结单独执行，不将总结失败变成重判。
7. 自然表达通过共用策略，评分依据/正确性/覆盖游标保持不变。

## 跨票接缝与责任

拥有作答判定与反馈原子边界；35 管暂停恢复，36 管总结；内核可在已提交反馈后继续运行，不要求每模块先 DONE。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 等价答案按冻结要点通过，争议进入复核；标准不随作答漂移。
- [x] 批改/结构/复核失败不跳题、不记学生错误。
- [x] 用户消息、答案和判定幂等；旧租约/取消竞争不重复或覆盖。
- [x] 错题反馈后继续原覆盖计划，无自动补救题和强制重答。
- [x] 最后题判定成功后总结失败仍保留反馈和判定，恢复不重判。

## 验证与交付证据

覆盖 L06–L07、L10–L11、R01–R03，故障注入判定/反馈/下一题呈现之间并检查真实领域记录。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。


## Comments

### 2026-10-04 实施与验证记录

**实现说明与接口/合同变化：**

- `src/bridges/contracts/study.py`：新增 `StudyPointCheck`（point/status/fragment_ids）与 `StudyGradeRecord`（judgement/point_checks/recheck_status/source/explanation）；`StudyReviewQuestion` 增加 `feedback`/`grade_record`，`public_view()` 隐藏 `grade_record`、仅在判定后展示 `feedback`；`STUDY_STATE_VERSION` 仍为 3（新增字段均可选，旧状态原样可读，无需迁移重写）。
- `src/bridges/study/review.py`：`REVIEW_PROTOCOL_VERSION` 保持 `study-review-v2`，新增 `GRADE_PROTOCOL_VERSION="study-grade-v3"` 与能力 `study.recheck_grade`（`study-recheck-grade-v1`）；`_point_records` 要求逐项精确覆盖全部冻结 core_points、证据限题目片段、判正确时不得含缺失/矛盾；判定非 correct 或模型标记争议时触发独立复核，`confirmed/revised` 落库、`conflict` 抛 `study_review_disputed`、`insufficient` 抛 `study_recheck_failed`，均保留当前题不推进；标准答案一律取作答前冻结值，忽略模型返回。
- `src/bridges/study/grade_kernel.py`（新增）：`ReviewGradeKernel`，配方 `study-review-grading`、节点 `study.grade`、门 `study.review_grade_committed`、产物 `study.grade_result`；判定与反馈产物、收据、可重放事件同事务按守卫提交；同来源消息按输入键复用已提交产物，不重新判定。
- `src/bridges/study/service.py`：图版本 `STUDY_GRAPH_VERSION="study-tutoring-review-v5"`；判定落库、下一题呈现落库、总结落库拆为独立守卫提交边界；`reviewed_committed` 终态只写消息正文；`_replay_judged` 按来源消息重放已提交反馈，激活题未被呈现时补提交下一题呈现。
- `src/bridges/chat/service.py`：旧图安全路由加入 `study-tutoring-review-v4`。
- `src/bridges/state_copy/catalog.py`：登记 `study_grade_invalid`、`study_review_disputed`、`study_recheck_failed`、`study_grade_budget`、`study_review_scope_changed` 五个错误模板；`study.review.feedback` 渲染器改为 `bridges.study.review.render_feedback`（原 `grade` 已随重构移除）。
- 合同产物：`openapi.json` 与 `packages/contracts/src/generated.ts` 重新生成（新增 `StudyPointCheck`/`StudyGradeRecord` 及题上字段），`tests/contracts` 通过。

**验证结果（conda `agent`，Windows，`PYTHONUTF8=1`）：**

- 本票验收 `tests/chat/test_improvement34_verified_grading_feedback.py` **7 passed**：等价答案经复核改判且冻结标准不漂移；复核 `conflict` 保留当前题、不记错答、重试可恢复；判定模型失败保留当前题且重试恰好提交一次、已提交判定重试不再生成（409）；故障注入「判定已提交、下一题呈现中断」→ 重试只重放反馈并补呈现，判定模型只调 1 次；最后一题判定成功后总结失败保留反馈与判定、重试只补总结（grade 1 次 / summarize 2 次）；错题反馈后按冻结计划进入下一未问题（题量不变、plan 1 次、复核 1 次）；旧租约迟到判定被守卫拒绝（`lease_lost`）且研究状态零变化。
- 聚焦回归 **212 passed**（学习链 `v2_17/18/19/20`、`improvement30/31/32/33`、`improvement33_acceptance`、`issue31_independent_acceptance`、`lifecycle/issue33_review_lifecycle`、`tests/contracts`、`tests/state_copy`；记录 `.scratch/2/validation/34-final-focused.xml`）。
- 全量：本票分支 `221 failed / 5203 passed / 39 skipped / 2 errors`；main 洁净基线（同基点 `48d12a45`）`223 failed / 5196 passed / 37 skipped / 2 errors`。按测试 ID 差分 **本票新增失败为 0**，两条 runtime 端口/时序用例仅方向相反地抖动；失败均为该开发环境既有基线失败（抽样在 main 对 chat/mcp/plugins/learning_projects/tieba/runtime 代表用例单跑同样失败）。详见 `.scratch/2/validation/34-full-baseline-compare.txt`。
- `ruff` 改动文件全通过（`.scratch/2/validation/34-final-ruff.txt`）；`mypy` 与 main 同形：同命令下 `22 errors in 10 files`，均为既有其他模块错误，本票源码无新增（`.scratch/2/validation/34-final-mypy.txt`）。

**限制与待验收项：**

- 网关替身只证明确定性机制；真实模型的等价判定、复核质量与反馈文案体验由评测票 42 验证。
- 本票未合并、未推送，待独立验收：分支 `codex/34-verified-study-grading-and-feedback`，工作树 `.worktrees/34-verified-study-grading-and-feedback`，基点 `48d12a45`。
