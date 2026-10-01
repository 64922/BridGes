# 11 — 解析任务指代并补回必要原文

**What to build:** “它／第二个／继续／照之前的条件”能续接唯一对象；实质歧义只问一个必要问题，查找失败如实说明本轮未定位。

**Blocked by:** 04 — 守住最终模型载荷预算与材料权威边界；08 — 保存有来源的跨轮任务、有效条件与澄清

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

中文连续串和回指词门槛漏掉预算、“继续”和结果对象；近期已有对象也可能被误标为未找到。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/上下文工程/讨论记录.md](../../../docs/上下文工程/讨论记录.md)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。
- [docs/上下文工程/审查与改进建议.md](../../../docs/上下文工程/审查与改进建议.md)。
- [docs/上下文工程/复核脚本.py](../../../docs/上下文工程/复核脚本.py)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。

## 任务内容

1. 定位顺序为明确任务/产物指代、当前任务、相关近期前文，再同会话原文检索。使用目标、对象、列表/结果版本和消息 ID，而非仅最近模块或模型自报高置信。
2. 把解析输出做成共同任务描述和可追溯锚点，供理解、检索、画像与模块共用；指代解析不产生新用户条件。
3. 先利用现有确定性结构锚点、中文关键词与检索；只有 40 的漏召回证据证明不足再考虑语义召回/重排。
4. 补回包含必要相邻轮次的原文，保留原值→纠正→撤销关系，既检查近期又检查旧区。部分命中不能掩盖其他必要条件缺失。
5. 多张论文/仓库列表的“第二个”会改变结果时绑定任务版本澄清；唯一列表直接续接，无须用户复述主题。未定位不能说用户从未讲过。

## 跨票接缝与责任

提供 12 的任务关系理解、13 的摘要定位、14 的原材料选择及 15/19 的任务查询。原始消息是权威，摘要只帮助定位。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 无引号“之前说好的预算是多少”、末尾约束后的“继续推荐”和近期唯一对象均正确回补。
- [x] 两张列表歧义能问必要对象，唯一对象直接续接，返回目标最新版本。
- [x] 恢复原文包含关键纠正关系，不复活撤销值，回补受统一预算和账户/会话权限控制。
- [x] 无法定位时给真实缺口，推测不得写入有效状态。
- [x] 记录采用消息/对象 ID 与未采用原因，诊断不含完整私人正文。

## 验证与交付证据

固定多轮消息和列表版本验证定位/歧义/失败，覆盖近期对象、同名对象、换话题和纠正邻接。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 执行与验收记录

### 2026-10-01：实现、两轴评审与修复

- 交付分支 `codex/11-reference-resolution-and-original-recovery`（工作树 `.worktrees/11-reference-resolution`，基点 main `f52c74c`）；本记录与实现同一提交。
- 合同：`src/bridges/contracts/references.py` 新增 `reference-v1`（`ReferenceStatus`/`AnchorKind`/`ReferenceAnchor`/`ReferenceTaskBrief`/`ReferenceClarification`/`ReferenceResolution`/`ReferenceTaskContext`）；编译审计记录携带 `reference_contract_version`。
- 解析器：`src/bridges/chat/reference_resolution.py` 实现确定性、只读定位：定位顺序＝明确任务/产物指代→当前任务→近期前文→同会话原文检索；结果列表按类型出现次序登记版本，序数指代绑定版本与对象 ID；实质歧义（多张同类列表）只问一个必要问题并绑定任务/预期版本；原值→纠正→撤销链补回相邻原文并标注不采用；无任务快照时用「继续/照之前」的确定性续接回退；未定位给短标签真缺口，不把「本轮未定位」写成用户从未讲过。未使用语义召回/重排（按票面留给 40 的漏召回证据决定）。
- 编译接缝：`compile_turn_context(..., task=ReferenceTaskContext | None)` 接入解析；补回受统一预算裁剪循环约束（每轮重算，降级进摘要后仍可补回；单轮上限沿用既有 `RECOVERED_MAX_MESSAGES=4`，触顶如实记缺口）；新增补回原文块（含纠正说明）、任务条件只读快照块、歧义规则与缺口规则；`CompiledTurnContext` 新增 `reference_status`/`ambiguous_reference`/`reference_anchor_ids`/`reference_adopted_object_ids`/`reference_rejected`/`reference_missing_count` 并写入审计记录（仅 ID/短标签/计数，无正文）。
- 任务接缝：`TaskService.current_reference_context(account_id, conversation_id)` 只读读取当前任务（含全部状态条件与版本来源消息，消费 08 的 `task-v1` 快照）；`ChatService` 与 `api/main.py` 注入该接缝（TaskService 构造顺序前移）。
- 持久化/迁移：本票不新增持久状态、不新增表/路由/迁移；仅编译记录新增审计字段，账户/会话/模式/附件与知识库分域未动。
- 两轴评审（标准＋规格并行子代理）发现与处置：①「照之前的条件」此前无任务时无法续接 → 将其归入续接词，按续接采用有效条件并补回来源；②仅按命中条件分组会让「引用被取代旧值」误报「该类别无有效值」→ 改为按类别读取任务全部条件，采用当前有效值并保留纠正说明；③合同版本未进编译记录 → 已补；④`RECOVERED_MAX_MESSAGES` 一度调为 6，属超出本票的行为变化 → 恢复为 4；⑤代词/结果消息的相邻来源采用逻辑去重并统一走 `_adopt_with_owner`；⑥`_MessageMatch.record` 补 `MessageRecord` 标注、缺口标签去重抽公共函数。未采纳项：条件类别多表合并、读取消息原始投影字典（`MessageRecord` 即原始投影字段，改判属 12/15 的消费者改造）。诊断与实现说明保留在代码 docstring/测试。
- 验证：新增 `tests/chat/test_improvement11_reference_resolution.py` 21 项（含无引号预算、末尾约束「继续推荐」、「照之前的条件」、近期对象不误报、两列表歧义与唯一列表最新版本、原值→纠正→撤销、引用被取代旧值采用当前值、部分命中真缺口、补回受预算控制、只读任务状态、审计不含正文、跨账户不可召回）；固定用例与结果均为确定性机制证据。
  - 定向回归：变化相关 10 个既有测试文件＋新增文件共 339 passed；`tests/contracts`、`tests/tasks`、`tests/ai` 全过。
  - 全量 `tests/chat`：分支 100 failed / 755 passed / 1 xfailed；main 同命令 100 failed / 734 passed / 1 xfailed；两侧失败集逐项一致，**0 新增失败**（passed 差额＝新增 21 项）。
  - `mypy src`：分支与 main 输出逐行一致（134 行既有错误），0 新增；`ruff check` 新文件全过（`api/main.py` 的既有 E402/I001 与 main 基线一致）。
  - 组合回归 `tests/lifecycle tests/runtime tests/closeout tests/observability`：分支与 main 的稳定失败集一致（各 13 项既有失败）；组合运行时偶发多 1 项且每次不同（`test_restore_*`），单目录/单测运行通过，判定为既有顺序型 flaky，非本票引入。
- 限制：仅证明确定性机制；真实模型的连续性收益、语义漏召回是否需语义检索按票面由 40 评测；无当前任务快照时澄清无法绑定任务版本（`task_id/expected_version` 为 null），有快照时绑定。
- 状态改 `ready-for-human` 等待人工验收；分支未推送、未合并。

### 2026-10-01：用户授权独立验收与合入

- 独立验收修复旧列表原文/对象未进入模型、新版缺项复活旧对象、同类独立列表歧义、无任务快照纠正邻接、无关否定误撤销、最后一篇量词/紧邻对象、重试轮次/任务快照与回补上限缺口；新增 14 项回归。详见[独立验收记录](../acceptance/11-reference-resolution-and-original-recovery.md)。
- 指代相关 35 项通过，确定性验收通过；本次用户已授权合并、推送与清理，不以此前待确认说明阻止执行。合入结果与全量对照见验收记录。
- 已以 `7ad8bd31` 无冲突合入最新 main，组合回归 331 passed 并成功推送 origin；本票工作树与本地分支已删除，失效 worktree 记录检查/清理完成。

