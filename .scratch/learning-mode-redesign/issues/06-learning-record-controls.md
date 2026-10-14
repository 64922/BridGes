# 06 — 档案纠正、删除与迟到结果保护

**What to build:** 用户可在学习详情中查看判断依据、纠正误判或删除记录；操作实际改变后续可采用的学情，历史扫描、缓存及迟到诊断不能把旧判断恢复回来。

**Blocked by:** 05 — 后台独立诊断与学习档案。

**Status:** ready-for-human

**发布状态：** 用户已确认拆分与依赖，正式发布；尚未实施。

**对应合同：** D04、D07、D09、D10；AC10、AC11；C01。

## 用户演示

1. 展开「混淆独立与互斥」的记录，看到来自哪道题、哪条回答及是否有提示。
2. 用户指出判断不准确，保存后展示纠正及其主动来源，下一轮不沿用被撤回判断。
3. 删除记录，页面刷新后仍已删除，迟到的同证据诊断不会恢复。
4. 后来确有新的作答时可记录新的证据，但不能用旧历史冒充新作答。

## 实施范围

1. 实现账户级依据查看、纠正和删除的完整接口与交互，操作须校验所有权、记录身份和预期版本。
2. 依据定位到实际消息/题目/材料，来源已删除或不可读时说明不可用，不伪造引文或继续供诊断使用。
3. 纠正保留用户主动来源、被取代结论和记录版本。用户自评与已核验表现区分，不把「我会了」自动包装成独立验证。
4. 删除停止该判断及关联旧证据在学情归档/个性化中的采用，保留必要撤回标记；原聊天仍可按历史读取义务查看，不自动删整段对话。
5. 更新诊断提交守卫：提交前校验纠正、删除、来源版本和档案版本，迟到结果不得原地覆盖用户操作。
6. 缓存、上下文切片、历史摘要和后台回放都遵守撤回范围，不能经另一入口再次推断并复活同一旧判断。
7. 真正的新作答可形成新观察；与用户纠正有冲突时保留双方依据，不能假装自动结果比主动纠正更权威。
8. 为后续消费者提供唯一的有效档案读取边界，返回当前可用版本与失效原因；09 使用该边界，不能直接读已删除业务行。

## 接口、持久化与界面

- 查看依据、纠正和删除均有明确操作状态；纠正提供保存/取消，删除按既有用户管理规范确认。
- 版本冲突如实提示并保留当前输入，不把覆盖失败显示为成功。
- 失败保留原记录，取消不改档案；只读用户视图不泄露内部私有评价依据。
- 删除与撤回标记进入生命周期；账户删除清理任务和派生引用，导出说明现存记录及有效来源状态。
- 档案和原子画像分别管理，不顺手改变画像开关或恢复已退役个人画像分组。

## 范围边界与依赖理由

05 已有真实档案、诊断任务和版本，本票才能证明用户操作影响真实后台结果。09 的个性化消费须等本票完成，不能先接入忽略用户撤回的读取路径。

## 验收标准

- [ ] 用户能查看真实依据，来源不可用时准确显示。
- [ ] 纠正有主动来源和版本，自动诊断不能无条件覆盖。
- [ ] 删除对真实持久数据及可采用切片生效，刷新后不恢复。
- [ ] 同证据迟到任务、缓存和历史回放不能复活已撤回判断。
- [ ] 新作答与旧历史有清晰身份，不靠旧消息重新命名绕过撤回。
- [ ] 用户自评不被声明为已核验掌握，冲突与不确定性保留。
- [ ] 并发纠正/删除、重复提交和版本冲突反馈正确，失败不丢输入。
- [ ] 他人无法查看、纠正或删除记录及其来源。
- [ ] 上下文有效性读取、导出、账户删除及 C01 审查贯通。

## 验证与交付证据

- 在诊断正在运行时纠正/删除，再放回迟到结果，验证提交被拒及用户状态保持。
- 同时打开两个详情页修改，验证旧版本保存不会覆盖新版本。
- 在原聊天、跨会话、摘要/缓存和后台回放四种读取中检查撤回，同一旧证据不能再次归档。
- 覆盖来源删除、账户切换、用户自评、取消编辑、重复删除和真正新作答。
- 交付 API/界面真实操作与后台恢复的同一旅程，不能只断言删除按钮消失。

## Comments

2026-10-13：档案管理与用户权威切片草稿，待拆分确认。

2026-10-13：用户确认「按此发布」，本票发布到本地问题跟踪器。分诊状态表示信息齐备；须待直接阻塞票验收完成后实施，本轮未开始编码。

2026-10-14：开始实施。前置票 05 已交付并合入 main（`e81b90de`/`7fffab9a` → 合并 `6644f35a`，验收状态 ready-for-human），05 的诊断任务、账户级档案与 `record_version` 乐观锁均已在 main 落地，本票直接消费。固定 Git 基线为 main `c04c2c2b`（含 11 交付）；工作树 `.worktrees/06-learning-record-controls`，分支 `codex/issue-06-learning-record-controls`，开发与验证使用 conda `agent`（Python 3.11.15）。本票负责的共享接缝：账户级档案的用户控制状态（`state`/`user_note`/`corrected_at`/`deleted_at`/撤回证据集）、纠正与删除接口、诊断提交守卫、迁移 v76、生命周期接入，以及 09 唯一可用的有效档案读取边界；09 是本票的下游消费者，本票不修改 10 的材料作答复核语义、不接触画像开关。

2026-10-14（实施与验证记录）：
- **合同与状态机：** `contracts/study_diagnosis.py` 新增 `StudyRecordState`（active/corrected/deleted）、`StudyRecordCorrectionRequest`（`note` 1–500 + `expected_version`，`extra=forbid`）、`StudyRecordDeletionRequest`（`expected_version`）与 `StudyRecordBoundaryEntry`（record_id/record_version/effective/reason/verified）；证据投影新增 `source_available`/`source_note` 且 `occurred_at` 变为可空（来源不可读时不伪造时间与引文），档案投影新增 `record_version`/`state`/`user_note`/`corrected_at`/`superseded_verdict`/`superseded_performance`/`source_available`。
- **持久化 v76：** `study_learning_records` 补 5 列（`state`（默认 active）、`user_note`、`corrected_at`、`deleted_at`、`retracted_evidence_ids_json`），新增 `study_record_controls` 操作留痕表（纠正/删除各留一条，含操作版本与时间）与账户索引；`MIGRATION_ADDED_COLUMNS`、`REQUIRED_TABLES`/`REQUIRED_TABLE_COLUMNS`/`REQUIRED_INDEXES` 全部登记；旧库经启动迁移升级，既有 v75 断言按先例放宽为 `>= 75`。
- **服务与守卫：** 新增 `bridges.study.record_controls.StudyRecordControlService`：纠正按 `expected_version` 乐观锁写入用户自评、保留被取代结论与操作版本，删除写撤回标记（`deleted_at`+`retracted_evidence_ids_json`）并停止该记录与旧证据的采用；`effective_records`/`record_boundary` 是 09 唯一有效读取边界（`user_corrected`/`user_deleted`/`source_unavailable` 与 `verified` 语义）。`diagnosis_repository` 提交守卫：迟到结果命中撤回证据/发生时间早于删除 → `_RecordRetractedError` 整体拒绝并把任务如实标记 `invalidated`；纠正前证据迟到完成只登记观察不覆盖用户操作；纠正后新观察合并但保留 `pending_confirmation`，自动结论不冒充比用户纠正更权威；删除后真正的新作答（晚于删除）形成新观察并清空旧控制。`chat/service_projections.py` 改用控制服务投影，删除记录不再进入会话视图。
- **API 与生命周期：** `POST /chat/conversations/{id}/study/records/{rid}/corrections`（返回更新后的档案投影）、`DELETE /chat/conversations/{id}/study/records/{rid}`（204）；404 `record_not_found`、409 `record_version_conflict`/`record_deleted`、422 `record_note_required`，账户作用域与私有字段不泄漏。`ACCOUNT_TABLES` 接入 `study_record_controls`（先于档案表）、新导出类别 `study_record_controls`（「学习档案管理记录」）与逻辑清单/快照恢复/账户删除贯通。`openapi.json` 以合并前版本为基线全量重生成后位置式最小合并（相对基线纯追加 360 行/仅 7 行既有字段变更 = `occurred_at` 可空），与新鲜 spec 逐字段相等；`packages/contracts/src/generated.ts` 经 openapi-typescript 7.13.0 重新生成（+280/-3）。
- **前端：** `lib/api/chat.ts` 新增 `correctStudyRecord`/`deleteStudyRecord`；`StudyProgress` 学习档案面板新增「纠正」（行内 textarea + 保存/取消，失败保留草稿、成功后按新投影刷新）、「删除」（`window.confirm` 明示「不再用于后续教学」）、来源不可读的证据与记录如实标注、纠正态展示自评与被取代原结论；`page.tsx` 新增 `correctRecord`/`deleteRecord` 处理器（版本冲突如实提示并刷新，不把失败显示为成功）。
- **验收标准证据（映射 45–53 行）：** ①查看依据/来源不可用：`test_view_evidence_locates_real_sources_and_marks_unavailable`（来源删除后 `source_available=false`、说明真实、不伪造引文）；②纠正与版本：`test_user_correction_keeps_source_and_late_diagnosis_cannot_overwrite`（主动来源、被取代结论、`record_version` 递增，旧版本提交 409 且原记录保留）；③删除生效与刷新不恢复：`test_deletion_persists_and_late_results_cannot_revive`（删除后投影消失、同证据迟到结果被拒写、任务 `invalidated`）；④迟到/缓存/回放守同一撤回范围：守卫在提交点拒绝，迟到任务不写结果不复活判断；⑤新作答身份：`test_new_answer_after_deletion_forms_new_observation`（晚于删除的新作答形成新观察，旧历史不冒充）；⑥自评不冒充已核验、冲突保留：纠正态展示「用户纠正（自评，未经核验）」且纠正后新观察保留待确认（`test_corrected_record_new_observation_coexists_with_user_note`）；⑦重复/冲突/隔离：`test_version_conflict_duplicate_and_account_isolation`（重复删除 409、跨账户 404、版本冲突不覆盖）；⑧界面旅程：`e2e/issue06-learning-record-controls.spec.ts` 两条桌面旅程（纠正保存与刷新保持；删除确认、迟到成功不恢复）。取消编辑、失败保留输入由 `StudyProgress.test.tsx` 新增 6 项覆盖（纠正提交/失败保留草稿/纠正态展示/删除确认/取消不提交/来源不可读标注）。
- **验证（conda `agent`）：** 聚焦 10 项通过（`test_issue06_learning_record_controls.py` 6、`test_schema_v76_study_record_controls.py` 2、`test_openapi_sync.py` 2）。四目录回归对照（`tests/chat tests/storage tests/lifecycle tests/contracts`）：基线 `c04c2c2b` 95 失败/1623 通过，本分支 95 失败/1631 通过（Δ通过 +8 = 本票新增；一次运行中 `test_improvement13_acceptance.py::test_121_...` 单条负载抖动，单独 5/5 与整文件 8/8 复跑通过、复跑后失败名单与基线逐条 0 差）。全量 pytest（排除 `tests/career_plan`——该目录在 main 与基线均既有收集错误：测试引用 `CareerBackgroundLoader` 而 src 两侧均无定义）333 失败/5694 通过/37 跳过，失败均在未触及的既有模块（MCP 退役 410、retirement、security 的 issue42 脚本凭据扫描、retrieval 等），与四目录对照结论一致。静态：改动文件 Ruff 仅剩 3 项既有问题（`api/chat.py` F401/F841、`database.py:1671` E501），改动模块 mypy 无报错（修复一处 `no-redef`）。前端：`tsc --noEmit` 干净、`vitest run` 34 文件/277 项通过（含 `StudyProgress.test.tsx` 30 项）、`next build` 通过、`next lint` 仅 3 条既有警告；`playwright test e2e/issue06-learning-record-controls.spec.ts` 2 项通过（本机以 `BRIDGES_PYTHON=agent`、`PYTHONPATH=<worktree>/src`、`PORT=3100`/`API_PORT=8100` 运行避开共享端口；真实链路守卫由 pytest 覆盖）。
- **C01：** 纠正/删除的输出语义与守卫为按 BridGes 需求实现，未复制参考项目代码；未新增第三方依赖（前端 openapi-typescript 为既有依赖）。
- **剩余限制与后续：** 诊断文本质量由 13 验收；09 必须以 `record_boundary`/`effective_records` 读取，不得直读已删除业务行；撤回范围对历史摘要/缓存的旁路复用在本票以「唯一读取边界 + 提交守卫」覆盖，13 端到端验收时复核。

2026-10-14（合并与推送记录）：
- 合并前 main 已推进到 `9af3c325`（含 10 材料作答复核合入 `c034f69e` 与 11 的交付记录）；本票基线 `c04c2c2b`。三方合并**零冲突**：10 在 `StudyProgress` 两个视图的 `source_status` 待答提示与本票的档案面板/签名改动自动并置（合并后两处提示与 `onCorrectRecord`/`onDeleteRecord` 同时在场），`openapi.json`/`generated.ts` 文本并置后 `test_openapi_sync` 逐字段通过，合同产物无需重生成。
- Git 记录：功能提交 `0daf8add`（`feat(study): add learning record correction, deletion and late-result protection`，20 文件 +3323/−246）；合并提交 `44c472f0`（`merge: issue 06 learning record controls`）。
- 合并后验证（主工作区，conda `agent`）：`tests/contracts tests/storage tests/lifecycle` + 本票/05/10/02/03/07 聊天用例共 **239 项通过**（以独占 `--basetemp` 运行——主工作区共享 `.tmp/pytest-basetemp` 有其他会话进程持有数据库文件，Windows 文件锁会令无关用例在清理阶段报错；换独占目录后全绿）；前端 `tsc --noEmit` 干净、`vitest run` 34 文件/**278 项通过**（含 10 的 1 项与本票 6 项）、`next build` 通过。
- 推送：本地 main 经代理 `http://127.0.0.1:7890` 推送 `origin/main`，推送后 `git rev-parse` 两侧一致。
