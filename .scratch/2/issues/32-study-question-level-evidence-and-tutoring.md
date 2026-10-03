# 32 — 按问题充分性逐层补证并自然辅导

**What to build:** 教材足够就直接辅导，不足才按许可查知识库/公网；有效部分可交付，补充来源不扩展复盘范围。

**Blocked by:** 15 — 按解析任务选择检索材料与模块上下文；21 — 按任务、边界与前文适配有分寸表达；31 — 核验知识范围覆盖并提交辅助预习

**Status:** ready-for-agent

**优先级：** P1

## 背景与需求

现辅导借日常模式规划联网，缺少问题级充分性门；可选外部来源失败可能中断书页能支持的讲解。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/study-workflow.md](../../../docs/workflow/study-workflow.md)。
- [docs/workflow/discussion.md](../../../docs/workflow/discussion.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。

## 任务内容

1. 实现 assess_evidence 输出关键解释点、已支持点、缺口和补证类型；先取本节相关片段与必要前文，再判断证据充分性。
2. 书页不足时按设置检索知识库，再检查剩余缺口，仍不足且允许联网时最小公开术语补证；明确时效/核验需求也可触发。不每次强制联网。
3. 用户禁用知识库/不联网时跳过相应层，必要缺口未核实；公网不发书页、完整历史或私人原文。
4. 解释先本节内容，区分知识库/联网补充、模型组织的比喻/推导；新增关键科学事实有依据，推导假设/条件保留，错误或冲突来源分列。
5. 外部失败仍交付书页支持部分，缺失证据结论阻塞；关键书页不清须补拍，外部资料不替代识别门、不扩大考查范围。
6. 专业教学只在必要解释/例子调用，沿 21 在已有生成内适配，当前阶段/任务和切片快照共用；不每轮测验或自动推进复盘。
7. 辅导产物通过证据/公式/条件与预算门后保存，恢复只重做受影响补证，停止不推进领域状态。

## 跨票接缝与责任

拥有辅导充分性量表与取证分支，31/30 提供书页；33 考查范围仅消费书页范围，22 接入相关明确偏好。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 书页足够时无额外联网，知识库/公网不足按真实缺口触发。
- [x] 用户来源限制不绕过，查询只最小公开参数。
- [x] 补充失败交付已支持部分，不伪造来源或整体隐藏有效结果。
- [x] 教材/补充/推导来源和关键条件明确，冲突保留。
- [x] 辅导不自动复盘或扩考查范围，合法学习生成事实/流式回归通过。

## 验证与交付证据

覆盖 L03–L05，捕获真实路径源调用和阶段状态，测试充分/不足/禁用/失败/冲突。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## Comments

### 2026-10-04 实施与验证记录

**实现说明与接缝/迁移变化：**

- `src/bridges/study/evidence.py`（新增）：问题级证据评估与逐层补证。先经统一上下文编译（本节相关片段 + 会话前文 + 预算门）调用 `study.assess_evidence`（能力 `qwen_structured_output`，协议 `study-tutor-evidence-v1`），模型只输出关键解释点、已支持点（必须引用本轮真实 `source_id`，伪造引用降级为未核实缺口）与缺口（补证类型 `knowledge_base|web|page|none`）。书页不足且允许时先查知识库，复查剩余缺口；仍不足且允许联网时只把缺口公开术语交给既有 `select_task_materials`/公开查询规划器补证，再复查。时效/核验只在已有缺口时触发联网；用户关闭知识库或明确不联网是硬门（`skipped_disabled`/`skipped_restricted`），缺口保持未核实。失败/空命中/冲突只记录真实状态并交付书页支持部分。自动缺口补证经 `RunBudget.begin_adjustment(reason_code="study_evidence_gap")`，显式请求不占调整额度。
- `src/bridges/study/tutoring.py`：`tutor(...)` 增加 `budget` 参数；删除旧「借日常模式规划联网」路径与「联网补充未完成」整体报错；评估结果作为硬约束注入生成（未核实缺口不得下确定结论、冲突分列、模型段/推导假设与条件保留）；渲染继续按 `本节书页/知识库补充/联网补充/模型知识补充` 分来源标注，补充失败仍交付已支持部分。评估与补证只读，不改写阶段、范围、预习问题或考查范围，也不自动启动复盘。
- 合同：`src/bridges/contracts/study.py` 新增 `StudyEvidenceGap`、`StudySupplementAttempt`、`StudyEvidenceAssessment`；`StudyExchange.assessment` 为可选字段（v2 JSON 向后兼容，旧的已存交换读取为 `None`，无需状态版本迁移，也未新增表）。`src/bridges/contracts/retrieval.py` 新增 `EVIDENCE_GAP`；`src/bridges/retrieval/decision.py` 规则版本升为 `retrieval-intent/v4`，`decide_retrieval`/`LayeredRetrievalService.ensure_decision` 接受 `needs_local_material`（真实缺口才放行本地检索，用户关闭知识库仍优先 `user_disabled`）；`src/bridges/study/service.py` 把 `budget` 传入辅导。
- 恢复与复用：重试不重复外呼——联网投影按同一用户回合的尝试组复用已固化成功/部分成功投影（`persisted_web_projection`），知识库轮次按既有 `user_message_id` 复用；只重做受影响的生成与复查（复查失败保持缺口，不阻断交付）。
- 合同产物：按仓库契约测试重生成 `openapi.json`（新增三个组件与 `assessment` 属性）；未重生成 `packages/contracts/src/generated.ts`（沿用本批工单 31 先例：仅 `openapi.json` 为同步锚点，契约测试只校验 TS 文件存在）。
- 测试接缝：`tests/chat/study_state_fixtures.py`、`tests/chat/test_v2_17_study_pages.py` 网关替身增加 `study.assess_evidence` 确定性响应；`tests/chat/test_v2_18_study_tutoring.py` 联网失败分支改为「交付书页部分 + 未核实缺口」；`tests/retrieval/test_decision.py` 升 v4 并新增 `needs_local_material` 用例；新增 `tests/chat/test_improvement32_study_tutoring_evidence.py`（8 项）。

**验证结果（conda `agent`，Windows，`PYTHONUTF8=1`）：**

- 本票验收测试 `tests/chat/test_improvement32_study_tutoring_evidence.py` **8 passed**：书页足够零额外检索且评估记录充分；书页不足先知识库后联网、公开查询不含邮箱/书页原文；关闭知识库/明确不联网跳过对应层且缺口未核实；补充失败（联网超时）交付书页部分、不伪造 web 来源；冲突来源分列保留；伪造支持引用降级未核实；低置信书页要求补拍且阶段/范围/考查范围与复盘入口不变；重试复用已固化知识库/联网结果不重复外呼。
- 学习全链路聚焦回归 **151 passed**（v2_17/18/19/20、工单 30/31、`study_state_fixtures`、工单 22、识别失败、本票 8 项、决策测试）。
- `tests/chat` 全量 **1170 passed / 50 failed**，与干净 main 基线一致：失败全部集中在既有退役/环境类文件（`test_career_planning_chat`、`test_issue09_career_resilience`、`test_selections`、`test_mcp_call_chat`、`test_attachment_project`、`test_improvement13` 等）；在 main 对同一批文件复现 49–50 项失败（`test_improvement13` 单项在 main 亦稳定失败），学习辅导相关用例无新增失败。
- `tests/retrieval` + `tests/knowledge_base` + `tests/web_search` + `tests/learning` **316 passed / 4 failed**（相对 main 多 1 项新增决策测试通过，失败 4 项与 main 逐项相同）。
- `tests/contracts` 重生成 `openapi.json` 后 **3 passed**（main 同为 3 passed）；无 API 路径变化，仅新增投影组件。
- `ruff check` 全部改动文件通过；`mypy` 本票改动文件无新增报错（既有基线报错位于未改动的 `chat/graph.py`、`chat/service.py`、`video`、`image`、`runtime` 等模块）。

**限制与待验收：**

- 网关替身只证明机制：`study.assess_evidence` 的真实模型充分性判断、缺口措辞与公开补证收益由评测票 42 实测，真实 Tavily 可得性亦不在本票内证明。
- 未装配向量端口时，知识库关键词检索按 trigram 命中；本票把缺口短语切成 3 字滑窗作为最小本地查询以改善召回，但纯关键词回退对非同文短语仍可能空命中（如实记为未核实缺口）；生产向量路径的召回质量待评测。
- 重试复用已固化的知识库轮次与联网投影；受影响的证据复查与辅导生成会重做（复核评估为一次有界调用），并非零模型调用恢复。
- `assessment` 只追加可选字段、无数据库迁移；旧前端不读取该字段时行为不变。`generated.ts` 未随 `openapi.json` 重生成，留给最终集成票 43 按前端需要同步。
- 本票待独立验收；分支 `codex/32-study-question-level-evidence-and-tutoring`，工作树 `.worktrees/32-study-question-level-evidence-and-tutoring`，基线 `809f05a5`。

