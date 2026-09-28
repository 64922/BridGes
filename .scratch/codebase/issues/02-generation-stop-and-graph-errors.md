# 02 — 接入图异常与用户停止

**What to build:** 日常父图失败、排队时停止和流式生成中停止都遵循共同终态规则；用户始终保留已收到的内容，停止与完成竞争不会把同一轮显示成互相矛盾的结果。

**Blocked by:** 01 — 统一普通生成的完成与失败收尾。

**Status:** ready-for-human

## 背景

日常父图的 `converge_stopped`、`converge_error` 自行拼装消息及思考摘要，执行器 `_converge` 又安排消息收尾、错误事件、运行状态和队列完成。它们需要遵守同一组规则，但目前知识分散。此票迁移这些真实退出入口，不改动图的业务节点职责。

## 实施范围

1. 核对排队停止、流式停止、图校验失败、图执行异常分别如何呈现，保留已有稳定错误码及停止投影。
2. 让日常父图和执行器的停止路径进入 01 的共同终态 module。必要的模式差异作为已明确的输入或内部策略处理，不让调用者继续复制 thinking、错误或事件构造规则。
3. 保留停止信号传递、心跳与租约职责；共同 module 决定可提交结果，执行器仍承担调度。
4. 明确条件更新的竞争规则：已获准提交的终态不得被迟到完成、迟到异常或重复停止覆盖；不能只在内存中加标志而绕过持久保护。
5. 移除本票迁移后形成的重复终态构造，但保留仍被其他路径使用的兼容入口。

## 验收标准

- [x] 排队时停止不发起模型调用，消息与运行进入正确停止终态，订阅按现行事件合同结束。
      —— `test_queued_stop_never_calls_model_and_converges_shared_terminal`：`request_generation_stop` 后 `drive` 一次收敛；适配器 `stream_calls == 0`；消息 `stopped` 且无错误码、无模块建议（不额外推进业务进度）；运行 `stopped` 无错误码；持久终态事件恰一条 `error`（`stopped`／「生成已停止。」／`retryable=True`）；队列行 `completed`；订阅回放以 `error(stopped)` 结束且无 `done`。
- [x] 流式停止保留已生成内容，不再接受迟到增量改写终态正文；重复停止不新增有效终态事件。
      —— `test_streaming_stop_keeps_content_and_rejects_late_writes`：闸门放行首批正文后经真实停止接口停止；正文恰为「块块」（仅停止前已收到的部分）；迟到闸门放行后正文与事件数不变；迟到完成走回合编排同一个 `finalize_message(DONE)`，持久守卫拒绝（0 行），消息/运行/事件保持停止终态；重复停止幂等（`test_completion_first_…` 与 `test_stop_generation_finalizes_stopped_and_keeps_content` 既有断言继续通过）。
- [x] 日常父图校验/执行失败的中文原因、可重试性和思考摘要保持现行含义，刷新后与即时结果一致。
      —— 既有 `test_module_dispatch_failure_marks_location_and_retry` 继续通过（节点位置中文 + 稳定码 + 重试办法）；新增 `test_graph_unknown_exception_converges_with_location_and_contract`：消息 `error_message`＝「在「编译上下文」步骤失败：…请重试。可点击重试。」、`error_code=internal_error`、思考摘要 quality＝「生成过程出现内部错误，请重试。」；运行 `failed` 且错误码/原因与消息一致、`current_node` 留位、`duration_ms ≥ 1`；事件载荷中文原因走统一派生规则（映射表文案优先）；GET 刷新与即时结果逐字段一致。
- [x] 用可控同步点分别模拟“停止先提交”和“完成先提交”，消息、运行和终态事件始终指向同一获准结果。
      —— 停止先提交：`test_streaming_stop_keeps_content_and_rejects_late_writes`（闸门 + 真实执行器线程控制顺序，迟到完成经真实收尾函数被持久守卫拒绝）；完成先提交：`test_completion_first_survives_late_stop_and_late_exception`（真实驱动到 `done` 后迟到停止幂等返回 `done`、`updated_at` 不变、无新增事件）。三个观察面（消息查询、运行查询、`list_generation_events` 回放）逐面断言。
- [x] 模拟终态后的迟到异常，验证不会把完成改为失败或再追加矛盾事件。
      —— `test_completion_first_survives_late_stop_and_late_exception`：`persist_result` 节点内的确定性同步点抛 `RuntimeError`（真实图入口未知异常路径）→ 运行仍 `done`、无错误码，持久事件恰一条 `done`、无 `error`，订阅回放不含矛盾终态。
- [x] 学习模式、显式工作模块及历史消息中现存的停止/错误投影不受公共收尾逻辑变更破坏；失败或停止不额外推进业务进度。
      —— 全量失败名单与基线双向 diff 为空（学习模式、六个显式模块的停止/错误投影测试全部原样通过）；新增 `test_stop_endpoint_fallback_cancels_inflight_search_projection` 钉住停止取消搜索投影的统一策略；`test_queued_stop_…` 断言停止后无模块建议。
- [x] 父图和执行器不再各自重复安排终态跨对象写入，01 的完成与失败回归继续通过。
      —— 图边界 `converge_stopped`/`converge_error`、执行器排队停止、停止接口兜底各为一次 `terminal.converge` 调用；执行器 `_converge`/`_finalize_run` 与图内 `emit_error`/`_conversation_mode` 已删除；`tests/chat/test_terminal_core.py` 9 条全过；全量 01 回归在名单内逐名一致。

## 验证建议

通过真实图入口及停止请求入口验证，复用“停止保留内容”的现有场景。竞争测试使用事件/屏障控制顺序，不依赖长时间睡眠或概率性线程竞争。检查消息查询、运行查询与持久事件三种观察面，避免仅断言内存状态。

## 范围限制与交付

不调整六个显式模块的路由，不创建通用模块注册框架，不合并工作流运行状态与产物可信状态。交付应列出已迁移入口、竞争裁决规则以及仍留给 03 的恢复路径。

## Comments

停止事件若沿用现有 error 传输种类，应保留既有合同，不为名称整齐而新增前端状态。

---

# 实现记录（2026-09-28）

分支 `codex/02-generation-stop-and-graph-errors`（工作树 `.worktrees/02-generation-stop-and-graph-errors`，分支点 main `7c5d04b`）。实现提交 `53401d5`，评审修正提交 `4903287`，记录提交见文末。

## 1. 四类退出入口的现行呈现核查（实施范围 1）

| 入口 | 迁移前呈现 |
| --- | --- |
| 排队期停止（`run_executor._converge`） | 消息 `stopped`（无错误码，thinking＝停止结论），自拼 stopped 终态事件（`append_generation_event`，无判重），运行 `stopped`，队列完成 |
| 流式停止（图边界 `converge_stopped`） | 只提交消息 `stopped`（thinking 自拼）；终态事件与运行留给执行器尾段 converge |
| 图校验/执行失败（`converge_error`） | 只提交消息 `error`（节点位置中文文案自拼 + `failed_thinking` 自拼），再 `emit_error` 实时事件；运行留给尾段 |
| 停止接口超时兜底（`service.stop_generation`） | 只提交消息 `stopped`（含搜索/教学卡片取消投影），事件与运行不补——无执行器时订阅端会悬挂 |

结论：四个入口各自复制「thinking 派生 + 消息终态提交」，事件与运行知识又散在执行器尾段；错误码合同（`stopped`/`internal_error`/模块码）、中文原因派生（映射表优先）与可重试性（码表裁决）保持不变。

## 2. 迁移与策略（实施范围 2–5）

四个入口全部改为一次 `GenerationTerminal.converge` 调用（实例由 `ChatService.terminal` 持有，图/执行器/接口共用）：

| 入口 | 迁移后 |
| --- | --- |
| `graph.py::converge_stopped` | `converge(fallback=stopped_outcome(), run_duration_ms=图侧墙钟)` |
| `graph.py::converge_error` | 节点位置中文文案作为 `TerminalOutcome.error_message` 输入，其余 module 派生 |
| `run_executor._run_turn` 排队停止分支 | `converge(fallback=stopped_outcome())`，随后队列完成 |
| `service.stop_generation` 超时兜底 | `converge(run_id=…或 None, fallback=stopped_outcome(duration_ms=…))` |

**已删除的重复终态构造**：执行器 `_converge`、`_finalize_run`（只服务前者），图内 `emit_error`、`_conversation_mode`、`message_streaming`，及 service/graph/run_executor 中随之孤儿化的 thinking/取消投影导入。**保留的兼容入口**：`turn.finalize_message`（回合编排与 `_reconcile_stale_message` 仍用）、`run_executor._append_error_event`（`_reap_message` 用，归 03）、`_persist_event`（游标事件通道，事件载荷已委托 module）。

**module 新增的两个内部策略/输入**（模式差异的归宿）：
1. **停止不是错误**：`stopped_outcome()` 工厂（含可选 `duration_ms`）；停止终态的消息不写错误码/原因，终态事件沿用 `error` 种类 + 稳定码 `stopped`（合同不变，无新增前端状态）；停止提交统一把已触发的公网/arXiv 搜索与教学卡片收敛为取消态（从消息当前投影派生）。
2. **终态事件中文原因统一为一条派生规则**：`user_facing_error(code, outcome.error_message)`——映射表文案优先，收尾结果携带的原因作未映射码回退，与运行期实时事件同一条规则。

**竞争裁决规则（实施范围 4）**：全部落在持久层，不用内存标志——消息仅 `streaming → 终态`（`finalize_message` 影响行数）、终态事件每运行至多一条（判重与插入同事务）、运行仅 `queued/running → 终态`。先获准提交者胜：迟到完成（真实 `finalize_message(DONE)` 被拒）、迟到异常（module 从已终态消息派生，忽略兜底）、重复停止（幂等重放）都不改写结果、不追加矛盾事件。

**调度职责不变（实施范围 3）**：`TaskQueue` 领取/完成、租约与心跳、停止信号传递、看门狗均未改动；module 只决定可提交结果。

## 3. 一个实现中发现的结构问题：终态事件必须保持为最后一个事件

验收要求的「订阅以终态收尾」依赖事件顺序不变式：**终态事件之后不得再有任何事件**。首轮实现后既有用例 `test_stop_terminates_at_cancellable_node_and_replays_to_final_state` 失败暴露了它：停止接口兜底把终态事件提前落库后，图还在展开，`invoke completed`/`verify_output started` 等 node 事件落在终态事件之后，部分游标重放不再以终态收尾（旧实现因兜底只写消息、事件由尾段最后补齐而侥幸成立）。

修法在 `_node` 包装器（graph.py）：停止检查从「`stop_requested()` 且 `message_streaming()`」加强为「`stop_requested()` 即停」，并在节点体返回后复查——请求过停止就不再发出节点进度、不再进入后续节点。这使终态事件**结构性地**保持为最后一个事件，同时让竞争裁决更严格：停止与完成竞争时，先在消息层获准提交的一方胜出，输家经 module 幂等重放收敛到同一结果（完成先提交时运行/事件保持 `done`，停止先提交时保持 `stopped`）。

## 4. 仍留给 03 的恢复路径（交付清单）

| 入口 | 位置 | 现状 |
| --- | --- | --- |
| 失联收尸的消息收敛与事件补发 | `run_executor._reap_message` | 仍自拼 thinking/事件；与 `expire_overdue_runs` 组合的矛盾事件缺口按 01 记录遗留 |
| 读取路径的陈旧收敛 | `service._reconcile_stale_message`（含 `get_conversation` 四分支调用点） | 仍自拼 thinking/终态，迁移时按「已提交运行终态优先」裁决 |

## 5. 验证命令与结果

conda `agent` 环境、工作树内运行（`pyproject.toml` 的 `pythonpath=["src"]`；basetemp 一律仓外；`unset SSL_CERT_FILE`）。

| 命令 | 结果 |
| --- | --- |
| `pytest tests/chat/test_terminal_stop_and_graph_errors.py -q` | **7 passed**（本票新增） |
| `pytest tests/chat/test_terminal_core.py -q` | **9 passed**（01 合同继续通过） |
| `pytest tests/chat tests/plugins -q`（迁移后定点） | **140 failed / 550 passed**，与 01 合并后基线同数 |
| `pytest -q` 全量（分支，`4903287`） | **249 failed / 3840 passed / 39 skipped / 3 deselected / 0 error**（1099 s） |
| `pytest -q` 全量（基线＝分支点 `7c5d04b` detached 工作树 `.worktrees/02-prefix-baseline`，同一命令同一跑法） | **249 failed / 3833 passed / 39 skipped / 3 deselected / 0 error**（1027 s） |
| 两侧失败名单双向 diff | **仅分支 0 条、仅基线 0 条，名单 md5 相同**（`2876f3744ef24d143cf820727f201ed0`，249 条） |
| 账目 | Δ通过 +7 = 本票 7 条新用例；Δ失败/Δ跳过 = 0；Δ收集 +7 |
| `ruff check`（本票 4 源文件 + 新测试文件） | 干净；`src/bridges/chat/` 整包 9 条，比分支点 10 条少 1 条既有 I001（导入整理顺带消除），未新增 |
| `mypy`（本票 4 源文件） | 20 条全部在既有文件/既有行（与 01 记录同数同源），本票改动行 0 条 |

**跑法备注**：`tests/integration/test_runtime_smoke.py` 的三条 `test_start_fails_*` 在本机满负载（与并行票全量同时段）下会挂死（首次全量卡死 28 分钟后放弃），两侧对称 `--deselect` 后重跑；其中两条在历史全量里本就是快速失败（计入 251 基线名单）、一条通过，deselect 对双向 diff 与 Δ账目无影响。留档：主仓 `.tmp/codebase-02/raw/`（双侧全量日志、规范化失败名单）。

## 6. 两轴评审与修正

`/code-review` 两轴（Standards / Spec）各一个只读子代理，评审对象 `7c5d04b...53401d5`。

| 发现 | 轴 | 处置 |
| --- | --- | --- |
| 停止接口兜底传 `run_id=""` 哨兵，契约未写明 | Standards | **已修**：`converge` 的 `run_id` 显式允许 `None`（消息无关联运行时只收敛消息），写入 docstring 契约（`4903287`） |
| 兜底手拼 `TerminalOutcome(STOPPED,…)`，绕开既有工厂 | Standards | **已修**：`stopped_outcome()` 增加 `duration_ms` 参数，兜底复用工厂 |
| 工单 28 行「失败或停止不额外推进业务进度」无直接断言 | Spec | **已修**：排队停止用例改用带论文请求特征的正文，断言停止后无模块建议 |
| 「留给 03 的恢复路径」未列交付清单 | Spec | **已修**：见本文第 4 节 |
| 排队停止不传 `run_duration_ms` 与图边界不一致 | Standards | **不采纳**：与现行口径一致（排队期从未运行无耗时可测，图边界沿用执行器墙钟） |
| `max(1, int((monotonic()-started)*1000))` 三处重复 | Standards | **不采纳**：调用方各自测墙钟是 module 的既有设计（module 不拥有时钟），表达式是仓库既有惯用法，为两处调用提公共助手得不偿失 |
| `_reconcile_stale_message` 四分支仍自拼 thinking | Standards | **不采纳（本票）**：正是 03 的迁移范围，已在第 4 节登记 |
| 包装器停止检查加强（删 `message_streaming` 条件 + 体后复查）属工单未字面要求的行为变化 | Spec | **保留（有意）**：见第 3 节，终态事件最后性是「订阅按现行事件合同结束」的前提；竞争语义与持久守卫裁决一致 |
| service.py import 整块重排 | Spec | 工具（ruff I001）自动修复所致，机械改动 |
| 停止兜底 thinking 从「保留消息已有」改为「按当前模式重算」 | Spec | **不采纳**：流式期间消息无 thinking（`update_message_thinking` 无生产调用方），两口径当前等价；「按会话模式重算」与图/执行器停止路径同一策略（且修正了旧兜底对学习模式消息写陪伴步骤的错位）；module 若接受 thinking 覆盖参数会把 thinking 构造规则漏回调用方，违背工单 16 行 |

## 7. 提交版本

- 实现：`53401d5`（module 契约/策略收编 + 四入口迁移 + 包装器保序 + 7 条新用例）
- 评审修正：`4903287`（`run_id=None` 契约、`stopped_outcome(duration_ms)`、业务进度断言）
- 记录：见文末

## 8. 未完成与残余风险

- 失联收尸（`_reap_message`）与读取路径陈旧收敛（`_reconcile_stale_message`）的迁移、以及「已 `done` 消息 + 运行被收尸成 `failed`」的矛盾事件缺口，按交付清单留给 03。
- `stop_requested` 一旦写入不会清除；租约恢复的尝试进入包装器即抛 `DailyGraphStop` 并经 module 收敛为停止（对「停止请求后运行死亡再恢复」是正确语义，但若产品将来要「撤销停止」需先改运行表语义）。
- 包装器加强后，完成与停止在 `persist_result` 边界竞争时运行表的 `model_lock_id` 可能不写（消息上的运行锁不受影响，仅运行表关联字段缺失，观测口径损失）。
- 图边界停止的运行耗时改为图侧墙钟（与执行器墙钟同源、相差微秒级）；排队期停止运行耗时保持为空——两口径与迁移前一致。
