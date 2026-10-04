# 工单 34 独立验收与修复

日期：2026-10-04。固定审查基线 `main@48d12a454bb3f759d8a1ffcdb9a5b5732073501a`，原交付 `67e63c19`。原代理报告仅用于定位；本记录依据实际代码、独立测试和同环境 main 对账。

## Standards（规范符合性）

按 code-review 技能以独立规范轴审查 AGENTS、CONTEXT、执行说明及工作流规范，应用全部 12 项 smell baseline。原硬规范违反 0；判断性建议 2：`grade_kernel.py` 的 `top` 命名含糊、服务重复守卫模板。已分别改为 `review_scope_version_id`、提取 `_verify_review_commit()`。独立静态复验通过，无剩余阻止合入项。

共享内核只增加事务内领域提交回调，领域属主仍负责评分状态。消息属主提供 `update_message_content_in_transaction()`，没有跨领域直接写表。改动均服务本票验收缺陷，不重构其他任务。

## Spec（需求符合性）

原需求轴发现 5 项缺陷：下一题标记与正文分开提交、节点提交到领域提交之间存在停止窗口、合法答案仅判定成功时关联、空逐项证据可被放行、部分解释请求被批改。父代理另确认预期领域版本未在写事务核对。原交付隔离检出独立反例 **8 failed**，原文、测试快照及 XML 见 `../validation/34-review-original-red.*` 与 `34-original-regressions.py`，不能用原代理 7 项通过证明这些窗口安全。

已修复：

- 收到合法答案先按已呈现当前题、匹配范围、已核验范围/评分依据检查，再单独保存答案和来源消息；模型或复核失败不写学生判定、不推进游标。
- NodeKernel 的 `commit_effect` 在产物、收据、外箱事务内调用学习领域写入；反馈正文一起保存。任何领域写入失败整体回滚，消除已提交产物却缺领域判定的窗口。
- 下一题 `asked`、激活题与可恢复助手正文同事务；终态失败或停止后仍能读取真实已交付反馈/题面。已判定题停止后明确继续只补呈现，不要求重答、不重判。
- 每次写事务检查执行权及预期完整领域快照，按题 ID/范围版本/来源核对结果；迟到的范围或题目变化不被覆盖。
- 每个冻结核心要点必须有合法书页证据；初判及复核空证据均拒绝。解释请求走辅导，共用表达策略也用于复核的用户可见解释。
- 只有迁移明确标记的 `legacy` 题走旧合同，缺失新题评分依据不再自动降级；已有旧题标准答案优先保留，历史已判记录不重判。

最终独立需求轴复验通过；机制证据不等于真实模型语义质量证据。

## 逐项验收证据

| 工单验收要求 | 实际证据 |
| --- | --- |
| 等价答案通过、争议复核、标准不漂移 | `test_equivalent_answer_passes_recheck_and_frozen_standard_does_not_drift`；冻结 canonical、逐项 hit、revised 记录；旧题兼容回归 |
| 批改/结构/复核失败不跳题、不记学生错误 | `test_grade_failure_keeps_question_then_retry_commits_once`、`test_disputed_recheck_keeps_question_without_student_error`、空证据初判/复核、收到答案后模型失败；只记录收到答案事实 |
| 消息/答案/判定幂等、旧租约/取消不覆盖 | 原租约竞争；新增领域写失败事务回滚、节点提交后停止、终态中断恢复、范围迟到变化；现有 API/SSE/消息重试回归 |
| 错题反馈继续原计划，无补救/强制重答 | `test_wrong_feedback_continues_frozen_plan_without_remediation`；题 ID/数量/计划调用次数不变；停止后继续不重答 |
| 最后题判定保留，总结失败恢复不重判 | `test_last_judgement_survives_summary_failure_and_retry_does_not_regrade`；反馈和判定先保存，重试仅再调总结 |

附加任务要求：解释请求不批改（两类真实请求）；收答案前拒绝未呈现题、范围变化、未核验范围及未核验新题（四类）；判定/复核记录的导出、备份恢复、删除、审计及账户隔离由扩展的 `tests/lifecycle/test_issue33_review_lifecycle.py` 验证。

## 合同与迁移

图 `study-tutoring-review-v5`；判定协议/能力 `study-grade-v4`；登记配方 `study-review-grade-recipe-v2`；独立复核能力 `study-recheck-grade-v1`。新判定产物输入键含题目冻结信息、答案、来源与表达快照，旧配方产物不跨新证据门复用。评分标准不取判定模型改写。

`StudyReviewQuestion` 新增可选 `feedback/grade_record`，公开投影隐藏私有逐项记录及未判题答案依据；状态版本仍为 3，可选字段缺失可读取，不改变既有旧题迁移。新增状态沿用 study_states/node_artifacts 既有备份、导出、删除和账户作用域；OpenAPI 与 TypeScript 合同同步验证。

## 实际验证

全部开发/验证使用 conda `agent`、Windows、Python 3.11；最终命令 `PYTHONUTF8=1`。全量使用独立 PYTHONPYCACHEPREFIX，聚焦/故障验证使用隔离 basetemp，避免 editable 安装与历史缓存被当成通过证据。

- 首轮现有本票/前置/复盘/总结/合同/文案：82 passed。
- 原交付隔离检出：8 个新增反例全部失败，证明缺陷可复现。
- 修复后提交机制/合同/内核/本票生命周期：39 passed；旧断言修正组合 44 passed。
- 静态：最终修复文件 Ruff 通过；消息仓库原有 2 项 Ruff 与 main 同位置同规则，未改无关代码。Mypy 22 = 22，逐条错误集合相同，无新增。
- 扩展聚焦初跑 287 passed / 9 failed：其中 4 项是“收到答案不保存”的旧断言，已更正并复跑通过；5 项生命周期 API 测试在 main 独立复现，均为旧测试创建会话 API 返回 409、仍预期 201。本票生命周期、备份/删除/隔离机制另有实际通过测试，不能以这 5 项失败反推本票破坏生命周期。

最终组合、全量对账、合并与推送结果在完成后追加；尚未运行的结果不算通过。

### 最终复验（kind 缺陷闭合后，2026-10-04）

独立初审未闭合项：`_save_review_content` 追加的 delta 事件载荷缺少 `kind` 字段（原为 `{"message_id","delta"}`）。前端 `apps/web/src/lib/api.ts` 按 `event.data.kind` 收窄事件，`run_executor` 与契约 `ChatStreamDeltaData` 的 delta 载荷均携带 `"kind": "delta"`，缺失会导致判定反馈/下一题呈现的增量在订阅端被丢弃。修复为用 `ChatStreamDeltaData` 生成载荷；新增 `test_sse_replay_delta_frames_carry_frontend_kind`：解析重放 SSE 帧，断言 delta 载荷 `kind=delta`、`message_id` 匹配、拼接正文等于落库正文且反馈/下一题各出现一次。先还原旧载荷复现红（`KeyError: 'kind'`），修复后复跑绿，红→绿闭环。

- 分支聚焦（`34-review-final3-focused.*`）：**334 passed / 5 failed**；同组测试在干净 main `eb85c064`（`34-review-final3-main-focused.*`）**313 passed / 同一 5 项 failed**。逐测试 ID 对账：**0 项 main 通过而分支失败**（无新增回归）；21 项分支新增/新增参数化测试全绿；无 main 独有测试。
- 5 项失败均为 `tests/lifecycle/test_lifecycle_api.py` 的会话创建 409（仍预期 201），在 main 同环境逐项复现，属既有环境/生命周期基线问题；本票 `tests/lifecycle/test_issue33_review_lifecycle.py` 全绿。
- Ruff：本次改动 Python 文件通过（`34-review-final3-ruff.txt`）；`src/bridges/chat/repository.py` 残留 2 项（N818、E501）在 main 同文件同位置复现，属既有问题，未改无关代码。
- mypy：同命令分支 22 项、main 22 项，逐条错误集合完全相同，无新增（`34-review-final3-mypy.txt`）。
- `git diff --check` 干净；`openapi.json` 重生成后哈希一致（311 paths）；`generated.ts` 用 `openapi-typescript@7.13.0` 重生成后仅行尾差异，规范化后逐字节一致。
- 验收期间 main 由 `48d12a45` 前进到 `eb85c064`（工单 41 已合入）；上述对账均以当前 main 为基线，合并目标为 `eb85c064`。`study-grade-v3` → `study-grade-v4`、评分配方 `study-review-grade-recipe-v1` → `v2`（复核处置语义收紧），图版本仍为 `study-tutoring-review-v5`。

## 交付状态

- 合并（2026-10-04）：`codex/34-verified-study-grading-and-feedback`（`76649c0c`）以 no-ff 合入 main `eb85c064`，合并提交 `e9b1e758`，无冲突。合并树重跑同一聚焦集：**334 passed / 同一 5 项基线失败**（`34-review-merged-focused.*`），与分支结果一致。
- 推送：`git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 push origin main` 输出 `eb85c064..e9b1e758 main -> main`；`git ls-remote origin main` = `e9b1e7584c307c1125a2b34b5194c0f8f94715f1`，与本地 `main`、`origin/main` 三方一致。
- 清理：独立确认 Issue 工作树无未提交的受跟踪改动（仅被本记录替代的中间日志），已删除工作树 `.worktrees/34-verified-study-grading-and-feedback` 与本地分支 `codex/34-verified-study-grading-and-feedback`，并执行 `git worktree prune`。其他任务的工作树/分支（`.worktrees/37-composite-plans-and-verified-synthesis`、`.worktrees/26-github-requirement-evidence-matrix` 及各自分支）未改动、保留。

## 剩余限制

确定性网关与故障注入证明提交/恢复/版本机制，真实模型等价判定、复核科学质量及反馈体验仍由评测票 42 验证。未新增真实模型调用评测；没有把模型一致性当独立事实来源。本票没有改前端渲染代码，正式 API 与生成合同已验证；没有新增真实浏览器联调证据。全量既有失败会逐项对账，不宣称仓库全绿。
