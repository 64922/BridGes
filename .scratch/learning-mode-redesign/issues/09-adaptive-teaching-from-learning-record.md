# 09 — 按学情调整后续教法与路线

**What to build:** 导师实际利用相关、已核验且未撤回的学情调整例子、提示、前置知识和必要路线；用户能理解调整原因并改变安排，档案不只是一个可查看的报告。

**Blocked by:** 06 — 档案纠正、删除与迟到结果保护；07 — 系统性目标与简短学习路线。

**Status:** ready-for-human

**发布状态：** 用户已确认拆分与依赖，正式发布；尚未实施。

**对应合同：** D01、D03、D04、D08、D10；AC01–AC03、AC06、AC08、AC11；C01。

## 用户演示

1. 用户曾混淆独立与互斥，相关诊断已核验；之后学习条件概率，导师有针对性地检查或补讲这个前置概念。
2. 只有提示后完成时，导师选择适当独立应用，而不是直接宣称已经稳定掌握。
3. 用户纠正/删除记录后，同样请求不再沿用旧误判。
4. 用户不想补讲而要求继续，导师尊重选择，路线保存真实未验证状态。

## 实施范围

1. 通过 06 的有效读取边界选择相关档案：按知识上下文、目标和证据适用性，不固定取最近五条，不注入整个档案。
2. 本轮形成唯一采用快照，带记录/来源/版本；待核验、失败、失效、已删和不相关记录不进入正式学情上下文。
3. 使学情影响实际教学动作及内容：提示粒度、例子、前置知识补讲、迁移应用和必要复核；不能仅在结尾加一句「已结合画像」。
4. 普通教法变化在当前教学中完成；需要修订路线时按目标、路线版本和用户约束生成建议并保存修订原因，不每轮调用整套规划。
5. 用户明确目的、直接讲解、跳过和改方向优先。补讲建议不能强制阻止继续，档案不能反向修改学习目标。
6. 在后续模型调用前复查采用记录的有效性；编译期间的纠正、删除、撤回或目标版本变化使旧快照失效，不能继续外发旧信息。
7. 后台尚未完成或档案不足时，仍依据当前回答教学并保持不确定性；不能提前采用猜测诊断，也不能把档案失败当停课条件。
8. 验证新作答与旧表现矛盾时保留证据和当前不确定性，不固定贴标签；知识点上下文不相同时分别处理。

## 接口、持久化与界面

- 记录采用哪些有效学情及简短调整原因，审计只含必要引用/状态，不记录整份私人正文。
- 路线变更及用户选择可追溯，当前进展与采用快照有版本关系；失败不虚报保存。
- 聊天中可简短说明「先补这个前置点」，学习详情按需查看依据；不展示模型内部推理。
- 原子画像中的明确偏好只按原授权切片使用；学情与画像不混写，关闭/撤回原画像的间接使用也不得绕过。
- 新采用/修订记录完成生命周期；来源失效不重写历史消息，但停止后续采用。

## 范围边界与依赖理由

06 使记录的纠正/删除与读取语义可靠，07 提供实际可改路线。这两项都是真实前置。08 的多会话控制不阻塞本票的单会话自适应；整合旅程由 13 覆盖。

## 验收标准

- [ ] 正式采用仅含相关、已核验且有效的学情，不固定最近五条或整份注入。
- [ ] 同一学习请求在不同真实学情下产生可观察的教法差异。
- [ ] 提示后完成与独立完成影响合理，单次答对不被提升为长期掌握。
- [ ] 用户跳过/直接讲解/原目标优先，补讲不能强制锁住路线。
- [ ] 编译后、调用前的纠正/删除使旧采用失效，后续请求不携带旧判断。
- [ ] 未完成诊断和失效来源不被当作正式事实，无档案仍能继续学习。
- [ ] 真实矛盾保留不确定性，同名不同知识上下文不串用。
- [ ] 必要路线修订有版本与原因，普通教学不额外每轮完整规划。
- [ ] 可见依据、审计、生命周期及 C01 审查完成。

## 验证与交付证据

- 配对运行相同目标/输入、不同已核验表现，检查实际模型载荷和动作，而非只比较文案字段。
- 在上下文编译与模型发送之间删除/纠正记录，证明旧采用被撤回。
- 覆盖待诊断、诊断失败、不相关误区、同名不同概念、用户拒绝补讲和路线保存冲突。
- 提交至少一条「实际作答—正式诊断—后续教学改变」的完整反馈旅程；真实收益由 13 实测。

## Comments

2026-10-13：诊断反馈教学切片草稿，待拆分确认。

2026-10-13：用户确认「按此发布」，本票发布到本地问题跟踪器。分诊状态表示信息齐备；须待直接阻塞票验收完成后实施，本轮未开始编码。

2026-10-14：开始实施。前置核对：06 已交付并合入 main（`44c472f0`，含纠正/删除、迟到保护与唯一有效读取边界 `record_boundary`/`effective_records`），验收状态 `ready-for-human`；07 已交付并合入 main（`78e9889c`，账户级目标与路线版本），其工单头部 `Status` 字段仍是 `ready-for-agent`（交付记录见本文件 Comments，功能与验证已完成并入 main）。两者均已被本票消费的接口/表结构实际落地。固定 Git 基线为 main `0534f0db`（含 06 交付记录）。工作树 `.worktrees/09-adaptive-teaching-from-learning-record`，分支 `codex/issue-09-adaptive-teaching-from-learning-record`，开发与验证使用 conda `agent`（Python 3.11.15）。本票负责的共享接缝：新增教学采用快照（选择/复验/渲染/审计/提交）与迁移 v78（合并时 08 已占用 v77，本票迁移顺延为 v78）；消费 06 的 `effective_records`/`record_boundary` 与 07 的目标/路线版本；不修改 08 的跨会话续学语义、不接触 10 的材料读取与 11 的查证快照。

2026-10-14（实施与验证记录）：
- **合同与快照：** 新增 `contracts/study_adaptation.py`（`ADAPTATION_CONTRACT_VERSION="study-adaptation-v1"`）：`StudyAdaptationAdjustment`（补讲前置/独立应用/迁移应用/复核/提示粒度）、`StudyAdoptedRecord`（记录标识+版本+结论+表现+调整方向+原因）、`StudyAdaptationSnapshot`（adoption_id、会话、目标/路线版本、有界条目、不确定性说明）、`StudyAdoptionProjection`（用户可见采用记录）。新增 `study/adaptation.py`：`StudyAdaptationService.load_or_compile` 只经 06 的 `effective_records`/`record_boundary` 读取，确定性 `select_relevant_records`（只收有效+已核验+独立完成/提示后完成/具体误解；按知识上下文/目标/路线/当前消息相关性排序，上限 3 条；同名不同上下文必须有用途上下文二次匹配才采用；待进一步验证只进不确定性说明）；`render_directive` 把采用落到提示粒度、例子、前置补讲、迁移应用或复核（禁止只在结尾补「已结合画像」、禁止能力标签、声明用户控制优先）；`planner_context` 供验证规划；`revalidate_snapshot` 按记录版本/有效性/目标与路线版本复验；`suggest_route_revision` 仅当前置链命中且用户无控制语义时回退，按路线版本守卫提交；`StudyAdaptationTurnState` 在服务层与回合编排间共享本轮状态。
- **持久化与生命周期 v78：** 迁移 v78 新增 `study_record_adoptions`（adoption_id 主键、账户/会话外键 CASCADE、run/assistant 消息、目标与路线版本、status/invalid_reason、entries_json、不确定性说明、合同版本、时间戳）与账户索引；`REQUIRED_TABLES`/`REQUIRED_TABLE_COLUMNS`/`REQUIRED_INDEXES` 登记；`ACCOUNT_TABLES` 置于档案表之前，导出类别 `study_record_adoptions`（「学习教法调整采用记录」），逻辑清单/快照恢复/账户删除贯通。采用行在消息 done 终态事务内写入（停止/失败不写、撤回不虚报；`adoption_id` 冲突忽略使重试不重复）。
- **接线（真实链路）：** `chat/service_stream.py` 在导师轮编译唯一采用快照（读局部进展/目标/路线）、审计 SUCCESS 并把快照冻结进运行配置 `study_adaptation`；终态回调在 done 事务内提交采用行与路线修订（`save_route_revision_in_transaction`，版本守卫失败如实跳过）。`chat/turn.py` `stream_turn` 新增 `study_adaptation_block`/`study_adaptation_state`：区块进入导师系统提示词；规划角色调用前与主生成调用前各复验一次（失效即从历史撤回区块、置 `invalid_reason`、审计 BLOCKED 且不写采用行），主生成确实携带仍有效快照时才标记 applied；`_prepare_study_verification` 传入 `learning_context` → 规划请求新增 `learning_adaptation` 键；规划系统提示词补充采用语义（先查/补具体误解、提示后完成安排独立应用、独立完成改迁移应用，不贴能力标签）。`chat/service_retry.py` 重试复制 `study_adaptation` 快照（沿用同一采用、调用前复验）。`service_projections.py` + `contracts/chat.py` 新增 `ChatConversationProjection.study_record_adoptions`（只读 applied 行）；`AuditAction.STUDY_ADAPTATION_APPLIED`（details 只含采用标识/版本/结论/调整方向与失效原因，不含正文或提示词）。`openapi.json` 以基线序位最小合并（+153 行纯追加，位置式重排到既有顺序后与新鲜 spec 逐字段相等），`generated.ts` 经 openapi-typescript 重新生成（+121 行）。
- **前端：** `StudyProgress.tsx` 新增「教学采用的学情依据」详情（记录标题/学科/用途、核验结论、调整方向中文化、原因与不确定性说明；空采用不渲染），`page.tsx` 传入 `study_record_adoptions`；单测新增 2 项（展示采用与调整方向；无采用不显示）。
- **验收标准证据（映射 43–53 行）：** ①采用边界：`test_verified_record_changes_actual_teaching_and_stays_visible`（载荷只含相关已核验记录）+ `test_relevance_selection_excludes_pending_and_cross_context`（删除/来源失效/待验证不入正式采用）；②配对运行差异：`test_hinted_and_independent_completion_produce_different_teaching`（同目标同输入、独立完成 → `transfer_application`+`recheck` vs 提示后完成 → `independent_application`，且导师载荷区块不同）；③掌握语义：提示后完成调整原因含「不要当作已稳定掌握」，单次完成不入长期掌握；④用户控制优先：`test_route_revision_...` 第 4 轮「跳过补讲，直接讲条件概率」不触发回退且导师区块含控制优先声明；⑤编译后调用前失效：`test_correction_between_compile_and_send_withdraws_old_adoption`（规划请求确曾携带旧采用，纠正后主生成载荷无学情区块、无采用行、BLOCKED 审计 `record_changed`，后续请求 `learning_adaptation == {}`）；⑥无档案继续：`test_without_formal_records_teaching_continues_without_adaptation`；⑦矛盾与同名：选择器单测（冲突只留不确定性、不同用途不串用）；⑧路线修订：`test_route_revision_...`（v1→v2、current_node 前置、`assistant_proposal`、原因含记录版本，目标版本不变；全程目标规划仅首轮一次；`expected_version=0` 冲突守卫 False 不覆盖）；⑨可见依据/审计/生命周期：旅程测试（投影、`applied` 行、审计无正文）+ `test_schema_v78_study_record_adoptions.py`（迁移列、删除顺序、导出类别、逻辑清单、备份恢复、账户隔离、账户删除）。重试沿用：旅程测试断言运行配置冻结同一 `adoption_id`。
- **验证（conda `agent`）：** 聚焦 `tests/chat/test_issue09_adaptive_teaching.py` 6 项 + `tests/storage/test_schema_v78_study_record_adoptions.py` 2 项全部通过；`tests/contracts tests/lifecycle tests/storage` 196 项通过；Issue 02–11 学习链路 13 个测试文件共 86 项通过。静态：改动文件 Ruff 干净（仓库既有 UP042/E501 与基线一致），`python -m mypy src` 141 项与基线逐条一致（无新增）。前端：`tsc --noEmit` 干净、`vitest run` 34 文件/280 项通过、`next build` 通过。全量 pytest：350 失败/5812 通过/37 跳过，失败均为未触及的既有模块（MCP、插件、orchestration 37、career、resources 等；已抽样在基线 main 复现同样失败集合：`tests/mcp/test_mcp_service.py` 31、chat 10 文件 79 项逐条一致），`tests/career_plan` 4 个文件的收集错误在 main 亦存在（引用未定义的 `CareerBackgroundLoader`）。
- **C01：** 采用选择/复验/渲染/审计为按 BridGes 需求自主设计，未复制参考项目代码或提示词；未新增第三方依赖。
- **剩余限制与后续：** 本票 UI 为只读依据展示，未新增 Playwright 旅程（交互与模型载荷由上述 pytest 覆盖）；诊断文本质量与真实收益由 13 端到端实测；采用行保留历史含义（撤回只停止后续采用并审计，不重写已提交行）。
