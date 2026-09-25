# 02 — 可恢复的对话运行

**What to build:** 用户在日常聊天中可看到真实执行进度，停止或重试失败轮次，并在刷新或断线后恢复运行状态。

**Blocked by:** 01 — 日常普通对话

**Status:** ready-for-agent

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

**编排合同：** 日常 LangGraph 父图按 `validate_turn → compile_context → select_explicit_module → invoke_subgraph_or_chat → verify_output → persist_result` 推进。本票让无模块普通聊天走通整条链；`03` 扩展上下文编译，`11` 起逐个接入子图。派发只读请求中经服务端校验的逐消息 `module_id`，模型不得从正文改写。运行状态至少关联账户、会话、运行 ID、图版本、当前节点、等待原因、模型锁与 SSE 游标；检查点映射现有消息事务。澄清和逐题等待由后续切片持久化，不能靠跨请求内存协程。

- [x] 普通对话通过可持久化的运行图执行，节点进度只对应实际开始或完成的步骤，失败明确标出位置与重试办法。
- [x] 同一请求重试复用稳定运行标识，不重复写用户消息、助手消息或附件；同一会话同时只允许一个可写运行。
- [x] 用户停止后在可取消节点终止；刷新或 SSE 断线后可凭游标重放事件并恢复最终状态。
- [x] 检查点、运行与事件均按账户和会话隔离；跨账户恢复请求被拒绝，旧消息仍可阅读。

## Comments

- 实现：新增 `src/bridges/chat/graph.py`（LangGraph 日常父图，六节点与 `daily-parent-v1` 图版本）与 `src/bridges/chat/checkpoints.py`（`RepositoryCheckpointSaver`，经 `BridgesDatabase.scoped` 强制账户隔离）；`run_executor` 改经 `run_graph_turn` 驱动，事件实时持久化，SSE 游标重放沿用既有 `generation_events` 单调 `seq`。
- 契约：`ChatStreamEventKind.NODE` 节点进度事件（started/completed + 节点名与耗时）；`ChatRunView` 增加 `graph_version/current_node/wait_reason/model_lock_id`；发送/重试支持 `idempotency_key`（generation_runs 部分唯一索引兜底幂等重放）；逐消息 `module_id` 由服务端校验，未知值在 `select_explicit_module` 节点以 `module_not_available` 失败并标出节点位置。
- 测试：`tests/chat/test_v2_02_checkpoints.py`（4 用例）与 `tests/chat/test_v2_02_resumable_runs.py`（9 用例）覆盖四项验收；前端展示节点进度中文标签并在发送/重试时携带幂等键。
- 数据库：迁移 49（generation_runs 新列与幂等索引、messages.module_id、graph_checkpoints 与 graph_checkpoint_writes 表）。
- Code review（两轴）结论与修复：检查点恢复已接线——`run_daily_turn` 在运行谱系已有检查点时以 `invoke(None)` 从上次提交的节点边界续跑（已完成节点不重跑、不重复调模型、游标事件不重复；中断节点重跑为 at-least-once，由消息事务与终态守卫收敛），新增 `test_lease_recovery_resumes_from_checkpoint_lineage` 覆盖；修正 `emit_error` 死参数。已知边界：`DailyTurnState` 的 `mode`/`module_dispatch` 为 Issue 03/11 预留写入位；测试侧 issue06_latency_budget 三个用例（stage 顺序/并行搜索/性能摘要）为 main 基线既有失败（fake 搜索注入与正文英文不匹配），与本票无关。
