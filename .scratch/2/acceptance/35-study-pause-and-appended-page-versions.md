# 工单 35 独立验收与修复

日期：2026-10-05。原交付 `3e652e4f678d398f1803d9391aaab8ff8622b6f0`，固定 main 基线 `3665008b2a008ca36d02e0cfeb3896525aa8f640`。交接报告只用于定位；证据来自实际代码、双轴独立审查、本次执行的测试与 main 对照。

结论：修复后通过本票确定性验收。修复源码提交 `7b464f98`；后续提交仅完善独立测试初始化与验收证据。全量对账无剩余新增产品回归，真实模型与浏览器验证限制见末节。

## Standards（规范符合性）

code-review 规范轴独立审查 AGENTS、CONTEXT、批次执行说明、领域文档、工作流、ADR 及全部 12 项 smell baseline。静态初审报告 0 项硬违反、0 项值得报告的异味；修复复审通过。规范轴结论与需求轴分别记录。

修复后的候选写和有效版本切换均在事务内检查执行权与有效状态，符合公共编排 §8；只有最终消息事务回调允许本事务已写入的消息终态，候选写不豁免终态。持久停止与租约转移仍拒绝迟到结果。图版本升级及旧图安全结束符合恢复兼容要求。新增字段带默认值，沿用学习状态 JSON 生命周期，没有新增表或退役产品入口。

## Spec（需求符合性）

独立需求轴发现 2 项阻断，主审反例另确认 2 项问题，均已修复：

1. **候选写覆盖并发有效状态。** `save_state_in_transaction()` 原用运行启动的 committed 快照整份保存候选；OCR/映射期间的新辅导记录会被抹掉，最终版本守卫反而看到旧快照一致。现在每次候选写先在同一事务核对有效状态，版本冲突不再进入失败候选补存。识别、映射两个边界均有红→绿证据。
2. **旧图检查点不兼容。** 原图仍为 v5，旧 finish_pages 缓存输出没有 summary_history/unanswered，可绕过新增语义并丢失历史。图改为 `study-tutoring-review-v6`，旧 v5 通过学习入口安全结束；明确重试才新建当前图运行。原材料、历史和附件不被旧运行修改。
3. **终态豁免跳过持久停止。** 共享守卫在消息终态判断后检查 stop_requested；最终回调豁免 message_terminal 时会遗漏跨进程持久停止。现在显式补查运行 stop_requested，回滚迟到追加。只改本票终态豁免接缝，不重排共享守卫。独立反例只修改持久标志、不设置进程内 Event；租约转移控制项也通过。
4. **已答未判定误标为未作答。** 总结引用标签原把所有无判定题写为“未作答”。现在已收到答案显示“已作答（尚未判定，未计入掌握）”，真正未答仍单独标记。标签反例修复前失败，修复后通过。

最终需求轴只读复验通过。未发现剩余阻止本票合入的机制缺陷；真实模型质量不由替身测试证明。

## 逐项验收证据

前置 30/31/33/34 的实际实现、验收记录和主线提交已核对，相关识别/范围/出题/判定测试在本次扩展组合重新运行。

| 工单要求 | 独立执行的证据 |
| --- | --- |
| 暂停不批改、继续未问题、未答不计掌握 | `test_pause_is_not_graded_and_resume_defaults_to_next_unasked`、`test_unanswered_question_never_counts_as_mastered`：题 ID/来源/原判定保留，plan 调用不重复，辅导不增加 grade 调用，未答超报总结被拒绝 |
| 追加识别/范围失败保留原有效状态，成功仅未问题重排 | `test_append_failure_keeps_effective_scope_and_reuses_recognized_pages[ocr/map]`、`test_append_after_summary_versions_history_and_preview_questions`：失败 pages/units/scope/summary 不变，成功后已问题与判定不变，未问题按新 scope 重规划 |
| 复用未变化页、关键冲突阻断、双方来源保留 | `test_scope_conflict_blocks_switch_until_resolved`、`test_new_page_critical_unclear_blocks_switch_and_supplement_resolves`、`test_replacement_keeps_superseded_source_fragments`：vision_count 证明确实复用，关键疑点先补录，superseded_fragments 保留原文 |
| 总结/预习版本准确，依据变化不继续当掌握 | 总结历史/预习问题原 scope 关系测试、`test_summary_basis_changed_history_is_marked_not_mastered`；新总结拒绝已失效依据，渲染明确待重新确认；已答未判和未答标签独立回归 |
| 幂等、停止、重启、版本守卫与隔离 | 原交付停止/重启/最终并发测试；新增 `test_candidate_write_preserves_concurrent_effective_state[recognition/mapping]`、`test_append_final_transaction_rejects_late_authority_change[stop/lease]`、`test_previous_graph_cannot_replay_pre_versioned_update` |
| 新状态完整生命周期 | `tests/lifecycle/test_issue35_version_history_lifecycle.py`：v3 原总结读取、v4 历史/未答/取代片段实际导出、加密备份恢复、账户删除及另一账户保留、跨账户读取拒绝、导出审计 |

覆盖 L08、L09、L13，以及 R03 迟到/停止/租约与旧图恢复。原运行预算继续使用 load_run_budget/RunBudgetLedgerRepository，未增加重置或替代预算器；前置预算/识别恢复测试仍在扩展回归中。

## 合同与迁移

- 学习状态 JSON 版本 4：`StudyPage.superseded_fragments`、`StudyReviewQuestion.unanswered`、`StudySummaryRecord`、`StudyState.summary_history`。旧 v3 按默认值读取，保留原总结，不推测改写历史判定。
- 学习图 v6；已有 v1–v5 运行仍进入学习版本门安全结束，显式重试用新图。识别、范围、评分协议/配方不变，适用的逐页产物继续复用。
- 新字段随 study_states 全体 JSON 纳入导出、备份恢复、删除和账户隔离；本次实际生命周期测试通过，无新数据库表或对象库域。
- `study_page_update_changed` 登记 STATE_CONFLICT/REFRESH_STATE。书页失败保留当前有效范围和可重试候选，不把系统失败当作学习表现。
- 本次在 conda agent 运行 `scripts/regenerate_openapi.py`，311 paths，再生 OpenAPI 无差异；用本地 openapi-typescript 7.13.0 再生 generated.ts，统一行尾后逐字一致。

## 实际验证

环境：Windows PowerShell，conda `agent`，Python 3.11，`PYTHONUTF8=1`，pytest 各次使用独立 basetemp。主线实际源码导入路径为 D:/BridGes/src，工作树 pytest 遵循项目 pythonpath=src。所有数量都是本次实际运行；初次命令文件名/测试夹具修正不作为产品失败。

证据目录：`../validation/35-acceptance/`。原交付报告中的旧通过数量未充当本次证据。

- 原交付新增竞争/旧版本反例：`red.xml` 为 **4 failed / 1 passed**；已答未判标签：`answered-red.xml` 为 **1 failed**。两个竞争、持久停止、旧图安全结束和标签的红证据保留。
- 新增独立回归最终 `green.xml`：**7 passed**，包括完整生命周期。早期生命周期夹具缺字段/默认值/嵌套导出解析已修正，最终按真实导出 state_json 与保存状态逐字结构比较。
- 最终扩展组合 `final-focused.xml`：**235 passed / 5 failed**。同 5 项 `tests/lifecycle/test_lifecycle_api.py` 在 main 独立复现（`main-lifecycle.xml`，**9 passed / 5 failed**）：会话创建返回 409，旧测试预期 201，属于既有失败。本票生命周期机制独立通过。
- 最后将消息终态豁免限定最终回调后的 `final-boundary.xml`：**30 passed**，含本票原交付测试、新增竞争/生命周期、总结回归。
- Ruff 本票源码/测试通过；chat/service.py 单独运行也通过（chat-service-ruff.json 为 []）。`git diff --check` 通过。
- 本次 `python -m mypy src/bridges`：分支与 main **均 108 errors in 22 files**，逐条错误集合一致，无新增。原交接不同范围命令的 22 项不作为此命令基线。
- 本次独立全量 main：**5301 passed / 225 failed / 37 skipped / 2 errors**（1686.37 秒）；分支：**5320 passed / 222 failed / 39 skipped / 2 errors**（1502.36 秒）。记录 main-full.* / full.*，按 nodeid 差分见 full-compare.json，比较脚本随记录保存。日志发现 1 处测试凭据匹配，full.txt/full.xml 已脱敏，测试标识、失败类型和计数不变；sanitizer 随证据保存，未改他人日志。
- 分支全量是在生命周期夹具最终修正及标签补测之前启动，不能声称它是最后提交的完整全绿运行。新增 18 项采集、无删除测试，其中 17 项通过；唯一仅分支失败是新增生命周期测试对旧总结默认 `question_ids=[]` 的夹具断言遗漏。已修正默认值和嵌套导出解析，green/final-focused/final-boundary 均通过；生命周期单跑又暴露现有聊天/学习循环导入，测试显式初始化既有聊天组合根后消除收集顺序依赖。最终使用独立 PYTHONPYCACHEPREFIX、生命周期优先收集的 `final-recheck.xml`：**22 passed**（原票 12 + 独立新回归 7 + 合同 3），验证最终文件与字节码缓存无关。这些新增测试失败不是遗留基线失败，已逐项修正并复验。
- 仅 main 失败的 4 项：预算重试 active/closed 时序（main 单跑 main-budget.xml **1 passed**）、两项启动/端口测试（工作树无 Web 构建按 NEEDS_WEB_BUILD 跳过）、秘密扫描（main 已有未跟踪 34-review-main-full.txt 告警；分支全量无此文件）。其他失败/错误同 main；没有把绝对失败数量变少当作验收证据。

## 交付状态

- 验收分支最终提交 `185d7f125ae3de97c38a196205aa1a58f2f5ed3c`，生产修复提交 `7b464f98ad502bc3ecbeb11824873c3074a5654a`。本票状态已改为 ready-for-human，Comments 追加独立验收说明。
- 合并前 main 的 4 个同路径未跟踪日志阻挡 Git 写入，首次合并安全中止。已原样保留到 `.scratch/2/validation/35-premerge-preserved/`，逐文件 SHA-256 一致后再合并，没有覆盖原有内容。没有源代码冲突。
- 分支以 no-ff 合入 `main@3665008b`，合并提交 **`b9fb1a995b101d680cfc36493202138a326fc9b0`**；合并树与验收分支的 Git 内容完全一致。
- 合并后 conda agent、隔离 PYTHONPYCACHEPREFIX 和 basetemp，复跑本票、独立新增回归、生命周期、总结与合同：**33 passed**（`merged.xml` / `merged.txt`）。
- 经 `http://127.0.0.1:7890` 代理向当前配置远端 `https://github.com/64922/BridGes.git` 推送：`3665008b..b9fb1a99 main -> main`。`git ls-remote origin refs/heads/main`、本地 main、origin/main 三方一致为上述完整合并 SHA；另验证 Issue 分支是远端 main 的祖先。
- 推送确认后，工作树完整 `status --porcelain --untracked-files=all` 为空。忽略内容为测试临时数据库/对象、字节码/工具缓存及历史 test-results 日志；39 个忽略测试日志已另行原样备份并核对哈希至 `35-premerge-preserved/ignored-test-results/`，必要验收证据均在 Git。
- 已依次删除 `.worktrees/35-study-pause-and-appended-page-versions` 和本地 `codex/35-study-pause-and-appended-page-versions`（原 HEAD `185d7f12`）；确认路径、分支均不存在。`git worktree prune --dry-run --verbose` 无失效项，再执行 prune 后复核正常。
- 其他任务保留：工单 40 工作树与分支（HEAD `3bd92eff`）、其独立基线工作树（detached `7818c34`）、其他本地分支，以及主工作区其他任务未跟踪验证产物。备份的旧日志保持未跟踪，不混入本票代码提交。
- 本段记录及合并后验证产物随后作为 main 的文档收尾提交交付；最终远端 SHA 在用户汇报中给出并再次核对。

## 剩余限制

网关替身与故障注入证明确定性状态/事务/版本/恢复机制。真实 OCR、范围冲突科学裁决、总结措辞与教学体验仍按评测票 42 验证；本票没有新增真实模型评测、外部可得性或浏览器联调证据。仓库全量既有失败和类型基线不为零，本记录不宣称仓库全绿。
