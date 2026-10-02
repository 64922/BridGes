# 12 — 主智能体理解混合入口与跨轮任务关系

**What to build:** 日常用户明确查找意图可直接启动登记能力，正文优先于菜单提示；聊天、修改、暂停和取消正确续接，学习模式阶段仍受代码保护。

**Blocked by:** 01 — 记录已确认合同替代与增量迁移接缝；04 — 守住最终模型载荷预算与材料权威边界；08 — 保存有来源的跨轮任务、有效条件与澄清；09 — 持久化整次运行预算与有限调整额度；10 — 以校园通勤贯通持久节点、收据与执行内核；11 — 解析任务指代并补回必要原文

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

固定 module_id 与用户明确正文或复合目标冲突；旧澄清回扫缺乏任务归属。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/discussion.md](../../../docs/workflow/discussion.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/daily-workflows.md](../../../docs/workflow/daily-workflows.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。

## 任务内容

1. 同一次结构化理解输出轻量回复或 new/continue/revise/pause/cancel 关系、原话目标、硬条件、缺项、目标引用及登记节点建议。路由和参数尽量合并，不额外叠加规划调用。
2. 问候/陪伴采用该次轻量回复或现有一次文本流式，不创建复杂任务、专业委派或统一裁判；已识别单模块填登记配方，复合计划在 37 集成。
3. 保留请求 module_id 作为提示与历史标识，另存实际路由来源、能力列表和简短理由。论文 chip 加明确通勤正文按通勤处理；点击建议绑定任务版本，不重写原消息冒充首次选择。
4. 代码验证会话固定模式、任务归属/版本、能力注册、参数来源、硬条件、顺序/依赖和预算。正文中的只查论文/不要联网等限制优先，模型不能启动退役能力或切换模式。
5. 澄清只在会影响结果的必要缺项/歧义时提问，换话题暂停旧任务和激活等待；停止当前运行暂停任务，取消任务失效等待，重新发起有明确新版本/新任务。
6. 学习策略读取权威阶段，只在当前小节允许动作；否定/引用/假设和普通作答不触发阶段切换，“暂停复盘”与取消整个学习任务分开。

## 跨票接缝与责任

拥有主理解与模式/任务关系入口；领域节点由 10/24–36 注册，37 拓展真正复合调度，38 投影实际路由。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [ ] 普通聊天仍一次生成且轻量；明确自然语言单模块直启，模块提示冲突按正文。
- [ ] 含糊主题只问必要问题，不先搜索猜测领域；错误自动启动及硬条件绕过被门禁阻止。
- [ ] 换城市更新任务版本，暂停/换话题/取消不会误吞旧澄清或复活等待。
- [ ] 直接 API 与模型建议均无法切换锁定模式、越账户或调用未知/退役能力。
- [ ] 学习阶段意图的否定、引语、作答和暂停语义通过代码阶段门。
- [ ] 原请求标识、实际路由与版本在恢复/重试/导出中一致，旧消息不重标。

## 验证与交付证据

覆盖工作流 A01–A03、A06–A08、A11、L05、L08、R07–R08，检查实际调用和领域状态，不能只断言节点名。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 实施记录（2026-10-02）

分支 `codex/12-main-agent-hybrid-entry-and-task-relations`，基点 `main@2c003eed`；worktree `D:\BridGes\.worktrees\12-main-agent-hybrid-entry`。开发与验证均在 conda `agent` 环境。

### 实现说明

- **一次确定性主理解**：新增 `bridges/contracts/understanding.py`（`MainUnderstanding`、`UNDERSTANDING_CONTRACT_VERSION="main-understanding-v1"`、`RouteSource`、`HardConditionKind`）与 `bridges/chat/understanding.py`（`MainAgentUnderstanding.understand`）：同一次输出任务关系（new/continue/revise/pause/cancel）、原话目标、硬条件、缺项、目标任务引用与登记模块建议；不调用模型、不执行检索、不写状态。
- **正文优先、提示保留**：请求 `module_id` 只作提示与历史标识随用户消息持久化，不改写；路由快照新增 `requested_module_id`/`module_id`/`route_source`/`capability_list`/`understanding_version`。正文单模块直启优先于模块提示（论文/生涯经统一路由器编译可执行计划，其余走登记能力），未覆盖来源记 `MODULE_HINT`。点击建议重试另存 `SUGGESTION_CLICK` 并绑定当前任务版本，原消息不被冒充为首次选择。
- **硬条件与门禁**：`不要联网/只用本地/只查论文` 进入理解快照与路由开关；`_route_for_turn` 收紧 `web_search_allowed`/`knowledge_base_allowed`；`stream_turn` 新增 `web_search_allowed` 门禁教学强制搜索与普通搜索；重试沿用原轮理解快照，点击建议也不放宽硬条件；`不要查知识库` 为 `NO_LOCAL`（只关知识库，公网保持开放）。
- **澄清与任务关系**：含糊单模块（如「给我找几篇论文」）只产出一个澄清路由，不先检索猜测领域；多能力/指代歧义只问一个问题；`_apply_task_understanding` 在消息写入后落地任务关系并登记版本绑定的澄清等待，失败时不吞掉用户修改而是收敛为 409 `task_state_conflict`；停止生成先暂停任务；暂停任务不被无关长消息复活（须显式续接或等待作答）。
- **学习阶段门**：`study/review.py` 的暂停复盘/回到辅导正则加固；否定/引语/假设/普通作答不触发阶段切换，「取消整个学习任务」与「暂停复盘」分离；学习模式只读权威阶段、不派发日常模块，正文论文词也不产生澄清路由快照。

### 接口与迁移变化

- `CapabilityRoute` 新增可选字段（默认 None/空，向后兼容已存快照）；`MainCapability` 增 `COMMUTE/RESOURCES/TIEBA/GITHUB`；`MODULE_CAPABILITIES` 成为模块→主能力的单一映射（`bridges/routing`）。
- 运行配置新增 `understanding` 快照（含 `contract_version`），重试/恢复/审计读取同一份理解；消息 `route` JSON 携带理解版本、请求提示与实际来源。无数据库 schema 迁移（均为既有 JSON 列）。
- 持久状态卫生沿用既有单一事实源：消息路由随消息导出；运行配置按既有隐私策略不整包导出（只出额度合同）；删除/备份走 `lifecycle/catalog` 通用清单，本票快照随 `generation_runs` 行删除。

### 验证结果

- 新增 `tests/chat/test_improvement12_hybrid_entry.py` 47 项全通过：正文直启与提示保留、硬条件类别与门禁（含 `NO_LOCAL` 不关公网）、澄清一问、任务关系与版本、停止暂停、无关消息不复活暂停任务、建议点击重试保留硬条件、学习阶段门参数化、持久状态导出/删除合同。
- 按新合同更新 `test_golden_intent_routes.py`、`test_natural_language_paper_route.py`、`test_arxiv_search_chat.py`、`test_arxiv_retry_stale_chat.py`、`test_issue03_atomic_first_turn.py`；定向合并复跑 182 passed（含 `tests/routing`、`tests/tasks`、`test_v2_19_study_review.py`、web search）。
- `tests/chat` 全量：86 failed / 939 passed / 1 xfailed；失败名单与 `main` 基线 families 逐名一致（既存失败：teaching/career/mcp/selections 等），并比基线多修复 10 项 web_search 与 4 项 issue05/06 时延预算（`web_search_allowed` 由固定 False 改为按硬条件决策的预期结果）。
- `tests/integration`+`tests/learning`+`tests/routing`+`tests/tasks`：19 failed / 420 passed / 26 skipped / 2 errors，与 `main` 基线完全一致（Windows CLI/进程锁既存失败）。
- ruff 变更文件零新增诊断（`routing/contracts.py` I001/SIM102 与 `test_issue03_atomic_first_turn.py` F841 为基线既存）；mypy 三个改动源文件零新增错误（`service.py` 仅剩与 main 相同的 5 处既存 union-attr/arg-type）。
- 真实模型体验与外部可得性不属本票证据范围，按 39–42 评测执行。

### 代码评审与修复（Standards/Spec 双轴）

- 修复：暂停任务被无关长消息复活；`不要查知识库` 误关公网且合成 `source_span`；重试/建议点击丢失原轮硬条件；学习模式正文触发论文澄清快照；硬条件解析三份字面量；`_MODULE_CAPABILITIES` 重复与死代码（`suggestion_binding`/`actual_capability`/`has_hard_condition`/空分支）。
- 保留设计：普通聊天不创建任务；澄清等待只在任务域登记；真正复合计划调度由 37、实际路由投影由 38 承接。

### 已知限制

- `NaturalLanguageRouter.classify` 产生的职业澄清（understanding 未识别为模块意图时）不登记任务等待，答复依赖下一轮重新理解；跨轮澄清持久化属工单 08/09 的任务域接缝，本票只统一「一次理解 + 任务关系」入口。
- 运行配置中的理解快照不进入账户导出（沿用既有「配置含私人材料不整包导出」策略），导出以消息路由中的实际来源与版本为准。

