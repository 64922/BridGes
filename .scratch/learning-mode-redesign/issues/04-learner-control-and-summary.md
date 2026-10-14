# 04 — 直接讲解、跳过验证与自然小结

**What to build:** 用户可以要求完整讲解、跳过当前理解检查、暂停学习或自然结束一个小目标；导师照做，进展可恢复，小结如实区分已验证和未验证内容。

**Blocked by:** 03 — 嵌入式理解验证与即时纠错。

**Status:** ready-for-human

**发布状态：** 用户已确认拆分与依赖，正式发布；本票已实施并完成验证，新学习模式尚未上线。

**对应合同：** D01、D05、D09、D11、D13；AC03–AC05、AC14；C01。

## 用户演示

1. 当前有一道待答问题，用户说「直接完整讲解，我不想答题」。
2. 导师给出完整说明，解除该问题的作答等待，记录尚未验证，不继续强迫回答。
3. 用户要求下一步，学习可以继续，不因未验证而被门禁阻塞。
4. 用户说「今天先到这里」，收到简短小结；刷新后原进展和暂停位置保存。

## 实施范围

1. 将直接讲解、跳过、暂停、继续和要求小结识别为用户学习控制，保留来源与本轮作用范围；不要用几个关键词代替完整语义及待答关联。
2. 跳过只解除对应等待，不清空已有证据、不变更旧评价依据、不记答错或掌握。历史未验证状态可以查看。
3. 完整讲解允许例子、推导或参考解法，尊重用户长度与方式要求；不借结束讲解附加强制测验。
4. 暂停保存局部进展、当前问题处置和可继续位置。停止一条正在生成的回复与暂停学习分开，不把传输中断当作用户学习控制。
5. 完成问题或小目标后自然小结：本次验证内容、仍未确认之处与可选下一步。用户主动收尾不需完成整个路线。
6. 小结绑定生成时的实际记录，区分即时反馈、正式诊断与尚未核验；独立完成仅描述本次表现，不推断长期能力。
7. 小结生成失败不回滚已经保存的作答或暂停，重试只补小结，不重判、不复活已跳过问题。

## 接口、持久化与界面

- 学习控制与消息/进展版本绑定；旧控制重试不能解除后来生成的新问题。
- 小结保存依据引用和生成时进展，不因后续学习覆盖历史文字。
- 聊天中自然完成主要操作，顶部与按需展开详情显示当前等待、跳过或暂停状态。
- 错误、部分小结、停止和重试显示真实状态；按键操作与焦点不因更新跳动。
- 新控制记录、小结和暂停数据的迁移、导出、删除与恢复在本票完成。

## 范围边界与依赖理由

03 已有待答问题和学习证据，本票才能正确解除等待并总结。跨会话长期目标的继续与切换由 08 完成；本票不设置复习提醒、不新增结束考试或额外评价体系。

## 验收标准

- [x] 明确要求完整讲解时得到完整内容，不强制继续追问。
- [x] 跳过解除正确的问题等待，未验证不算错答、掌握或缺口。
- [x] 用户可以继续下一步，未验证不是强制门禁。
- [x] 暂停位置可恢复，停止生成与暂停学习不会混淆。
- [x] 小结仅使用实际证据，未核验项保持待确认，不输出掌握百分比。
- [x] 单次答对仅描述本次表现，收尾不自动增加考试。
- [x] 小结失败独立重试，不重判、不重复保存、不覆盖历史。
- [x] 旧控制消息重试、版本冲突和迟到回复不影响新待答问题。
- [x] 自然语言、详情状态、错误反馈和生命周期均贯通。
- [x] C01 审查通过，控制与小结逻辑自主实现。

## 验证与交付证据

- 覆盖直接讲解、跳过、暂停、仅停止生成、继续、请求小结及自然完成。
- 覆盖同一控制重试、对旧问题操作、生成失败后再次收尾和后台结果仍未完成。
- 验证无作答小结、提示后完成小结与独立完成小结，均不虚构学情。
- 提交一条从待答、跳过、讲解到暂停恢复的桌面旅程及必要检查证据。

## Comments

2026-10-13：用户节奏控制切片草稿，待拆分确认。

2026-10-13：用户确认「按此发布」，本票发布到本地问题跟踪器。分诊状态表示信息齐备；须待直接阻塞票验收完成后实施，本轮未开始编码。

2026-10-14：开始实施。前置票 03 已验收并合入 main（合并提交 `e9427573`，交付记录见本目录 03 票 Comments）；固定基点 main `bb5d8015`（与 `origin/main` 一致），实施分支 `codex/issue-04-learner-control-and-summary`，工作树 `.worktrees/04-learner-control-and-summary`。执行者负责共享接缝：用户学习控制与小结点（03 的待答问题/证据之上），新增终态提交仅在既有 `persist_study_progress` 回调内扩展，不新设提交路径；并行票 05/10 的工作树保持不动。

2026-10-14：本票在独立分支 `codex/issue-04-learner-control-and-summary`（基点 main `bb5d8015`）实施并完成验证。

- **实现与所有权：** 新增 `bridges.study.controls`（`StudyControlRepository`：控制/暂停/小结的读取与终态提交）与 `bridges.study.session_summary`（依据冻结、规范原文渲染、逐行交付核验）；导师规划扩展在 `bridges.study.verification`（控制识别、暂停语义、小结触发与快照扩展）。终态提交仍在既有 `persist_study_progress` 回调内按 verify → progress → controls → goal 顺序完成，不新设提交路径。
- **控制识别与冻结：** 规划器意图扩展 `full_explanation/pause/resume/summary_request`（连同 03 既有 skip 构成五类用户学习控制），按完整语义识别；本轮控制、作用问题（`bound_check`）与暂停绑定冻结进运行快照 `run.config["study_verification"]["control"]`，终态事务照快照落库，重试沿用同一快照不重复记录。
- **跳过/直接讲解与继续：** `skip` 与 `full_explanation` 为释放意图：只解除绑定问题的等待并如实写 `unverified` 证据（不计错答、掌握或缺口），不触碰旧评价依据与其他证据；两意图不允许同轮借回复附加强制测验（`_NO_POSE_INTENTS`）；未验证不是门禁，下一条消息可正常推进新检查。
- **暂停、继续与停止区分：** 暂停保存主题、可继续位置（当前关注）、暂停时未解除的问题与进展版本（`study_session_pauses`）；继续只在规划时确实存在活动暂停时恢复该 `pause_id`，旧控制迟到重试不会解除之后的新暂停；生成中断（stop）不进入终态事务，不产生任何控制/暂停/小结记录，与学习控制明确分开。
- **阶段小结：** 触发来源 `explicit_request / natural_completion / pause`；规划时由实际证据 + 本轮评价确定性生成规范原文（`render_summary_content`），指令要求逐行原样包含；自然收尾轮即使规划器给出新检查也一律抑制（`wrap_up` 优先），不自动增加考试；小结只使用实际记录，正式诊断未提供时如实标注 `not_available`，不输出掌握百分比、正确率或长期能力判断。
- **交付状态与幂等：** 终态按回复逐行核验交付完整度：完整 → `complete`，缺行/改写 → `partial` 如实记录并在界面提示可重试；重试只补写同一小结（`UNIQUE(account_id, source_message_id)` + 状态只升不降），不重判、不新建行、不覆盖历史原文。
- **接口与合同：** 公开合同新增 `StudyControlProjection`（最近控制、作用问题与是否解除等待）、`StudySessionPauseProjection`（可继续位置与保留问题）、`StudySessionSummaryProjection`（原文与真实交付状态）与三个 `ChatConversationProjection` 字段（仅学习模式非空）；`openapi.json` 以最小增量同步（保留既有键序），`packages/contracts/src/generated.ts` 由 openapi-typescript 7.13.0 重新生成。
- **持久化与生命周期：** `SCHEMA_VERSION` 升至 73，新增 `study_session_controls`（UNIQUE 账户/来源消息/类型）、`study_session_pauses`（UNIQUE 账户/来源消息，`resumed_at` 标记恢复）、`study_session_summaries`（UNIQUE 账户/来源消息，依据 JSON 与进展版本）与 `idx_study_session_summaries_conversation`；登记进 `REQUIRED_TABLES/COLUMNS/INDEXES`、`ACCOUNT_TABLES`、导出类别「学习控制与小结」与逻辑清单；旧库经启动迁移升级，无回填，会话级联删除。
- **重试与幂等：** 评价证据标识在规划时冻结（`evidence_id`），小结依据引用在终态按实际证据重建并指向同一条真实证据；重试重放冻结快照（不重新规划）；`_check_has_evidence` 与「current==posed 即 no-op」防止旧控制重试复活已推进/替换的问题或重复记账；未验证证据按 `check_id` 去重。
- **前端：** `StudyProgress` 新增暂停/跳过状态与阶段小结 details（触发来源、完整/部分交付语义、原文按需展开）；`page.tsx` 透传三个新字段。
- **验收标准证据：** ①`test_full_explanation_releases_wait_and_never_forces_quiz`（完整讲解、解除等待、不追加测验、不写掌握）；②`test_skip_releases_only_bound_check_and_records_unverified`（只解除绑定问题、未验证不算错答）；③同测试与桌面旅程验证未验证不阻塞继续；④`test_pause_saves_position_and_resume_clears_it` + `test_stop_generation_is_not_pause`（暂停位置可恢复、停止≠暂停、刷新一致）；⑤`test_summary_uses_actual_evidence_without_mastery_claims`（依据只引用真实证据、无百分比、正式诊断待确认）；⑥`test_natural_completion_summary_does_not_add_exam`（自然收尾不新增考试）；⑦`test_summary_failure_retries_only_summary_without_rejudging` 与 `test_partial_summary_records_true_status_and_retry_upgrades`（失败独立重试、partial→complete 不覆盖原文）；⑧`test_old_control_retry_does_not_touch_new_pending_check`（旧控制重试不影响新问题、不重复记账）；⑨`test_study_controls_migration_export_delete_and_recovery` + 前端新增用例（生命周期、界面与刷新一致贯通）；⑩C01：见下。
- **C01：** 控制与小结逻辑为自主实现；导师提示词沿用 02 建立的 BridGes 合同基线并按本票扩展（控制意图不出题、小结逐行包含、收尾不新增考试）；规划器 schema 与提示词为本项目原创。
- **后端验证（conda `agent`）：** 新增 `tests/chat/test_issue04_learner_controls.py` 10 项与 `tests/storage/test_schema_v73_study_controls.py` 2 项全部通过；聚焦回归 44 项通过（issue03 13、issue07 28、v71/v72 迁移 3）。全量 `tests/chat tests/storage tests/learning` 双树对账：本票 94 失败 / 1635 通过，干净 main 94 失败 / 1623 通过，失败集合逐条一致（全部为 main 既有退役/环境相关失败），本票新增 12 项全部通过、无新增失败。
- **前端验证（Node v24.14.0）：** `StudyProgress.test.tsx` 20 项、全套 `vitest run` 34 文件 267 项通过；`tsc --noEmit` 干净；`next build` 生产构建通过（仅 3 条既有 lint 警告，无本票新告警）。
- **静态检查：** 改动文件 Ruff 通过（修复本票引入的 2 项：import 排序、未用变量；`database.py` 1 项 E501 为 main 既有同文行）；改动 11 个源文件 mypy strict 干净。
- **剩余限制与后续：** 正式诊断由 05 消费证据（小结中如实标注待确认）；跨会话继续与目标切换由 08 完成；真实教学效果由 13 独立验收；规划器依赖已注册的 `qwen_structured_output`，缺失或失败时小结不生成、控制不生效但回答照常（不伪造）。

2026-10-14（交付记录）：本票提交 `e81b90de`（20 文件），以 `--no-ff` 无冲突合入 main，合并提交 `7fffab9a`（合并树 `8bea279c` 与本票分支树一致）；合并前经代理 `http://127.0.0.1:7890` fetch 确认远端 main 与本票基点同为 `bb5d8015`。合并后在 main 复跑本票行为 10 项、v73 迁移 2 项与 issue03 回归 13 项通过；05 票合并完成、main 推进到 `daf77a5f` 后，追加 v72 迁移与 openapi 同步复跑，本票相关 29 项全部通过。推送：`git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 push origin main`（`bb5d8015..7fffab9a`），`git ls-remote origin refs/heads/main` 确认远端一致；随后 05 票的合并与交付推送把 main 推进到 `daf77a5f`，本票提交仍在其历史中。交付期间另一会话并发实施并合并 05 票，双方未互相改写提交内容；本条记录按约定在 05 合并完成、main 工作树恢复干净后补写。随后删除本票 worktree（`.worktrees/04-learner-control-and-summary`）与本地分支 `codex/issue-04-learner-control-and-summary`（曾为 `e81b90de`），`git worktree prune` 后主工作树与 05/10/11 票的 worktree 保持不变；其他任务的分支与目录未触碰。

### 2026-10-14 工单 13 回交缺陷：F3 完整讲解被输出上限截断

- **现象：** 固定语料 `study-journey-corpus-v1`（`25f082…`）下，极限情境 `l4-skip`（学员：「跳过引导，直接讲讲这道题的完整过程」）在 commit `a51f62a3` 的 3 次独立真实模型运行（`123822`/`123300`/`122751`）中**全部**以 `assistant_error_code=output_budget_exceeded` 结束，首段耗时 37.1–38.6 s、输出 2203–2253 token、2 次模型调用；正文为截断的部分内容（结尾语句中途断开）。
- **机制定位：** 聊天轮默认输出上限 `DEFAULT_OUTPUT_TOKENS = 1024`（`src/bridges/chat/lightweight_policy.py:105`，经 `chat/turn.py::_payload_dict` 传入）；流式适配器在 `finish_reason == "length"` 时产生 `output_budget_exceeded` 错误块（`src/bridges/ai/qwen_adapters.py` 流式分支）。该题完整讲解约需 2200+ token，单次调用上限不足。
- **影响：** AC04「直接完整讲解得到完整说明」与 D13 的真实模型路径不成立（错误结束且无自动分段续讲）。本票更早两次运行该轮曾正常完成，说明与回复长度相关、固定语料下 3/3 复现。
- **证据：** `validation/13-release-acceptance/failure-cases.md`（F3）、`journeys-20261014T123822Z.*` 等三次运行与 `release-report.md`。复验建议：修复后复跑本票 `scripts/run_issue13_learning_journeys.py` 的极限情境即可（评估资产可复用）。
- **建议方向（供本票评估，非强制）：** 提高讲解类回合的输出预算、支持超限时跨调用续写，或在截断时如实降级为分段交付而不是整轮报错。
