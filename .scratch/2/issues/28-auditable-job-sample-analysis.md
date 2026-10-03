# 28 — 用可核验岗位样本交付职责与薪资分析

**What to build:** 用户只查岗位时得到真实样本范围、能力要求及可比较薪资，不被强制要求画像或简历。

**Blocked by:** 10 — 以校园通勤贯通持久节点、收据与执行内核；12 — 主智能体理解混合入口与跨轮任务关系；15 — 按解析任务选择检索材料与模块上下文；21 — 按任务、边界与前文适配有分寸表达

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

职业建议不能以虚构/相邻岗位样本支撑，岗位要求也不能直接等同个人能力差距。先完整交付公开岗位分析分支。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/daily-workflows.md](../../../docs/workflow/daily-workflows.md)。
- [docs/workflow/discussion.md](../../../docs/workflow/discussion.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/v2/feasibility.md](../../../docs/v2/feasibility.md)。

## 任务内容

1. 登记 career.parse/plan/collect/filter/analyze/verify；保留岗位原词/职责意图、阶段、城市/经验条件，明确情报与个人准备分支。
2. 只用登记公开来源，记录公司/城市/日期/职责/薪资原文和真实页面；无详情仅来源线索，不生成样本分析。
3. 相同职责可用语义证据对应，目标不自动变相邻岗位；前端/测试不混入后端。去重、过期、城市/经验逐条代码核对，未知不进入相应统计。
4. 薪资保留币种/时间单位/月数和缺失字段，不可比较计薪不混算，统计只代表检索样本。
5. 换城市产生新版本并失效旧城市样本/统计，复用未改变背景与无关证据；节点恢复不全链重跑。
6. 输出样本/范围/能力分布/薪资口径与未确认项，确定性模板自然但不承诺就业或薪资；只查岗位不读完整个人背景。
7. 29 基于本票产物做个人差距，不能把公共样本本身当画像事实写入。

## 跨票接缝与责任

拥有岗位样本、职责矩阵及薪资统计产物；29 负责个人规划，37 消费需求组合。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [ ] 只查岗位无需画像/简历仍可完成，未知个人能力不判薄弱。
- [ ] 每样本有真实详情和条件依据，邻近岗位/城市未知样本不混统计。
- [ ] 薪资单位/月数/币种和缺失口径正确，不宣称全国市场或结果保证。
- [ ] 上海改杭州仅重算相关节点，旧统计不复用为当前数据。
- [ ] 链接级降级、样本不足、来源失败和节点恢复真实可查，历史结果兼容。

## 验证与交付证据

覆盖 A06、A16，固定招聘详情验证去重/统计与改城市依赖失效；真实公开样本门在 42。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 执行与验收记录

### 2026-10-03：实现与本地验证

- 交付分支 `codex/28-auditable-job-sample-analysis`（工作树 `.worktrees/28-auditable-job-sample-analysis`，基点 main `68357a20`）；本记录与实现同一提交，待独立验收。
- 内核迁移：新增 `src/bridges/career_plan/kernel.py`——六节点配方 `career.parse→plan→collect→filter→analyze→verify`（`career-job-sample`／`career-job-sample-recipe-v1`；能力版本 parse/plan/filter/analyze 升 v2，collect/verify 为 v1），登记三个必要质量门 `career.sample_evidence`／`career.stats_caliber`／`career.conditions_hold`，`career.independent_review` 为可选门且如实标注未执行；`CareerBudget` 接共享预算账本，`CareerNodeFlow` 节点执行体不调用模型；收据按输入键（含任务条件摘要 `prior_digest`）恢复，重试只回填不重跑已完成节点。
- 领域合同（全部 add-only，`messages.career_plan` 仍是既有 JSON 列，无表结构迁移）：`JobSample.match_basis/duty_evidence/experience_evidence`；`SalaryInterval.currency/salary_months`；`CareerAnalysis.missing_salary_count/experience_unverified_count`；`CareerPlanProjection.experience_hint`。
- 能力口径：职责语义匹配按岗位族锚点两票制（`DUTY_ANCHORS`），相邻族占优或仅一票不纳入；经验条件逐页核对，页面未给经验即剔除并计数 `experience_unverified_count`；薪资按（币种，计薪单位）分组，先认美元后人民币，`·N薪` 只记发薪月数、不并入区间；城市/经验以筛选条件如实披露，未知城市/未知经验不进入对应统计。
- 服务与接缝：`service.py` 改为 NodeKernel 驱动；失败投影在守卫事务之外单独落库后交给父图收敛节点位置；`CareerSupersededError`→`DailyGraphSuperseded`；`chat/task_materials.py` 登记 `career` 任务上下文（city/other/count）；`api/main.py` 注入 `task_version_provider`（换城市等条件版本变化进入 parse 输入键，旧产物不复用）。
- 路由修复（恢复本模块的必过流程）：移除 `understanding.py` 对显式 career 请求的「职业规划：」包装，显式模块请求不再被 Issue 29 生涯规划澄清劫持；普通聊天命中求职语境只落一键建议，不自动检索（职业模块显式入口语义）。同步把 Issue 12 的 `test_career_service_parses_accumulated_goal_without_changing_message` 补丁点迁到内核解析调用点；`test_other_modules_still_rejected_and_no_silent_search` 的正文改为不触发其他模块的句子（Issue 12 起正文单模块意图优先于模块提示）。
- 新增/更新测试：`tests/career_plan/test_career_plan_kernel_acceptance.py`（六节点配方与收据复用、重放零外部调用、三门拦截与放行）、`test_career_plan_module_flow.py`（换城市新版本不复用旧统计、重试复用已提交节点且零重取）、`test_career_plan_core.py`（职责两票制、经验未知计数、币种不混算、缺失口径、旧投影兼容）。
- 修复实现缺陷：`FilterOutcome` 冻结导致经验未知计数崩溃；`salary.py` 计薪周期 mypy 收窄。

验证（conda `agent`，Windows、Python 3.11）：

- `python -m pytest tests/career_plan -q`：**85 passed**。
- 定向回归 `tests/chat tests/kernel tests/paper tests/tieba tests/resources tests/github tests/commute tests/career tests/career_plan`：分支 **52 failed / 1839 passed / 1 skipped**；main 同构基线 **57 failed / 1822 passed / 1 skipped**；逐项对比 **0 新增失败**，对比基线修复 5 项（澄清跨轮恢复、失败轮结束等待、经验逐条核对、普通聊天只给建议、显式模块拒绝；含一项按新合同重命名的经验用例）。
- `python -m ruff check` 本票文件全过（`chat/graph.py` 两处 N818 为 main 既有）。
- `python -m mypy src/bridges/career_plan src/bridges/chat/understanding.py --no-incremental`：22 errors / 10 files，与 main 同构逐条一致（本票新增 `kernel.py`，career_plan 零错误）。
- `git diff --check` 通过。

限制：

- 未跑全量 5285 项；以定向回归集（1892 项）与 main 基线逐项对比判定无新增失败。
- 剩余 52 项失败均可在 main `68357a20` 同构复现（集中在 `tests/chat/test_career_planning_chat.py` 与各模块 flow 的建议/正文语义），不属本票接口；真实招聘来源可得性与真实模型体感在 42 号票验证。
- `career.independent_review` 可选门当前为确定性规则复核，未执行独立模型复核（已如实记录）。

状态：`ready-for-human`（等待独立验收）。

