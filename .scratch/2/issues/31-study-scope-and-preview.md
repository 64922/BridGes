# 31 — 核验知识范围覆盖并提交辅助预习

**What to build:** 本节有效片段形成可追溯知识范围，预习引导覆盖核心内容，用户无需当场作答便进入辅导。

**Blocked by:** 21 — 按任务、边界与前文适配有分寸表达；30 — 按页恢复书页识别并定位关键材料疑点

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

每页引用一处不足以证明全节覆盖，知识点同名也不能作为唯一身份，生成完不等于问题已持久提交。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/study-workflow.md](../../../docs/workflow/study-workflow.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。

## 任务内容

1. 实现持久 study.map/verify_scope/preview；知识点使用稳定 ID，含概念/关系/应用/易错点及支持片段，不以标题为唯一键。
2. 实质教学片段都有知识点关系或有依据排除理由，形成片段→知识点→预习/复盘题的覆盖矩阵；结构门与关键定义/公式/关系正确性分开。
3. 范围矛盾回到受影响识别/映射修复，不生成更多知识点凑覆盖，不越材料门。
4. 通过范围核验后按密度生成阅读引导问题，覆盖核心点并引用 ID，预习无需用户立即答。
5. 问题与有效范围版本持久提交后进入辅导，停止/失败不提前推进；表达策略作用自然语言，不改片段/范围/游标。
6. 旧预习/范围版本可读，对新增页的适用性由 35 更新，不静默宣称原问题覆盖新页。

## 跨票接缝与责任

拥有知识范围与覆盖矩阵，33 冻结题目范围，32 按点取得书页；领域服务唯一更新阶段。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 同名概念与不同页公式不串 ID/依据，每个实质片段覆盖或排除有理由。
- [x] 关键定义/公式与原文核对，结构完整不能代替内容正确。
- [x] 预习数量随密度，核心点覆盖且不要求当场作答。
- [x] 只有提交成功才进入辅导，重放/重试不重复预习或错误推进。
- [x] 新旧范围/预习版本和来源可导出恢复，预算/表达接入保持一致。

## 验证与交付证据

覆盖 L02，使用多页同名概念/遗漏关系/错公式材料做全链映射与提交故障测试。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## Comments

### 2026-10-03 实施与验证记录

**实现说明与接缝/迁移变化：**

- `src/bridges/study/scope.py`（新增）：持久节点 `study.map` / `study.verify_scope` / `study.preview`（协议 `study-scope-v1`，配方 `study-scope-mapping` / `study-scope-preview`）。稳定知识点 ID 由代码确定性生成 `ku_<sha256(kind|title|排序片段ID)[:16]>`，同名不同片段不同 ID；范围版本 `scope-<sha256(协议|材料哈希|覆盖摘要)[:16]>`，材料不变版本稳定、变更加 revision。覆盖矩阵为「片段→知识点/有依据排除理由」；结构门 `study.scope_structure` 与内容正确性门 `study.scope_content` 分开，关键定义/公式/关系（`kind=relation` 或命中核心符号/定义模式）才走内容核对。预习问题按 `preview_bounds`（`min=max(1,ceil(core/3))`、`max=max(min,min(8,core))`）随密度出题，引用知识点 ID 与范围版本，不要求当场作答。
- `src/bridges/study/service.py`：`map_units` 以 `ScopeMaterial`（页身份 + 实质片段快照）走内核映射；验证失败（`study_map_invalid` / `study_scope_incomplete` / `study_scope_content_conflict` / `study_scope_content_unverified`）做一次有界修复：带失败反馈重跑映射并限制 `repair_unit_limit=上一版知识点数`（结构门 `repair_unit_added` 拒绝新增知识点凑覆盖），再失败即如实报错。范围核验通过即持久 `stage="preview"`（含 `scope`/`scope_history`）；`preview` 的生成与 `stage="tutoring"` 只在 `finalize_message` 的 `persist_learning` 事务内提交，停止/提交失败整体回滚。`preview` 自然语言经 `compile_turn_context` 注入表达策略（`run.config.global_writing_policy` 快照，只作用于提问措辞）与预算门。追加页路径把候选 `scope` 放进 `page_update`，提交成功才替换有效范围。
- `src/bridges/contracts/study.py`：`StudyUnit.{unit_id, kind}`；`StudyQuestion.{unit_ids, scope_version_id}` 并要求至少引用一个知识点；新增 `StudyCoverageEntry`（知识点或排除理由二选一）、`StudyContentCheck`、`StudyScope`（revision/material_hash/coverage/content_checks/verified/legacy）；`StudyPageUpdate` / `StudyState` 增 `scope`/`scope_history`，`STUDY_STATE_VERSION=2`，`upgrade_legacy_study_state` 读取 v1 时补确定性 `legacy-*` ID、`legacy-scope-v1` 与复盘 coverage 标题→ID，不改写阶段/判定/总结；保存统一写 `state_version=2`。
- `src/bridges/study/review.py`：复盘覆盖按 `unit_id` 归类，同名概念不串依据；题目输出要求引用稳定 ID。
- 迁移与生命周期：未新增表，`SCHEMA_VERSION` 保持 68；范围/历史存在 `study_states` JSON 内，复用 v65 `node_artifacts`/`node_receipts`/`node_outbox`（导出、删除、快照恢复均在既有账户清单内），跨运行按输入键复用映射/核验产物。
- 测试接缝：`tests/chat/study_state_fixtures.py`、`tests/chat/test_v2_17_study_pages.py`、`test_v2_19_study_review.py` 的网关替身改为按 `task` 返回新结构；新增 `tests/chat/test_improvement31_study_scope_preview.py`（6 项）。

**验证结果（conda `agent`，Windows，`PYTHONUTF8=1`）：**

- 本票验收测试 `tests/chat/test_improvement31_study_scope_preview.py` **6 passed**：同名概念跨两页 ID 与覆盖矩阵不串、范围版本绑定；遗漏教学片段先被结构门拦下、修复不增知识点数量且排除理由补全；错公式内容冲突 → 一次修复通过 / 持续冲突以 `study_scope_content_conflict` 终止且不发布预习；问题数量随密度（6 核心点首答 1 题被 `study_preview_incomplete` 拦下），重试只调 1 次映射、2 次预习后提交；旧 v1 状态读取升级为确定性 legacy ID 并导出恢复。
- 学习全链路：v2_17 书页 **22 passed**；v2_18/19/20 辅导/复盘/总结 **46 passed**；工单 30 识别与失败套件 **34 passed**；夹具及消费方（含 `test_study_state_fixtures` 六节点收据）**54 passed**。
- `tests/chat` 全量 **1150 passed / 49 failed**：49 项全部为基线既有失败（退役项目/插件/MCP/生涯 `first_turn_required` 与内容漂移类，如 `test_selections`、`test_mcp_call_chat`、`test_attachment_project`、`test_issue09`），已在干净 main 抽样复现同名失败；无学习范围/预习相关失败。
- `tests/kernel` + `tests/contracts` + `tests/workflows` + `tests/architecture` **69 passed / 1 failed**，唯一失败 `test_openapi_sync` 在干净 main 同样失败（既有漂移，未在本票重生成以免混入无关大 diff）。
- `tests/lifecycle` + `tests/state_copy` + `tests/closeout` **209 passed / 13 failed**，失败均为基线既有（退役 API 409/410、worktree 内 API 子进程 `No module named bridges` 环境问题、能力清单漂移、`test_three_journeys` 对纯文本学习首轮的旧预期——该预期已由 v2_17 的 422 断言固化）。
- `ruff` 改动文件全部通过；`mypy` 改动源码文件无新增报错（既有 `contracts/career.py` 等基线错误无关）。

**限制与待验收：**

- 网关替身只证明机制：映射/核验/预习的真实模型质量与 OCR/视觉体验由测评票 42 实测。
- 反例材料（多页同名概念、遗漏关系、错公式）以确定性响应构造；真实模型映射是否总能覆盖全部实质片段仍待评测。
- 重试复用的验证方式为失败后经 API 重试并断言映射调用不重复（跨运行按输入键复用）；未注入进程级崩溃恢复场景。
- `scope_history` 上限 8 个版本，更旧版本随状态淘汰（导出文件仍保留导出时刻的历史）。
- 本票待独立验收；分支 `codex/31-study-scope-and-preview`，工作树 `.worktrees/31-study-scope-and-preview`，基线 `68357a20`。



## 2026-10-03 独立验收与修复

原交付存在规范和需求缺陷，已独立两轴审查、修复并复验通过。协议/配方/能力/学习图同步 v2；阶段与预习正文在终态事务提交，旧图安全结束、显式重试新图；全部范围历史保留。综合复验150 passed，最后核心复验20 passed；内核合同69 passed/1个main既有OpenAPI漂移，mypy22项与main逐项一致，Ruff通过。真实模型质量仍由42评测。详见[独立验收记录](../acceptance/31-study-scope-and-preview.md)，它覆盖并更正上文实施报告的历史8版淘汰及旧协议限制。合并推送与清理结果随后追加到该记录。
