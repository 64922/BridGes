# 12 — 旧学习只读退出与主动续学

**What to build:** 用户仍可阅读/导出旧学习记录，主动选择续学和本人旧材料后进入新模式对话；旧评分不变成长期掌握，旧运行、重试和隐藏入口不能重新执行五阶段流程。

**Blocked by:** 08 — 跨会话续学、岔题与目标切换；10 — 照片和文件资料的按需教学。

**Status:** ready-for-agent

**发布状态：** 用户已确认拆分与依赖，正式发布；尚未实施。

**对应合同：** D14；AC15、AC16；C01。

## 用户演示

1. 打开旧书页学习对话，预习、原问题、作答、判定、总结和材料仍可查看。
2. 用户点击主动续学，选择学习目标和仍可访问的旧资料，创建新模式对话。
3. 新导师按当前问题开始，旧评分只作为历史记录，不显示已迁入长期掌握。
4. 尝试旧重试、旧检查点恢复、旧建议或直接执行入口，均不能启动五阶段写入。

## 实施范围

1. 盘点前述各票替代的旧学习写入口、图注册、服务装配、前端动作、后台任务和隐式恢复路径；完成退出核查，不增加旧业务兼容层或双写。
2. 删除仍残留且不再被使用的旧执行实现和注册，仅保留历史读取合同、必要类型、来源定位和只读渲染。
3. 为旧会话提供明确历史状态和主动续学入口；禁止从历史停止/重试操作隐式恢复旧教学，不能仅隐藏按钮。
4. 续学必须由本人主动发起，创建新会话及新局部进展/长期目标引用。原消息、原判定和原材料身份不改写。
5. 沿用材料校验账户、可访问性、用户选择和真实来源版本，在新会话建立有权使用的引用。不得仅传旧对象 ID 绕过附件会话边界。
6. 对不存在、已删、已撤回或未解析材料逐项说明；可继续不依赖材料的新学习，不虚构已读内容。
7. 新会话创建与材料授权/引用提交保持一致，重复续学请求复用结果，失败保留选择，不产生孤立引用或重复会话。
8. 安全结束旧未完成运行/租约，按协议、图和能力版本拒绝旧检查点套新节点；旧任务处理结果不能写新档案。
9. 验证历史、数据迁移、导出、账户删除、备份及回滚兼容边界；回滚不能把新对象按旧阶段解释或删除。

## 接口、持久化与界面

- 历史投影使用 01 的独立读取边界，旧结果保持当时状态，私有字段不泄露。
- 主动续学的目标/材料选择、新会话关联、来源身份和审计可追溯；用户能取消，不自动迁入学习档案。
- 界面能区分旧记录只读与新学习，错误信息清楚指出失败的材料或创建步骤。
- 可访问的原资料通过新权限引用采用，不物理搬走或改变原历史来源；无法安全沿用时如实要求重新提供。
- 所有迁移和清理可重复运行，记录受影响对象及处置，不能扫描并删除无关账户或文件。

## 范围边界与依赖理由

08 提供可用的新目标/续学语义，10 提供新材料采用与授权边界。无需等待 11 的公开查证。前序票自身产生的孤儿代码应已删除，本票不能代替它们履行最小改动责任。

## 验收标准

- [ ] 旧聊天、作答、判定、总结、引用和导出保持可读。
- [ ] 用户主动续学新建新模式对话，取消与失败不改变原历史。
- [ ] 旧评分和旧无作答内容不被自动转换成正式长期学情。
- [ ] 材料选择、授权、版本和新会话引用真实一致，无跨域访问漏洞。
- [ ] 重复请求与部分失败不多建会话、不留下无权引用。
- [ ] 旧注册、写接口、建议、重试、后台运行和恢复全部无法复活旧流程。
- [ ] 没有新增旧业务兼容层、双写或长期双轨开关，必要只读合同保留。
- [ ] 旧运行安全收尾，新旧检查点与数据不混用。
- [ ] 备份、迁移、导出、删除、重复执行和回滚有实测证据。
- [ ] 日常三个模块、通用聊天和 C01 审查无新增回归。

## 验证与交付证据

- 构造不同旧阶段、未完成题、已判题、失败运行和已删除材料的历史样本。
- 从 UI、公开请求、服务装配与后台恢复多个入口尝试旧执行，证明服务端禁写，不只检查菜单。
- 覆盖重复续学、并发首轮、材料授权失败、事务回滚、他人对象和撤回来源。
- 以隔离数据库做迁移与回滚演练，检查原历史和新对象的独立性；记录退出清单及保留只读合同理由。

## Comments

2026-10-13：历史承接与旧模式退出切片草稿，待拆分确认。

2026-10-13：用户确认「按此发布」，本票发布到本地问题跟踪器。分诊状态表示信息齐备；须待直接阻塞票验收完成后实施，本轮未开始编码。

2026-10-14：开始实施（执行者：OpenCode 智能体，conda `agent` 环境）。
- 基线：main `cbbcb27d`（直接阻塞票 08 合并 `15c807ca`、10 合并 `c034f69e` 均已验收并集成）。
- 分支/工作树：`codex/issue-12-legacy-retirement-and-restart` / `.worktrees/12-legacy-retirement-and-restart`。
- 本次负责的共享接缝：`chat/service_context.py`、`chat/service_generation_records.py`、`chat/service_generation_admission.py`、`chat/service_retry.py`、`chat/graph.py`、`chat/repository.py`（首轮事务）、`storage/database.py`（迁移 v78）、`state_copy/catalog.py`、`apps/web`（聊天页与 StudyProgress）、`tests/evaluation`（表达式评测与工作流清单的学习部分适配）。
- 迁移编号：v78（当前 v77），仅新增 `chat_attachments` 采用来源列，不新增表；无其他执行者并发修改同一迁移。
- 退出范围：删除 `bridges/study` 旧五阶段执行模块（`service/kernel/scope/review/review_kernel/grade_kernel/summary/tutoring/evidence/turn_result/ports`）及其专用测试与评测学习部分；保留历史读取合同（`contracts.study`、`study/history.py`）与只读渲染；冻结的 V2 放行产物校验保留（清单标记退役，历史报告仍可读）。

2026-10-14：实施完成（第二轮记录）。合并 main 前进 3 个提交（issue 09 自适应教学）后同步：
- 迁移重编号：main 已占用 v78（`study_record_adoptions`），本票迁移改为 **v79**（`chat_attachments` 采用来源列），`SCHEMA_VERSION = 79`；合并冲突（`service_stream.py` import、`database.py` 迁移号）按双方意图解决，合并提交 `27376429`。
- 契约与前端：`openapi.json` 与 `packages/contracts/src/generated.ts` 从当前 FastAPI 合同重生成（新增 `/study/restart-options`、`/study/restart` 与 2 个 schema；`tests/contracts/test_openapi_sync.py` 通过）。`StudyProgress` 旧记录分支不再提供旧执行操作（开始/继续复盘按钮已删，服务端亦禁写），新增「主动续学」面板：旧历史只读说明、用户点击后才读取选项、目标/材料选择与补充说明、失败保留选择并逐项标注已不可访问的目标/材料、同选择重试复用幂等键；`page.tsx` 接线（`getStudyRestartOptions`/`restartStudyConversation`/导航到新会话）。
- e2e 清理（旧执行专用，UI 断言与测试替身已随执行退役）：删除 `v2-17..20-study-*.spec.ts`、`issue08-conversational-learning.spec.ts`、`issue23-learning-mode.spec.ts`、`issue38-domain-paths.spec.ts` + `playwright.issue38.config.ts`（其固定来源服务的旧学习替身已删；`.scratch/2/acceptance/38-browser/README.md` 追加退役说明）；`release-gate.spec.ts` 学习旅程与 `issue21-desktop-acceptance.spec.ts` 学习段改为新导师断言（局部学习进展、无教学卡）；`scripts/closeout_acceptance.sh` E2E 列表同步移除。
- 保留只读合同理由：`contracts.study` 与 `study/history.py`（版本化只读解释、私有字段不泄露）、前端 `StudyProgress` 只读渲染、`state_copy/catalog.py` 的 `chat.result.study.*` 文案、评测冻结产物（L01–L13 退役标记、来源定位保留）都是历史可读性所需，不是旧业务兼容层；`learning/teaching_gate.py`、`learning/progress.py` 与 `tests/learning/test_teaching_gate.py` 因评测与装配仍在引用而保留。
- 例外记录：`chat/turn.py` L3239–3735 旧同步教学分支未删除——所有入口（HTTP/重试/首轮/后台恢复/流订阅）均有守卫或收敛，该分支不可达；删除触碰面大于本票收益，留待后续清理票。
- 删除的专用测试与实现（本分支 vs main 共 51 个文件删除）：`bridges/study` 旧执行 11 个模块 + 评测脚本 2 个；`tests/` 下 30 个文件（`test_improvement30..36_*`、`test_issue02_learning_teaching_overhaul.py`、`test_issue03_learning_evidence_chat.py`、`test_issue08/18/19/31/34/38` 旧学习相关、`test_teaching_chat.py`、`test_v2_17..20_*`、`test_study_recognition_failures.py`、`test_study_state_fixtures.py` 与替身服务 `issue38_browser_api.py`）；前端旧执行专用 e2e/配置 8 个（见上）。
- main 既有红测（非本票造成，未修，仅记录）：`test_improvement12_hybrid_entry.py`（9）、`test_improvement12_task_relations.py`（2）、`test_issue42_scenario_gaps.py` A09/R07（2）、`test_issue42_ambiguous_resources.py`（4）、`test_issue39_*` 外 runner_reproducibility（4）、release_adjudication（2）、mail_verification（3）、authenticity_gate（1）、capability_manifest（1）、user_extensions retirement（11）等 2026-10-13 日常模块收敛遗留；`.scratch/daily-module-retirement.md` 明示「592 通过/71 失败，后续整理全量基线」，本票不代偿修复。R07 韧性锚点仍引用其中两条红测，随该债务处理。
- 顺带发现（超范围未修）：论文模块轮在助手消息上残留 `arxiv_search: loading` 初始投影（消歧后不清理，main 既有）。
- 验证证据：聚焦批次 18/4/5/1/3/29/36/14 passed；合并后验证 34 passed；全量分类对比 branch 失败数 ≤ main 且无 branch 独有失败；前端 `vitest` 286 passed、`tsc --noEmit` 0 错、`next lint` 无新告警、`next build` 成功。

2026-10-14（合并与推送记录）：
- 合并提交 `d6809f24`（`merge: issue 12 legacy study retirement and learner-initiated restart`）；功能分支提交：`8d2a78f8`（旧执行退役与续学后端）、`27376429`（先并入 main 前进的 issue 09）、`abe9ce70`（测试/门禁同步）、`723d9b71`（前端续学面板与契约重生成）、`6ce39b6d`（import 整理）。
- 合并解析要点：迁移号顺延 **v79**（main 已占 v78）；`chat_attachments` 新增采用来源两列；`openapi.json`/`generated.ts` 重生成后 `test_openapi_sync` 通过。
- 合并后验证（主工作区，conda `agent`）：`tests/contracts+lifecycle+storage+retirement+state_copy+closeout` 与 issue08/11 链路 **284 passed**；11 项失败全部为既有 `test_user_extensions_retirement.py` 债务（main 复现一致）。全量证据：chat/learning/kernel 94 失败与 main 完全一致（1274 passed）；storage/api/architecture/persistence/identity/integration/paper/projects/scope/expression 5 失败、ingestion 5 失败、ai..mcp 组 91 失败、media..workflows 组 94 失败均逐条在 main 复现；`tests/career_plan` 收集错误与 main 相同。前端 `vitest` 34 文件 286 项、`tsc --noEmit`、`next lint`、`next build` 全部通过。
- 推送：本地 main 经代理 `http://127.0.0.1:7890` 推送 `origin/main`（`1d1e1b59..d6809f24`）；`git ls-remote origin refs/heads/main` 与本地 HEAD 一致（`d6809f24`）。
- 清理：删除工作树 `.worktrees/12-legacy-retirement-and-restart` 与本地分支 `codex/issue-12-legacy-retirement-and-restart`；其他任务工作树（含 `worktree-12-frozen-holdout-release-acceptance`）保留。
