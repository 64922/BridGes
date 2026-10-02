# 15 — 按解析任务选择检索材料与模块上下文

**What to build:** 普通聊天和模块对同一任务取得正确主题、对象、条件与证据；材料查询最小化，公开服务不接收私人历史。

**Blocked by:** 04 — 守住最终模型载荷预算与材料权威边界；11 — 解析任务指代并补回必要原文；14 — 统一照片预算与旧附件、原图、证据读取

**Status:** ready-for-agent

**优先级：** P1

## 背景与需求

知识库/附件查询只看“第二个有什么区别”，模块各取最近 6 条用户消息，前文主题和最新条件难贯通。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。
- [docs/上下文工程/审查与改进建议.md](../../../docs/上下文工程/审查与改进建议.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/daily-workflows.md](../../../docs/workflow/daily-workflows.md)。
- [docs/workflow/study-workflow.md](../../../docs/workflow/study-workflow.md)。
- [docs/用户画像/提取时机与回答应用流程.md](../../../docs/用户画像/提取时机与回答应用流程.md)。

## 任务内容

1. 定义共同任务材料选择入口；理解前只用原文/固定模式/任务快照/少量消歧前文，确定动作后按节点目的选择材料。
2. 知识库、会话附件与画像各有最小查询语义，使用已解析主题/对象与必要前文；画像最终取用由 19 接线。
3. 模块声明所需任务字段、背景和证据范围，逐步替代固定最近 6 条用户消息及只看论文/GitHub 的锚点；确定性模块不被强迫用相同提示词。
4. 本地历史/画像/私人材料在本地选择组合；公开查询仅包含必要公共术语，不发送完整历史、书页或简历原文。
5. 检索许可、来源有效性、相对任务必要性和实际预算共用 04；每次后续模型调用记录自身采用清单，不以父图早期审计代替子模块实际输入。
6. 保留已有关键词/向量混合检索、附件与知识库分域，先修查询与选择口径，不预建新向量数据库。

## 跨票接缝与责任

提供 24–36 工作节点的最小上下文入口；19 独立实现画像可信/范围筛选，37 组合结果共用同一任务事实。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [ ] “第二个有什么区别”“继续解释”使用解析对象发起正确本地查询，末尾条件与最新纠正保留。
- [ ] 普通聊天→论文→GitHub 的同一目标可共享主题，无关旧条件不污染新任务。
- [ ] 公开请求参数含最小查询且不含私人历史/画像/简历；用户禁用来源时不调用。
- [ ] 模块实际采用清单与最终载荷一致；工具结果再加入后重新检查预算。
- [ ] 当前附件、全局知识库与两账户作用域测试通过，不因材料采用取得新工具授权。

## 验证与交付证据

固定语料和捕获工具参数测试跨轮检索与模块材料选择，真实召回效果在 40 评测。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

### 2026-10-02：实现与验证（待独立验收）

实施提交：`38e60031`（实现）与 `d16c5916`（独立评审修复），分支 `codex/15-task-aware-retrieval-and-module-context`（worktree 同号）。

- 新增 `bridges.chat.task_materials`（合同 `task-materials-v1`、`module-context-v1`）：确定性任务材料选择入口。知识库/会话附件/画像/公开检索各有最小查询，使用解析对象（结果列表/列表项标签）与当前有效条件；被取代/撤销/草案/线索条件只记排除 ID、不进查询；只有请求与任务都没有主题时才用最近少量消歧前文（`used_continuation_fallback`）。任务锚点的内部标签（`任务 {id}（版本 {n}）`）不作为主题词，避免纯续接把任务 ID 带进本地与公开查询。
- `chat.context_compiler`：预算裁剪后产出选择；`to_record()` 只含 ID/计数/查询指纹（审计唯一入口），`execution_record()` 加各域查询文本；`chat.service.compile_turn_context` 返回执行记录进图状态/检查点，审计仍走 `to_record()`；新增 `run_model_quota`、`module_task_context`、`audit_module_manifest`。
- `retrieval.service.run_round` 新增 `knowledge_base_query`/`attachment_query`：知识库与会话附件各用自己的最小查询，不同查询只向量化一次；授权不受材料采用影响（`use_knowledge_base` 仍只在用户禁用/决策处裁决）；账户/会话作用域不变。`chat.turn` 四处公开检索改用编译期最小公开查询，缺失时回退原文（与既有旧检查点行为一致）。
- 模块上下文：`MODULE_DECLARATIONS`（paper_search/github_projects/learning_resources/commute）声明任务字段、背景与证据范围；`build_module_context` 有任务时只取当前消息之前的任务来源/主题命中前文，条数以 `lookback` 封顶，实际采用的前文 ID 进 `source_message_ids`；无任务回退最近窗口。paper/github/resources/commute 接线，GitHub 任务来源锚点排在显式论文/仓库锚点之后。
- 子模块预算：新增 `payload_budget.evaluate_call_manifest` 共同入口（门 + 脱敏清单装配），论文概述与 GitHub 借鉴角度在工具结果加入后按各自最终载荷重新过门，超限闭锁不发调用并如实说明；`audit_module_manifest` 记录调用域、清单与门结果。无新表/迁移；无新审计正文。
- 独立评审（Standards + Spec 双轴，`main...HEAD`）：修复任务锚点污染查询、模块上下文无界/当前消息之后消息、采用前文未进来源清单、paper/github 复制预算器、`compile_turn_context` 文档与执行记录矛盾、`_run_retrieval` 退役术语；删除三处无调用者的投机导出/方法。
- 新增 `tests/chat/test_improvement15_task_materials.py` 19 项：指代/有效条件/指代词剥离、公开查询排除条件与内部任务标识、审计无正文、模块上下文任务来源过滤与有界/截止当前消息、编译集成（审计 vs 执行记录）、检索各域查询与 `USER_DISABLED` 不放开知识库、两账户作用域、论文任务主题续接、GitHub 任务锚点逐字照抄、论文/GitHub 最终载荷门超限闭锁与清单记录。
- 验证（conda `agent`，Python 3.11.15）：新测试 19 passed；`tests/paper`+`tests/github`+`tests/retrieval` 163 passed / 2 failed（两个退役项目入口失败在 main 基线逐项复现）；`tests/chat`+`tests/resources`+`tests/commute` 失败名单与基线 worktree 逐名一致（100 failed，既有失败），新增 19 项全过；其余套件与基线失败/错误名单逐名一致（162 failed / 9 errors）。ruff 变更文件全部通过；mypy `src` 98 errors / 20 files，与基线一致，无新增。
- 已知限制：tieba/career_plan 节点仍用固定窗口，等待其消费者票据按声明逐步接入；旧检查点没有编译期查询时公开检索回退原文（既有行为）；任务处于 paused/blocked 时条件是否继续参与由任务关系票据裁决，本票未改动；真实模型召回与概述质量按评测票验证。详细未决项由独立验收复核。


