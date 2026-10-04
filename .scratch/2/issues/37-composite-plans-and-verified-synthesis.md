# 37 — 执行跨模块依赖计划并统一核验综合结果

**What to build:** 同一用户目标可组合登记能力，独立节点并行、依赖节点顺序执行；统一回答只使用合格产物并说明未完成项。

**Blocked by:** 12 — 主智能体理解混合入口与跨轮任务关系；24 — 论文语义匹配、分层阅读与可恢复交付；25 — 按目标组织证据支持的精简资料路径；26 — 以必要功能证据矩阵推荐 GitHub 项目；27 — 按规定、体验和混合问题编排贴吧取证；28 — 用可核验岗位样本交付职责与薪资分析；29 — 依据最小背景区分个人差距与待确认项

**Status:** ready-for-agent

**优先级：** P1

## 背景与需求

复合请求不能只派一个模块或拼六段回答；模块自行终态使公共核验只能看到 DONE。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/discussion.md](../../../docs/workflow/discussion.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/daily-workflows.md](../../../docs/workflow/daily-workflows.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。

## 任务内容

1. 主智能体提出有限跨模块计划，内核验证登记能力、参数来源、DAG、模式、必经步骤和总预算；专业角色只提建议不能直接互调。
2. 论文+资料共享主题/基础独立执行；选定论文→GitHub 必须先确认论文身份；岗位需求/差距→资料/GitHub 仅用户目标需要时启动。
3. 全部分支共享 09，最多一轮受控调整包含必要补证/结构修复，不能每模块独立一轮；未知能力、新目标/硬条件放宽被拒绝。
4. 模型专业结论绑定具体证据和读取层次，确定性计算先代码校验。复杂比较/冲突/关键公式/个人差距或代码实现不足按风险独立核验。
5. 独立核验看结论、原证据和规则，不依赖生成者辩护；核验引用/裁决结构也校验，同模型独立调用不等于独立事实来源。
6. 产物运行状态与可信状态分开；必要步骤失败只阻塞依赖结论，可选失败或独立分支保留有效部分。综合后最终门检查新事实、限定条件和引用错连。
7. 任务条件改变、画像/材料撤回、时效要求变化沿输入依赖失效；无关节点不重跑，兼容 Schema/能力版本和权限验证后才复用。
8. 模块不自行结束助手消息，主结果统一终态提交；一次目标组织简洁解释与详情，不拼多个独立长回答。

## 跨票接缝与责任

拥有跨模块调度/综合质量门，10 的内核与各领域配方复用；38 接入可见可信块，43 复核学习与日常共享边界。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [ ] 论文+资料可并行且一总预算，论文身份不清不宣称对应实现。
- [ ] Java 后端实习目标组合岗位/最小背景/资料/项目，私人简历不发公网。
- [ ] 换杭州且资料暂不改仅重算相关岗位/建议依赖；仍有效公共材料复用。
- [ ] 关键结论支持不足被门阻止，普通闲聊/纯工具不强制模型裁判。
- [ ] 单分支可选失败有效部分仍交付，必要失败阻塞相关结论。
- [ ] 非法循环、未登记能力、硬限制绕过、第二轮调整及旧版本迟到写入均拒绝。

## 验证与交付证据

覆盖 A04–A06、A09–A11、R02–R08，记录实际调度/调用/质量裁决与依赖失效。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 实现记录（本地实现，待独立验收）

分支 `codex/37-composite-plans-and-verified-synthesis`，工作树 `D:\BridGes\.worktrees\37-composite-plans-and-verified-synthesis`，基点 `main@48d12a45`。开发与验证使用 conda `agent`（Windows、Python 3.11）。合同版本 `composite-orchestration-v1`。

### 交付内容

1. 新包 `src/bridges/orchestration/`：
   - `contracts.py`：计划/步骤/参数来源与分级/校验拒绝码/步骤结果/综合草案/最终门/复合结果等可序列化类型；综合产物登记类型 `orchestration.synthesis`（`COMPOSITE_ARTIFACT_TYPE`）。
   - `registry.py`：绑定真实领域配方的模块登记表与 5 个合法组合（paper+resources 并行、paper→github 身份依赖、career→resources、career→github、career+resources+github）；贴吧/通勤标记 `composite_ready=False`，复合校验拒绝。
   - `planner.py`：确定性构造与校验（未登记能力/配方版本/循环/模式/硬条件/参数来源缺引用/私人参数离设备/公网身份依赖/并行宽度/第二轮调整）。
   - `executor.py`：依赖波次调度、并行分支、共享 `CompositeBudget`（09 账本包装）、参数级输入指纹复用、条件键失效、必要失败阻塞依赖、可选失败保留、整次运行最多一轮受控调整、停止与提交守卫拒绝。
   - `synthesis.py`：按一个目标组织综合；最终门检查无证据断言、限定条件缺失、引用错连与核验结构；风险触发独立核验（复杂比较/来源冲突/关键公式/个人差距/实现证据），普通闲聊与确定性工具不强制模型裁判，同模型独立调用不视为独立事实来源。
   - `production.py`：模块服务 `defer_finalization` 交付 → 步骤结果适配、综合编排服务、终态映射与一次提交的投影汇总。
2. 模块无终态接缝：`bridges/contracts/modules.py` 新增 `ModuleDelivery`；`paper`/`resources`/`career_plan` 服务新增 `defer_finalization`（GitHub 既有同款）。defer 时内核照常提交产物与收据、不写助手消息终态；失败作为交付返回；迟到/失效仍抛 `*SupersededError`。
3. 聊天接线：
   - `chat/understanding.py`：多条能力信号命中**已登记组合**时不再逐条追问，`capability_list` 记录多模块；未登记组合保持一次澄清。
   - `chat/service.py`：复合路由（`MATCHED` + 多值 `capability_list`，不派发单模块）。
   - `chat/run_budget_ledger.py`：已登记复合运行冻结 `NORMAL`（60 秒、15 秒预留），全部分支共用同一本 09 账本。
   - `chat/graph.py`：`invoke_subgraph_or_chat` 复合分支（模式/硬条件/来源门 → 计划 → defer 调用 → 综合与最终门 → 计划/草案/门进检查点）；`verify_output` 核验最终门与成功分支的产物引用；`persist_result` 在 `RunCommitGuard` 守卫事务内**一次** `finalize_message` 提交全部投影与综合正文；迟到/停止按既有 `DailyGraphSuperseded`/`DailyGraphStop` 语义拒绝。
   - 私人参数（简历/画像）只由职业模块本地读取，不进入步骤解析指纹与公网步骤参数；岗位→GitHub 的 `GithubRequirementInput` 用岗位原词且 `identity_confirmed=True`；论文→GitHub 在上游身份未确认时 `identity_confirmed=False` 并带限定说明。
4. 持久状态：不新增数据库表/迁移；计划随运行配置、复合结果随父图检查点、分支产物沿用内核产物表与 09 账本（备份/导出/删除/审计沿用既有机制）。

### 接口变化

- 新增 `ModuleDelivery`（`contracts/modules.py`）：`module_id`/`status`/`projection_field`/`projection`/`content`/`message_status`/`error_*`/`retryable`/`artifact_refs`/`lock`/`wait_reason`。
- `PaperSearchService.run`、`LearningResourcesService.run`、`CareerPlanService.run` 新增关键字参数 `defer_finalization: bool = False`；`*RunOutcome` 新增 `delivery` 字段；默认行为不变。
- `MainUnderstanding.capability_list` 在已登记组合下可为多值；`CapabilityRoute.capability_list` 同步（合同已有字段，无版本变更）。
- 新增编排包公开类型与 `CompositeOrchestrationService`/`ModuleServiceStepRunner`（供 38 投影与其他消费者复用）。

### 验证结果

- 引擎机制 `tests/orchestration/test_issue37_composite_engine.py`：**14 passed**。覆盖 A04 并行+共享预算、A05 身份未确认不宣称对应、A06 换城市只重算条件相关步骤并复用公共材料、A09 可选失败保留有效部分/必要失败阻塞依赖、A11 无网与来源限制拒绝、R03 提交守卫拒绝迟到写入、R04 第二轮调整拒绝、R08 未登记能力/循环/硬条件绕过拒绝；均用真实 `BridgesDatabase` + 09 账本 + 内核 `RunCommitGuard`。
- 模块无终态交付 `tests/orchestration/test_headless_deliveries.py`：**6 passed**（defer 时消息保持 streaming 且投影为空、产物引用与内核收据一一对应、与 defer=False 控制组投影一致）。
- 聊天派发 `tests/chat/test_issue37_composite_dispatch.py`：**3 passed**（一次终态提交两个投影与综合正文；澄清写运行表且整体 `needs_input`；旧终态迟到写入被守卫拒绝）。
- 相关回归：`tests/chat/test_improvement12_hybrid_entry.py` + `test_improvement12_execution_guards.py` + `test_golden_intent_routes.py` + `tests/orchestration` 共 **155 passed**。
- 全量：`pytest tests -q` 为 **5242 passed / 202 failed / 39 skipped**；失败集合与本机 `main@48d12a45` 基线同源（对失败文件集合抽跑：main **196 failed / 286 passed**、分支 **196 failed / 286 passed**，失败清单一致），未发现本票引入的新失败。基线失败集中在已退役插件/MCP/学习项目等历史测试与本机环境差异。
- 静态检查：`ruff check` 对本票全部改动文件通过（仅 `graph.py` 两条既有 `N818` 命名告警，main 同样存在）；`mypy` 对 `orchestration`、`contracts/modules.py`、`understanding.py`、`run_budget_ledger.py` **无错误**（`graph.py` 仅余 main 既有的 `add_node` 重载与 GitHub 服务联合类型告警）。

### 剩余限制

- 真实模型体验与外部来源可得性按评测票验证；本票证据仅证明确定性机制。
- 综合最终门目前对生产交付的「无证据断言」判定以结构化投影与 `artifact_refs` 为主：各领域模块经 `defer` 交付尚未携带逐条 `Claim`，引擎层已用 `Claim` 覆盖核验/限定条件/引用错连机制；逐条 claim 的上送是 38/后续配方可用的强化点。
- 跨运行的复用（同一任务后续轮次传入 `prior_results`/`changed_conditions`）在引擎与 09 账本层已实现并测试；聊天父图目前每次运行独立执行，跨轮复合计划与产物重载留待投影/任务续接接线后启用。
- 论文身份需用户选定后才能进入 GitHub「对应实现」断言；本票保证未确认时明确限定，不声称对应。


