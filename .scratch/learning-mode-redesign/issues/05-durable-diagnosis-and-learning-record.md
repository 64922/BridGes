# 05 — 后台独立诊断与学习档案

**What to build:** 用户实际作答后立即得到导师反馈，后台另行核验表现并形成可查看的学习档案；关闭页面或应用重启不丢任务，待核验和失败不会伪装成掌握结果。

**Blocked by:** 03 — 嵌入式理解验证与即时纠错。

**Status:** ready-for-agent

**发布状态：** 用户已确认拆分与依赖，正式发布；尚未实施。

**对应合同：** D04、D07、D10；AC02、AC05、AC08–AC10；C01。

## 用户演示

1. 完成一个有提示的概念应用，导师先给即时反馈，界面不等待完整后台诊断。
2. 学习详情显示待核验；后台成功后显示具体表现及真实回答依据。
3. 独立完成与提示后完成分别展示，同名但不同上下文的概念不混为一个判断。
4. 页面关闭、进程重启和同一消息重试后，任务恢复，档案只记一次。

## 实施范围

1. 学习领域拥有诊断任务、诊断结果、知识判断与账户级档案，复用共享队列调度，不复制个人画像提取业务或参考项目文件数据库。
2. 仅实际作答、解释或应用产生可诊断证据；控制命令、阅读、「懂了」、纯提问、未作答及失败回复不自动生成掌握/缺口判断。
3. 回复终态、有效学习证据和必要诊断任务入队在同一短事务提交。诊断模型调用在事务外，不占用前台回复等待。
4. 独立诊断核对题目、实际回答、提示程度、冻结评价依据、知识上下文和可用来源；输出具体表现、支持/反驳依据与不确定性。
5. 诊断区分本次独立完成、提示后完成、具体误解、尚未验证与无法判断；不生成百分比、人格、心理、注意力或整体能力标签。
6. 知识点身份包含学科与用途上下文；仅标题相同不能合并。单问题可形成档案，不要求先有长期目标。
7. 以账户、证据身份、输入版本与诊断协议构成幂等单位；重复事件和已完成任务不能重复计数或再次改学情。
8. 保存任务业务状态与结果版本；按证据发生顺序归并，支持来源失效与基本版本守卫。用户纠正/删除的扩展保护由 06 接续。
9. 使用既有有界重试、租约和运行预算；失败保留原判断，不判学生错，耗尽后允许针对同一任务手动重试。

## 接口、持久化与界面

- 账户级档案读取提供概念上下文、实际表现、依据定位、提示程度、核验状态与更新时间，不暴露评价私有字段或完整隐藏提示词。
- 待处理、运行中、成功、失败、失效的状态来源于真实业务记录；队列领取状态不是正式学情。
- 学习详情提供最小可用的档案与任务查看、失败说明和重试，空态说明尚无可用证据。
- 程序运行时后台继续，程序退出后重启恢复，不宣称关闭程序期间仍在调用模型。
- 原子画像的记录/使用约束保持原合同；不通过学习档案复制已禁用或撤回的画像事实。
- 新表/状态、任务与结果引用的迁移、导出、备份、账户删除、来源撤回和恢复在本票完成。

## 范围边界与依赖理由

03 提供实际作答和冻结依据，故为前置。07 的长期目标不是诊断的启动条件；本票先完成局部难点的账户级档案。06 负责用户管理，09 负责将有效档案反馈到后续教法。

## 验收标准

- [ ] 作答后回复先交付，完整诊断在后台独立执行。
- [ ] 必要任务与回复/证据原子提交，崩溃边界没有孤立成功回复或丢失任务。
- [ ] 非作答、停止和失败不产生成功诊断或无依据知识缺口。
- [ ] 提示程度、实际来源、概念上下文和不确定性均可追溯。
- [ ] 单次完成不变成长期掌握，同名不同概念不错误合并。
- [ ] 重复调度、租约恢复、重复完成不重复记账。
- [ ] 乱序诊断、来源失效和版本冲突不覆盖更新证据。
- [ ] 失败保留原档案，有界重试与手动重试各有真实状态。
- [ ] 页面关闭/应用重启恢复有效任务，账户隔离与私有字段成立。
- [ ] 新数据生命周期、合同生成和 C01 审查完成。

## 验证与交付证据

- 在回复事务提交前后、入队前后和任务完成前后注入故障，核对原子性与可恢复性。
- 重复投递同一证据、并发领取、租约过期、已完成再投递，证明只有一次正式更新。
- 将两个作答反序完成，验证当前判断按实际证据语义归并，冲突保持待确认。
- 用真实 API/执行器/界面读取任务与档案，不以进程内对象断言代替持久链路。
- 交付迁移、导出/删除/恢复、调用预算和完整失败重试旅程。真实诊断质量由 13 验收。

## Comments

2026-10-13：后台诊断与档案切片草稿，待拆分确认。

2026-10-13：用户确认「按此发布」，本票发布到本地问题跟踪器。分诊状态表示信息齐备；须待直接阻塞票验收完成后实施，本轮未开始编码。

2026-10-14：开始实施。前置票 03 已交付并合入 main（`9717baeb` → 合并 `e9427573`，验收状态 ready-for-human 且合并记录完整），前置 03 的证据语义、冻结依据与终态原子提交点均已在 main 落地，本票直接消费。固定 Git 基线为 main `bb5d8015`（含 07 交付）；工作树 `.worktrees/05-durable-diagnosis-and-learning-record`，分支 `codex/issue-05-durable-diagnosis-and-learning-record`，开发与验证使用 conda `agent`。本票负责的共享接缝：学习证据终态提交的扩展（`chat/service_stream.py` 回调内同事务登记诊断任务）、会话投影新增诊断任务/学习档案字段、后台执行器 tick 接入诊断服务、迁移 v73 与生命周期清单。04/06/09/13 为后续消费者；本票不修改 04 的用户控制与 07 的目标路线语义。

2026-10-14（实施与验证记录）：
- **实现与所有权：** 新增 `bridges.study.diagnosis.StudyDiagnosisService`（登记、领取、执行、手动重试；专用队列 `study-diagnosis`，租约 120s、有界 3 次、单轮最多 3 件、失败保留原档案）、`bridges.study.diagnosis_repository`（任务业务状态、结果版本与账户级档案；幂等 `dx-<sha256(账户|证据|输入版本|协议)[:32]>`；知识身份=学科+标题+用途上下文，NFKC casefold 归一；按证据发生顺序归并、迟到结果不覆盖更新证据、方向冲突 `pending_confirmation`；`record_version` 乐观锁）、`bridges.study.diagnosis_prompt`（自主设计的诊断提示词与 JSON Schema）、`bridges.contracts.study_diagnosis`（`DIAGNOSIS_CONTRACT_VERSION="study-diagnosis-v1"`、5 种状态、5 种 verdict、任务/证据/档案投影）。输出合同确定性拒绝百分比、人格、心理、注意力与整体能力标签；`open_probe` 的完成类结论强制降级为「无法判断」；诊断能力未配置时如实失败并可手动重试。
- **原子提交与后台独立：** 03 的 `commit_turn_in_transaction` 扩展为返回诊断候选（同一事务内证据写入成功才返回），`chat/service_stream.py` 终态回调在同一事务调用 `schedule_in_transaction`（任务行 + 队列行与消息 streaming→done 原子一致）；模型调用在回复事务之外，由 API 进程生成执行器的顺风车 tick（`run_executor._background_tick_summary`）领取执行，不占用前台等待。修正发现的一处缺陷：诊断候选的冻结依据取自已绑定待答问题（`study_pending_checks`），而不是当前回合快照的 `posed_check`（那属于下一题）。
- **接口与合同：** `ChatConversationProjection` 新增 `study_diagnosis_tasks`（真实业务状态；队列领取状态不出现）与 `study_learning_record`（概念上下文、表现、依据定位、提示程度、核验状态、更新时间；不暴露私有评价字段与隐藏提示词），仅学习模式读取；新增 `POST /chat/conversations/{conversation_id}/study/diagnosis/{task_id}/retry`（仅 failed→pending，404/409/503 语义，账户作用域）。`openapi.json` 与 `packages/contracts/src/generated.ts` 以纯追加方式同步（openapi.json 与合并后应用输出逐字段一致；generated.ts 经 openapi-typescript 7.13.0 重新生成，仅新增本票类型块），`tests/contracts/test_openapi_sync.py` 通过。
- **持久化与生命周期：** `SCHEMA_VERSION=73`：v73 新增 `study_diagnosis_tasks`（业务状态/尝试/执行代次/私有依据/完成时间）、`study_diagnosis_results`（按任务版本追加，唯一 `(task_id, result_version)`）、`study_learning_records`（账户级知识判断，唯一 `(account_id, knowledge_key)`）与 4 个索引；登记 `REQUIRED_TABLES`/`REQUIRED_TABLE_COLUMNS`/`REQUIRED_INDEXES`；`ACCOUNT_TABLES` 按结果→任务→档案顺序接入，导出类别 `study_diagnosis`、逻辑清单、快照备份与恢复、账户删除均已接入并用测试覆盖。旧库经启动迁移升级，无回填；既有 v72 测试断言按先例放宽为 `>=72`。
- **前端：** `StudyProgress` 新增「学习档案」面板（新增模式的目标视图与局部进展视图共存）：核验状态逐条展示待核验/核验中/失败/来源已失效与真实失败说明，失败任务提供「重试核验」；已核验知识点展示判断、表现、支持/抵触依据、不确定性、证据条数与按发生顺序的作答/提示程度定位；证据冲突如实标注「待进一步验证」，不渲染百分比、人格或能力标签；空态不显示面板。`page.tsx` 在有非终态任务时按 3 秒轮询（有界 2 分钟，与附件解析同一节奏）并在重试后立即刷新；`lib/api/chat.ts` 新增 `retryStudyDiagnosis`。
- **验收标准证据（映射 47–56 行）：** ①回复先交付：诊断能力故障时消息 `done`、任务 `pending`、档案为空，能力恢复后自动重试成功（`test_reply_delivers_before_background_diagnosis_and_private_basis_stays_server_side`）；②原子边界：终态提交与任务登记同事务，失败/停止/非作答三轮驱动后证据、任务、档案三表零行（`test_non_answer_and_failed_turn...`、`test_stopped_turn...`）；③提示程度与来源：`assistance=after_hint`、`hint_count=1`、真实提示原文进入诊断载荷，证据含回答消息与反馈消息定位（`test_hint_level_source_and_context...`）；④同名不同概念：标题相同、用途上下文不同形成两条档案与两个 `knowledge_key`（同测试）；⑤重复调度/租约恢复/重复完成：同证据重复登记、崩溃留下的过期租约、已成功任务再投递均只记一次（`test_lease_recovery_and_manual_retry...`、首个测试尾部）；⑥乱序与冲突：旧证据任务耗尽重试后手动重试迟到完成，不覆盖新证据判断且冲突标记待确认（`test_late_older_result...`）；⑦来源失效与版本冲突：证据删除/检查版本变化后任务标记 invalidated，不写结果与档案（`test_invalidated_source_and_version_mismatch...`）；⑧失败保留与手动重试：3 次有界重试耗尽后 `failed/retryable`，档案保持原判断，手动重试成功后同一任务仅一条结果；永久失败（能力未配置）同样如实失败并可恢复重试（两个 retry 测试）；⑨重启恢复与账户隔离：应用关闭后同一数据库新进程按持久化任务恢复执行，跨账户会话与重试 404（`test_restart_recovers_pending_task...`）；⑩生命周期与合同：`test_schema_v73_study_diagnosis.py`（迁移/导出/预览/逻辑清单/跨账户/快照恢复/删除、v72→v73 升级幂等）与合同同步测试。
- **C01：** 诊断提示词、JSON Schema 与输出合同为按 BridGes 需求自主设计（`diagnosis_prompt.STUDY_DIAGNOSIS_SYSTEM_PROMPT`：只输出 JSON、只针对当时证据、不生成百分比/人格/心理/注意力/能力标签、无法判断如实说明），未复制参考项目代码或提示词；未新增第三方依赖。
- **验证（conda `agent`，Python 3.11.15）：** 本票聚焦 11 项通过（`tests/chat/test_issue05_durable_diagnosis.py` 9 项、`tests/storage/test_schema_v73_study_diagnosis.py` 2 项）；回归 140 项通过（`test_issue02_text_tutor`、`test_issue03_embedded_verification`、`test_issue07_goals_and_learning_route`、`tests/contracts`、`tests/storage` 全部）；生命周期 81 项通过（`tests/lifecycle` 全部）。静态检查：改动文件 Ruff 仅剩 `api/chat.py` 的 F401/F841 与 `database.py:1671` E501 三项既有问题（均非本票改动，blame 已核对），改动模块 mypy 无新增报错。
- **前端验证（Node v24.14.0）：** `tsc --noEmit` 干净；`vitest run` 34 文件/267 项通过（含本票 4 项）；`next build` 生产构建通过；`next lint` 仅 3 条既有警告；`npx playwright test e2e/issue05-durable-diagnosis.spec.ts --project=chromium` 1 项通过（待核验展示、失败重试请求、轮询推进到成功档案、展开依据、刷新保持；真实链路由 pytest 覆盖）。
- **剩余限制与后续：** 诊断输出质量（提示词效果、判词合理性）由 13 验收；用户对档案条目的查看/纠正/删除由 06 接续；档案反馈到后续教法由 09 接续。诊断在 API 进程生成执行器 tick 中顺风车运行（无独立常驻线程时依赖生成 tick 或重启后的首个 tick——与画像提取同一拓扑，E2E 以 `BRIDGES_GENERATION_EXECUTOR=1` 常驻线程覆盖）。

2026-10-14（合并与推送记录）：
- 合并前 `origin/main` 仍为 `bb5d8015`；本地 main 已被 04 合入（`e81b90de` 功能提交 + `7fffab9a` 合并提交），04 与 05 都占用了迁移 v73，按 03→07 先例在合并时把本票迁移**重编号为 v74**（`SCHEMA_VERSION=74`，测试改名 `tests/storage/test_schema_v74_study_diagnosis.py` 并断言 `>= 74`，v72→v74 升级路径覆盖 04+05 两步迁移）。
- 冲突解算（union，不丢任一侧）：`database.py`（v73 保留 04、v74 为本票；`REQUIRED_TABLES`/`REQUIRED_INDEXES` 合并）、`catalog.py`（`study_controls` 与 `study_diagnosis` 两个导出类别并存）、`contracts/chat.py` 与会话投影（04 控制/暂停/小结字段与 05 诊断任务/档案字段并存）、`verification.py`（04 的 `_RELEASING_INTENT_VALUES` 释放语义与本票的诊断候选初始化并存）、`StudyProgress.tsx`（04 的 `StudyControlStatus` 与本票的 `StudyDiagnosisPanel` 在目标/局部进展两个视图同时渲染）、`StudyProgress.test.tsx`（两个 describe 块共存，24 项通过）。
- 合同产物：以 04 之后的 main 版 `openapi.json` 为基线，对合并后应用全量重生成再位置式最小合并——结果与新鲜 spec 逐字段相等且相对基线纯追加（416 行），`tests/contracts/test_openapi_sync.py` 通过；`packages/contracts/src/generated.ts` 由合并后 openapi.json 重新生成（openapi-typescript 7.13.0），相对基线纯追加 317 行。
- 合并后 main 验证：`tests/contracts/test_openapi_sync.py tests/storage tests/chat/test_issue0[2345]（含 04 learner_controls 与本票 durable_diagnosis）test_issue07_goals_and_learning_route tests/lifecycle` 共 **235 项通过**；Ruff 仅剩三项既有问题（`api/chat.py` F401/F841、`database.py:1671` E501）；前端 `tsc --noEmit` 干净、`vitest run` 34 文件 271 项通过、`next build` 通过。
- Git 记录：本票功能提交 `d3659a6f`；合并提交 `6644f35a`（`merge: issue 05 durable diagnosis and learning record`）；合并后发现测试断言未随重编号更新，追加修正提交 `494c38cd`（`fix(storage): assert schema version at least 74 in issue 05 test`）。推送时本地 main 同时携带 04 的 `e81b90de`/`7fffab9a`（04 已完成验证但未推送，随本次一并推送）。
