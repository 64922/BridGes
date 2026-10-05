# 36 — 按本次表现总结并单独恢复总结失败

**What to build:** 总结如实区分本次涉及、答对、漏洞、未答和未判定内容；总结失败可独立重试，不改变已提交判定。

**Blocked by:** 32 — 按问题充分性逐层补证并自然辅导；34 — 幂等判定作答并执行一次覆盖反馈；35 — 恢复暂停复盘并原子更新追加书页版本

**Status:** ready-for-human

**验收结论：** 2026-10-05 独立验收通过；详见 [验收报告](../acceptance/36-evidence-bound-study-summary.md)。

**优先级：** P1

## 背景与需求

单次答对不证明长期掌握，未作答也不等于错误；最后题判定与总结混合会造成重判或重复要求回答。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/study-workflow.md](../../../docs/workflow/study-workflow.md)。
- [docs/workflow/discussion.md](../../../docs/workflow/discussion.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/用户画像/提取时机与回答应用流程.md](../../../docs/用户画像/提取时机与回答应用流程.md)。

## 任务内容

1. 只用实际小节范围、辅导和复盘记录，答对/漏洞结论逐项关联题目/评分记录与版本。
2. 书页支持而没有用户表现只能说涉及/学过，未答不当错答或掌握；无依据掌握百分比和全局能力标签不生成。
3. 总结不自动建立长期已掌握画像，不追加问题/补救课程/下一节任务；用户主动再讲回本节辅导，新小节另建学习会话。
4. 判定成功先保存反馈，独立总结节点通过质量门后提交；失败持久待总结状态，只重试总结、不重判最后题或重复作答。
5. 追加页/来源冲突使当前总结依赖失效，历史总结保留范围版本；教学阶段推进须领域服务预期版本守卫。
6. 既有总结生成调用复用 21/22（可用时）的表达与切片快照，事实和表现不被风格改写，04/09 限制预算。

## 跨票接缝与责任

拥有总结产物/待总结恢复；34 判定权威、35 小节版本权威，17 不把总结作为用户原话提取事实。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 答对和漏洞有实际题目依据，未答/未判单列，不输出伪掌握百分比。
- [x] 最后题已提交而总结失败只恢复总结，反馈/判定不重复。
- [x] 新页范围与总结版本一致，旧总结历史可读。
- [x] 不自动补题、长期掌握画像或下一节，主动追问才回辅导。
- [x] 自然表达不改变实际表现/缺口，停止/重放/导出一致。

## 验证与交付证据

覆盖 L10、L13 和无复盘/部分作答/追加页场景，断开总结调用后检查状态与重试边界。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## Comments

### 2026-10-05 实施与验证记录

**实现说明与接缝/合同变化**

- `src/bridges/contracts/study.py`：`StudySummaryPoint.kind` 增加 `unanswered`；`StudySummary.scope_version_id: str = ""` 由系统在核验后确定性盖章，不采用模型输出。无新增持久状态版本（新字段均有默认值），未引入新表/新对象存储；`study_states` JSON、导出、备份、删除沿用既有生命周期。
- `src/bridges/study/summary.py`：
  - 四章节：学到了什么 / 复盘已掌握 / 还需补的点 / 未作答或尚未判定；未答或未判必须单列，且不得写进掌握或漏洞。
  - 逐条证据：learned 必须引用真实书页片段；mastered 只来自 `correct` 且 `basis_current=true` 的题；gap 必须引用判定不完整/错误或依据已更新的题（书页支持不能充当漏洞，答对题不得同时进漏洞）；unanswered 必须对应已问未答或未判题。
  - 质量门 `_reject_unfounded_claims` 确定性拒绝伪掌握百分比（掌握度/率/百分比、带百分比的量化正确率等）、全局能力标签（整体/综合/全局+掌握/水平/能力/表现、天赋/智商）；学习条目另拒绝“已掌握/学会了”式宣称（“要求掌握”等书页目标措辞放行）。语义层越界仍由提示词规则与评测票 42 约束。
  - 生成材料新增每题 `checks`（评分记录逐项摘录）、`recheck_status` 与每题 `scope_version_id`，答对/漏洞逐项关联题目、评分记录与版本。
  - `_policy_block` 只复用 `snapshot_complete=true` 的完整表达策略快照（21/22 采用路径产物），降级/不完整快照退回无策略块的基线提示词；预算沿用 04/09 `compile_turn_context`，证据块不入预算即失败不静默丢题。
- `src/bridges/study/service.py`：`finish_pages` 追加/补拍把当前总结移入历史时，优先采用总结自身绑定的生成范围版本，旧状态回退运行启动时的有效范围。
- 失败恢复：无新持久标志；待总结状态由已持久化的 `review.complete=true + summary=null + stage ∈ {review, summary}` 表达（34 幂等路径），重试只跑总结节点，`_replay_judged` 不重判、反馈不重复。
- 前端消费者同步（合同 enum 扩展后不再丢段）：`apps/web/.../StudyProgress.tsx` 增加“未作答或尚未判定”段并同步空段文案，证据行区分“已作答（尚未判定）/未作答”；单测与 e2e 夹具同步，测试夹具补齐 `state_version`/`unanswered`/`scope_version_id` 必填字段。
- 合同产物：`openapi.json` 311 paths（+8/-2）；`packages/contracts/src/generated.ts` 用 openapi-typescript 7.13.0 再生（+11/-2，行尾统一后仅预期字段/描述差异）。

**验证结果（conda `agent`，Windows）**

- 本票测试 `tests/chat/test_improvement36_evidence_bound_summary.py` **10 passed**：四类分离且逐项关联评分记录与范围版本；总结失败持久待总结、重试只重试总结（判定调用数不变、反馈不重复、重放/导出一致）；追加页总结入历史保留原版本、新总结盖章新版本且历史可读；不自动补题/画像/下一节，用户追问才回辅导；无复盘不生成总结；伪掌握百分比与全局能力标签拒绝；未答/未判必须单列；漏洞必须绑定真实弱题/依据已更新题且不得与掌握矛盾；学习条目不得宣称掌握；21/22 完整快照复用、降级快照不复用。
- 聚焦组（study v2_17–v2_20、improvement30–36、fixtures/recognition、contracts、lifecycle、state_copy）：分支 **275 passed / 5 failed**，main 同命令 **265 passed / 5 failed**，差异 +10 passed / +0 failed；两侧相同的 5 项均为 `tests/lifecycle/test_lifecycle_api.py` 注册后创建 companion 会话 409 的既存问题，与本票无关。
- 全量 `tests/`（-n auto）：分支最终 229 failed / 5365 passed / 37 skipped / 2 errors；同基点 main 228 failed / 5356 passed / 37 skipped / 2 errors。采集 +10 为本票测试；失败集合差异仅 2 项 xdist 时序/端口抖动（串行单跑均通过）与 1 项 main-only 环境波动，本票相关模块无新增失败。
- `ruff` 改动文件全通过；`mypy src/bridges` 分支与 main 同为 108 errors in 22 files 且集合一致，本票源码文件无错误。
- Web（npm ci 后）：`npm run typecheck` 分支 14 errors（全在未改动文件）vs main 基线 25，无新增；`npx vitest run StudyProgress.test.tsx` **8 passed**；`npm run build` 成功；`npm run lint` 仅既有 warning。Playwright e2e 未跑（需 .venv+API/worker/假邮件/Web 全套栈，留给集成验收）。
- 证据：`.scratch/2/validation/36-review/`（final-focused-final、branch-full-final、main-focused、main-full、final-mypy、full-compare；web 日志见同目录说明）。

**限制与待验收项**

- 契约替身只证明确定性机制；真实模型的总结措辞、未答表述与风格遵循由评测票 42 验证；确定性质量门只拦可识别式样，语义越界依赖提示词规则与评测票，如真实模型反复越界需在 42 回补规则。
- 本票未合并、未推送，待独立验收：分支 `codex/36-evidence-bound-study-summary`，工作树 `.worktrees/36-evidence-bound-study-summary`，基点 `b1da9afc`。

