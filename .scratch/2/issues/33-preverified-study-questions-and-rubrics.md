# 33 — 出题前冻结并核验覆盖计划与评分要点

**What to build:** 复盘仅在用户明确开始且范围有效时进入，题目和私有评分依据先核验后呈现，每次只显示当前题。

**Blocked by:** 12 — 主智能体理解混合入口与跨轮任务关系；31 — 核验知识范围覆盖并提交辅助预习

**Status:** ready-for-agent

**优先级：** P0

## 背景与需求

作答后才生成评分依据会随学生答案漂移；ID 合法不能证明题目测试了该知识或标准答案正确。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/study-workflow.md](../../../docs/workflow/study-workflow.md)。
- [docs/workflow/discussion.md](../../../docs/workflow/discussion.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。

## 任务内容

1. 冻结本节有效书页范围，生成覆盖计划、题目和评分要点，数量随密度覆盖主要知识；外部补充不自动纳入。
2. 每题保存稳定 ID、小节/范围版本、题干、知识点和片段、标准答案、核心要点、等价表述/推导、关键误解、不完整/错误依据及质量裁决。
3. 核验题干实际测对应知识、评分要点受书页支持、答案一致；确定计算用登记工具，不要求通用模型保证公式。
4. 私有要点作答前冻结、不随答案临时修改；自设条件明确标为题设，不冒充教材例子。
5. 全部题可内部保存，用户只看已呈现题；未来题/评分要点/标准答案在读取会话和 SSE 重放不提前泄露。
6. 呈现记录与可恢复对话内容同事务提交，选中下一题不等于已呈现；失败有限修复一轮，仍失败保留前有效阶段不发坏题。
7. 新评分合同与旧题兼容，旧题保留原判定，不宣称历史按新标准评分；复盘自然表达作用题干解释，不改判定/覆盖控制。

## 跨票接缝与责任

拥有题目/评分依据写模型和私有读边界；34 按冻结标准判定，35 仅重排未问题；领域仓库提交，模型不直接改题状态。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [ ] 开始意图否定/引用不进入复盘，只有合法阶段和有效范围才计划。
- [ ] 核心知识覆盖、评分依据和标准答案经内容门，不仅 ID/每页引用门。
- [ ] 等价表述/推导有预先规则，评分要点作答前冻结。
- [ ] API/重放不泄露未来题和提前答案，呈现事务和重复读取幂等。
- [ ] 核验失败不发不合格题、不推进阶段，旧题历史兼容可读。

## 验证与交付证据

覆盖 L02、L05、L06、L12；直接 API/浏览器/SSE 测私有字段与呈现提交故障。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## Comments

### 2026-10-04 实施与验证记录

**实现说明与接口/迁移变化：**

- `src/bridges/contracts/study.py`：新增 `StudyQuestionCheck`（status/detail/question_matches_knowledge/rubric_supported/answer_consistent/calculation_checked）；`StudyReviewQuestion` 增加 `scope_version_id/core_points/equivalents/key_misconceptions/incomplete_basis/incorrect_basis/conditions/verification/legacy` 与 `public_view()`（未判定题不暴露评分要点、等价表述、标准答案与核验裁决，只保留题干）；`StudyReview` 增加 `scope_version_id/protocol_version` 与 `public_view()`（客户端只收到 `asked` 题）；`STUDY_STATE_VERSION=3`，`upgrade_legacy_study_state` 对无 `verification` 的旧 v2 题标 `legacy=True` 并原样保留历史判定/标准答案，不宣称曾按新标准评分。
- `src/bridges/study/review.py`（重写）：`REVIEW_PROTOCOL_VERSION="study-review-v2"`，能力版本 `study.plan_review/study.verify_questions/study.grade/study.calculate`；稳定题号 `rq_` + `sha256(协议|范围版本|题干|知识点|片段)[:16]`。`plan_review` 只在阶段合法且 `scope.verified` 时出题；先过结构门（核心知识点覆盖、片段归属、题干/要点/答案非空、自设条件须明确标注为“题设”、拒绝重复与占位），再走独立内容核验 `study.verify_questions`（题干测对应知识、评分要点受书页支持、答案一致）；数值结论用登记工具 `evaluate_calculation`（AST 白名单，不调用 `eval`，指数绝对值 ≤ 12，除零/未知变量/非法语法一律 `ValueError`）复算。核验冲突、未通过、计算不符分别以 `study_review_verify_conflict` / `study_review_verify_unverified` / `study_review_calculation` 抛出 `ReviewPlanError`，`next_question` 另有防御守卫拒绝呈现未核验题。新评分合同下判定模型返回的 `canonical_answer` 被忽略，冻结答案不随学生答案漂移；`legacy=True` 旧题走既有判定路径并保留原标准答案，不进入新核验。
- `src/bridges/study/service.py`：复盘节点改为 `plan_with_repair()`——首次失败按公共预算做一次有界修复（`budget.begin_adjustment/end_adjustment`），仍失败就按具体错误码终止且不改阶段、不呈现坏题；错误码在 `src/bridges/state_copy/catalog.py` 登记为稳定用户文案。`review.py` 对 `bridges.chat.context_compiler` 与 `bridges.study.tutoring.page_sources` 改为函数内延迟导入，消除 `bridges.chat` 初始化链上的循环导入。
- 合同产物：`openapi.json` 与 `packages/contracts/src/generated.ts` 仅新增/更新 `StudyQuestionCheck`、`StudyReview`、`StudyReviewQuestion`（test 环境全量再生成，无 `/_test` 路径等无关漂移）；`tests/contracts/test_openapi_sync.py` 通过。
- 测试接缝：新增 `tests/chat/test_improvement33_preverified_questions.py`（9 项）；`test_v2_19_study_review.py` 的网关替身适配 plan/verify/grade 新合同；`test_improvement31_study_scope_preview.py` 的 `state_version` 断言 2→3；`test_v2_20_study_summary.py` 复用新网关。

**验证结果（conda `agent`，Windows，`PYTHONUTF8=1`）：**

- 本票验收 `tests/chat/test_improvement33_preverified_questions.py` **9 passed**：私有要点/标准答案冻结且读取与 SSE 重放不泄露、未判定题不出现；同名知识点分属不同页时依据不串；核验冲突一次修复后通过；持续冲突以 `study_review_verify_conflict` 终止且不改阶段、重试可恢复；登记计算工具纠正错误数值要点（`study_review_calculation`）；自设条件必须标注；T+提交中断不发题且重试只调一次计划；旧合同题保留原判定与标准答案且不触发计划/核验。
- 学习全链路 `test_v2_17/18/19/20` + `test_improvement31/33` **83 passed**（`.scratch/2/validation/33-final-study.xml`）；`tests/contracts` **3 passed**（`33-final-contract.xml`）。
- 全量：本票 `5355` 项 / 通过 `5130` / 失败或错误 `225`；干净 main 基线（同基点 `809f05a5`，`PYTHONPYCACHEPREFIX` 强制重编译）`5346` 项 / 通过 `5122` / 失败或错误 `224`。失败差集仅 `test_improvement13_acceptance::test_121_message_history_prepares_bounded_cache_and_converges`，该用例在 main 洁净检出单独运行与整文件运行同样失败（1 failed / 7 passed，两侧同形），属基线既有顺序/时序耦合；`main_only_failures` 为空，无学习/复盘相关新增失败。详见 `33-comparison.json`、`33-main-full.xml`、`33-final-full.xml`。
- 环境说明：`tests/integration/test_runtime_smoke` 12 项与 learning 1 项失败为迁移后环境问题（editable 安装指向已不存在的旧 Desktop 路径；旧 `__pycache__` 掩盖基线失败），已用新 `PYTHONPYCACHEPREFIX` 在 main 复现同形结果。
- `ruff` 改动文件通过、`mypy` 改动源码文件无报错（`33-final-ruff.txt`、`33-final-mypy.txt`）。

**限制与待验收项：**

- 网关替身只证明机制，真实模型的出题/核验/判定质量与 OCR/视觉体验由评测票 42 验证。
- `evaluate_calculation` 只登记四则、整除、取模、幂与变量表达式；模型未提供 `calculation` 时不做数值复算，也不因此拒绝题目。
- 旧题不重新核验、不改写历史判定（按兼容要求），仅新题进入冻结与核验路径。
- 本票待独立验收；分支 `codex/33-preverified-study-questions-and-rubrics`，工作树 `.worktrees/33-preverified-study-questions-and-rubrics`，基点 `809f05a5`，未合并、未推送。
