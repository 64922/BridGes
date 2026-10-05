# 35 — 恢复暂停复盘并原子更新追加书页版本

**What to build:** 用户暂停去辅导后从未问题继续；追加同节书页成功才更新范围，已问题与判定保持原版本历史。

**Blocked by:** 30 — 按页恢复书页识别并定位关键材料疑点；31 — 核验知识范围覆盖并提交辅助预习；33 — 出题前冻结并核验覆盖计划与评分要点；34 — 幂等判定作答并执行一次覆盖反馈

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

追加页会改变范围和未问题，失败不能覆盖原有效小节；暂停后的辅导消息不能被当答案。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/study-workflow.md](../../../docs/workflow/study-workflow.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。

## 任务内容

1. 暂停回辅导清除当前激活答题等待，保留已呈现/已答/已判来源；辅导消息不进批改。无新增页时恢复未问题，已呈现未答题单独记录。
2. 追加同节照片创建待提交更新版本，复用未变化页识别产物，识别新增页/合并范围/核验，必要步骤全部通过再原子切换版本。
3. 识别/范围失败保留原有效阶段与范围，重试只处理受影响页；新页冲突原文或用户补录时保留双方来源，关键冲突先解决。
4. 已问题与原判定不改写；尚未问题和新范围重规划，旧总结失效为当前依据但保留对应历史版本。
5. 预习保留原范围关系，不宣称覆盖新增页；历史表现因依据变化受影响时在新总结标清，不继续当无争议掌握证据。
6. 停止/取消/版本变更时拒绝迟到提交，依赖失效只传播到受影响产物，恢复遵守原运行预算。

## 跨票接缝与责任

拥有小节更新事务和未问题重排/暂停等待，30/31/33 提供各阶段产物，36 消费版本化表现。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 暂停辅导不被批改，继续默认未问题，已呈现未答不当答对。
- [x] 追加失败原有效范围完整，成功后仅未问题重排，已题判定保留。
- [x] 未变化页复用，新增关键冲突未解决不切有效版本。
- [x] 旧总结/预习范围标记准确，后续总结不冒用旧版本。
- [x] 更新/停止/重启竞争保持幂等、版本守卫与账户隔离。

## 验证与交付证据

覆盖 L08–L09、L13，跨追加版本/暂停/恢复/晚返回做故障测试。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。


## Comments

### 2026-10-05 实施与验证记录

**实现说明与接口/合同变化：**

- `src/bridges/contracts/study.py`：新增 `StudyPage.superseded_fragments`（补拍取代的原识别片段，只作历史证据）、`StudyReviewQuestion.unanswered`（已呈现未答）、`StudySummaryRecord`（summary/scope_version_id/superseded_reason）与 `StudyState.summary_history`；`STUDY_STATE_VERSION` 3→4，新增字段均有默认值，`upgrade_legacy_study_state` 说明 v3→v4 原样可读、不猜测改写历史。
- `src/bridges/study/service.py`：新增 `_mark_active_unanswered()`，在暂停复盘、转辅导、追加页切换使当前激活题作废时记录 `unanswered=true` 并清除激活题（题号/来源/已判定保留，批改只发生在作答消息）；`finish_pages` 提交时把旧总结移入 `summary_history`（标注原 `scope_version_id` 与失效原因）并置空当前总结，答案文案说明原总结作为历史保留；补拍替换时旧页 `fragments` 与既有 `superseded_fragments` 一并移入新页历史；新增 `_verify_update_commit()`，在 `persist_learning` 写事务内核对停止信号、共享执行权守卫（豁免 `finalize_message` 已收敛的 `message_terminal`；租约/运行终态照常拒绝）与有效状态（排除 `page_update`/`pending_object_ids`），不通过则整体回滚并保留候选可重试；与既有 `_verify_review_commit()` 共用 `_verify_material_commit()` 守卫骨架。
- `src/bridges/study/summary.py`：新增 `question_basis_current()`（题目已记录支持片段是否仍属当前有效范围；未记录支持片段的遗留题没有依据变化证据，维持原判定语义）；`_verify` 要求 mastered 只来自 `correct` 且 `basis_current=true` 的题，依据已变化的历史正确判定必须计入 gap，`unanswered` 必须计入 gap；`build_summary` 数据补充 `basis_current`/`unanswered`/`scope_version_id`；`_ref_labels` 抽出 `_question_label()` 并标注“（依据已更新，需重新确认）”与“未作答（已呈现，未计入掌握）”；`_RULES` 同步收紧总结规则。
- `src/bridges/state_copy/catalog.py`：登记 `study_page_update_changed`（STATE_CONFLICT/REFRESH_STATE），用于追加版本守卫拒绝迟到提交。
- 合同产物：`openapi.json` 与 `packages/contracts/src/generated.ts` 重新生成（311 paths，新增上述字段与 schema），`tests/contracts` 通过。
- 持久状态生命周期：无新表/新对象存储，新字段随既有 `study_states` JSON 持久化；旧状态读取自动升级，账户导出含 `summary_history` 原文，删除/备份沿用既有生命周期，无新增审计事件。

**验证结果（conda `agent`，Windows）：**

- 本票验收 `tests/chat/test_improvement35_study_pause_and_appended_versions.py` **12 passed**：暂停不批改且恢复从未问题开始；未答不得计入掌握（总结超报红、修复后绿、未答题在总结保留事实）；追加 OCR/范围映射失败保留原页/范围/总结且重试复用识别产物（`vision_count==2`）；范围冲突阻断与补录解除；关键疑点未解决不切有效版本；补拍保留旧原文为 `superseded_fragments`；总结历史版本化与预习范围关系、后续总结不冒用旧版本；停止/重启/并发写竞争下追加被守卫拒绝且零覆盖；依据变化总结单测。
- 聚焦回归 **190 passed**（study v2_17–v2_20、study fixtures/recognition、improvement30–34、contracts；`.scratch/2/validation/35-review/final-focused.xml`）。
- 全量（工作树分支最终态 vs 同基点 main `3665008b`）：分支 `222 failed / 5314 passed / 39 skipped / 2 errors`；main 洁净基线 `225 failed / 5301 passed / 37 skipped / 2 errors`。采集数 +12 为本票新增测试且全绿；仅 main 失败的 4 项为既有环境/时序波动（paper deadline、secret scan，runtime 端口/启动 2 项在无 Web 构建的工作树按 `NEEDS_WEB_BUILD` 转 skip）。分支出现 1 项既有顺序相关抖动 `tests/learning/test_teaching_progress.py::test_answer_assessment_drives_remedial_next_action`：该用例在未改动的 main 工作树（同基点）单跑同样失败、分支上整目录 `tests/learning` 90 passed、learning 模块不 import study，且同一命令在 main 与分支首跑均通过，判定与本票无关。详见 `.scratch/2/validation/35-review/35-full-baseline-compare.txt`、`main-full.txt`、`branch-full.txt`。
- `ruff` 改动文件全通过（`.scratch/2/validation/35-review/final-ruff.txt`）；`mypy` 与 main 同形 `22 errors in 10 files`，均为既有其他模块错误，本票三个源码文件无新增（`.scratch/2/validation/35-review/final-mypy.txt`）。

**限制与待验收项：**

- 网关替身只证明确定性机制；真实模型的未答总结、依据变化标注与补拍识别体验由评测票 42 验证。
- 本票未合并、未推送，待独立验收：分支 `codex/35-study-pause-and-appended-page-versions`，工作树 `.worktrees/35-study-pause-and-appended-page-versions`，基点 `3665008b`。

### 2026-10-05 独立验收与修复

依据工单、规范、实际代码和本次独立执行，修复后达到本票确定性验收标准。上节为编码代理历史交付，独立证据以 [验收报告](../acceptance/35-study-pause-and-appended-page-versions.md) 为准。

- code-review Standards / Spec 双轴独立审查并复审完成。需求轴发现候选写覆盖并发有效状态、旧图恢复不兼容两项；主审反例另发现持久停止被终态豁免跳过、已答未判标签误标未答。修复源码提交 `7b464f98`，图升级 v6，历史 v5 安全结束后显式重试。
- 原交付独立竞争反例 4 failed / 1 passed、标签反例 1 failed；修复后独立新回归 7 passed，最终隔离字节码缓存组合 22 passed，边界/总结组合 30 passed。
- 最终扩展组合 235 passed / 5 failed；同 5 项生命周期 API 失败在 main 精确复现。本次全量 main 5301 passed / 225 failed / 37 skipped / 2 errors，分支 5320 passed / 222 failed / 39 skipped / 2 errors。唯一仅分支失败为新增生命周期旧夹具的默认值断言，已修正并复验；未把它归为旧基线失败。最终没有剩余新增产品回归。
- 新状态实际导出、备份恢复、删除、账户隔离通过；Ruff 通过；Mypy 全 src 与 main 同一 108 项错误集合；OpenAPI/TypeScript 再生一致。验收日志凭据匹配值已脱敏，计数/标识/断言保留。
- 合并、推送提交及清理状态见验收报告交付段。真实模型、外部服务和浏览器体验继续按评测票验证，本次不宣称仓库全绿。

