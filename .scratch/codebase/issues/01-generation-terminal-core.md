# 01 — 统一普通生成的完成与失败收尾

**What to build:** 普通聊天一轮正常完成或生成失败后，消息、生成运行和持久终态事件由同一 module 协调；用户刷新或重连仍看到同一结果，重复收尾不会产生第二次完成。

**Blocked by:** 无，可立即开始。

**Status:** ready-for-human

## 背景

现有 `finalize_message` 能集中部分消息投影，但后台执行器还需独立安排运行终态、错误事件和队列完成。小函数之外的时序约定仍属于调用者必须学习的 interface。此票先打通普通聊天的成功与失败路径，为后续停止、图异常和失联路径提供稳定基础。

## 实施范围

1. 核查普通聊天从提交消息、后台执行、流式输出到完成/失败的真实路径，列出消息状态、运行状态、事件种类、错误可重试性、模型运行锁和耗时字段的现行对应关系。
2. 定义生成终态 module 的职责与不变量。隐藏共同投影和跨对象提交知识；不把领取、续租、模型生成、事件订阅全部塞入该 module。
3. 接入普通聊天成功和普通生成异常两条完整路径。若其他路径暂时需要原入口，保留内部兼容委托，保证中间状态可运行。
4. 选择最小的原子提交或可恢复提交策略。涉及消息、运行和事件的失败不得由 caller 通过猜测顺序修复。队列确认只有在结果可被持久恢复后发生，调度本身仍由执行器负责。
5. 用共同 interface 验证结果，而非只断言调用了某个辅助函数。

## 验收标准

- [x] 普通成功轮次保留全部正文和真实模型运行锁，消息、运行及持久完成事件一致；刷新不创建新消息或重复模型调用。
      —— `test_success_round_keeps_content_lock_and_single_terminal_event`：正文 `完整正文`、`run_lock_id` 非空、`model_id` 与运行周期一致、运行 `done` 且 `duration_ms ≥ 1`、终态事件恰一条 `done`（事件载荷里的运行锁与消息一致）；订阅以 `done` 结束；GET 会话两次后消息仍是 2 条、适配器调用次数仍是 1。
- [x] 普通失败轮次保留已生成内容，稳定错误码、中文原因和可重试性与现有合同一致，订阅能结束。
      —— `test_failure_round_keeps_content_and_terminal_contract`：先出正文再抛 `RateLimitError`；消息 `error` 且正文 `已生成的正文`、`error_code=rate_limit`，运行 `failed` 且错误码/原因与消息逐字相同，终态事件恰一条 `error`（`rate_limit`／含「限流」／`retryable=True`），订阅结束。
- [x] 相同完成请求执行两次不改变已提交结果、不重复追加有效终态、不重复确认业务完成。
      —— `test_repeated_converge_replays_committed_result`：两次 `converge` 均 `replayed=True`（消息未改写、事件 seq 为 None、运行未重写），正文/运行锁/模型/耗时逐字段不变、事件 seq 序列不变、适配器调用仍为 1；`TaskQueue.complete` 自身幂等（`runtime/queue.py`）。
- [x] 对真实 SQLite 路径注入提交故障，验证不会暴露互相矛盾的终态；重试同一收尾操作能够恢复或保持原有已提交结果。
      —— 两个故障窗口各一条用例：`test_run_commit_fault_leaves_recoverable_state_and_replays`（真实执行器 `run_tick` 跑完整轮次，`finalize_generation_run` 注入 `StorageError`）：消息仍 `done` 且带运行锁、终态事件恰一条 `done`、运行仍 `running`（**没有被改写成失败**）、队列仍 `claimed`（结果不可恢复前不确认完成）；重放同一收尾补齐运行且不追加第二条事件。`test_event_commit_fault_leaves_recoverable_state_and_replays`：`append_terminal_generation_event` 注入故障后消息已终态、无终态事件、运行仍 `queued`；重放后恰补一条 `done` 事件并收敛运行，再次收尾为空操作。
- [x] 同一账户下不同运行及不同账户之间不能串写，模型锁和历史消息不因本次重构而改写。
      —— `test_converge_isolates_runs_and_accounts`：收尾 A 运行后，同账户另一会话的运行与另一账户的运行都仍是 `streaming`／`queued` 且无终态事件；`test_repeated_converge_replays_committed_result` 断言 `run_lock_id`/`model_id` 不变；`test_concurrent_terminal_append_keeps_single_terminal_event` 断言并发收尾只有一次事件追加、一次运行提交。
- [x] 原入口仍在用时保持兼容；相关聊天生成回归通过，没有要求同时完成 02 或 03 才能通过的中间提交。
      —— `turn.finalize_message`（回合编排与 `career_plan/service.py` 仍在用）签名只多了一个返回值；本票未迁移 `_reap_message`（03）、`_converge` 与图内 `converge_stopped`/`converge_error`（02），相关回归见记录。全量 251 失败与基线逐名相同（失败名单 md5 相等）。
- [x] 调用者无需独立决定消息/运行/终态事件的提交顺序；实现不是简单转发三个旧函数后仍把异常恢复留给 caller。
      —— 执行器尾段只剩一次 `converge` 调用（原 4 分支消息状态映射 + `_ensure_terminal_event` 已删除），调用者不供投影、不排序、不判重；`test_converge_refuses_to_guess_for_streaming_message` 钉住「没有兜底结果就拒绝发明终态」。

## 验证建议

复用断连重连、刷新不重复生成、终态订阅回放的现有测试。增加针对完成与失败的合同测试，并覆盖提交故障后的同操作重试。使用确定性模型 adapter 和真实临时 SQLite，不访问外网；在 agent 环境运行相关测试。

## 范围限制与交付

不修改对外事件名称、消息字段、错误语义或界面布局，不扩大为整个聊天重写。终态 module 只管理执行结果，不授予产物可信状态。交付时记录不变量、首批迁移入口和后续仍由 02、03 接管的入口清单，说明策略为何能在失败时恢复。

## Comments

初始任务来自架构候选，不预设存在已复现缺陷。

---

# 实现记录（2026-09-28）

分支 `codex/01-generation-terminal-core`（工作树 `.worktrees/01-generation-terminal-core`，分支点 `f04c79c`）。实现提交 `87ac420`，评审修正提交 `7d39c32`，记录提交见文末。

## 1. 现行路径核查（实施范围 1）

普通聊天从提交到收尾的真实对应关系（本票接入前）：

| 环节 | 消息状态 | 运行状态 | 事件种类 | 可重试性 | 运行锁 | 耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| 提交（`_send`） | `streaming` | `queued` | `started`（+`stage`/`node`） | — | 未写 | — |
| 后台执行（图 + `stream_turn`） | `streaming` → 三态 | `running` | `delta`/`stage`/`node` | — | 成功终态随消息同事务写 | 消息侧由回合编排测量 |
| 成功收尾（回合编排 `finalize_message(DONE, lock=…)`） | `done` | 仍 `running` | 回合编排 `yield done` → 执行器 `_persist_event` 落库 | — | `run_lock_id` | 回合编排测量 |
| 生成异常（`stream_turn` 的 `except` / 预算超支） | `error` + 稳定码 | 仍 `running` | 回合编排 `yield error` → 执行器落库 | `_RETRYABLE_CODES` | 无 | 同上 |
| 执行器尾段（旧） | 读消息状态分四支 | `done`/`stopped`/`failed` | 缺失时由 `_ensure_terminal_event` 补齐 | `error_is_retryable(码)` | — | 执行器测墙钟 |

结论：**消息才是唯一携带内容的记录，运行表只有状态与错误字段，事件是消息投影的快照**；旧尾段把「消息状态 → 运行状态 + 事件种类 + 事件载荷」这一映射又实现了一遍（同一规则在生产代码里有 4 份：尾段、`_ensure_terminal_event`、`_reap_message`、`_converge`），且判重依赖内存里的 `last_kind`（跨进程恢复即失效）。

## 2. module 的职责与不变量（实施范围 2）

新增 `src/bridges/chat/terminal.py`（`GenerationTerminal`），对外只有两个层次：

- `converge(account_id, run_id, message_id, *, fallback=None, run_duration_ms=None)`：一次调用确定消息、终态事件与运行终态，返回 `TerminalCommit`（区分「本次写入」与「已提交」，`replayed` 即幂等重放）；
- `done_payload` / `error_payload`：终态事件载荷的唯一装配处（运行期实时事件与收尾补齐共用同一形状）。

不变量（写在 module docstring 里，实现与测试都按它构造）：

1. **消息是唯一真相源**：结果（完成/失败/停止、错误码、中文原因、可重试性、耗时）从已提交消息派生，运行与事件绝不反向推导；已终态的消息不被改写（并发时以先提交者为准，靠 `finalize_message` 的 `streaming → 终态` 守卫 + 影响行数判定）。
2. **提交顺序固定：消息 → 终态事件 → 运行**。事件先于运行，因为订阅端点以运行终态判断结束（`api/chat.py::_subscribe_generation_events`），运行先终态会让最后一次回放读不到终态事件；运行放最后，因为它是三者中唯一可由消息重算的对象。唯一例外是消息已被删除的竞态：没有消息就没有可投影的终态，只收敛运行、不补发事件。
3. **每步都有持久守卫，因此部分提交可恢复**：消息仅 `streaming → 终态`、终态事件每个运行至多一条（**判重与插入在同一事务内**）、运行仅 `queued/running → 终态`。
4. **提交故障不产生矛盾终态**：失败只留下「尚未补齐」（运行未收敛），不会留下与已提交消息冲突的事件或运行状态。
5. 不接管领取/续租/模型生成/事件订阅（仍是执行器），不改写运行锁与历史消息。

未塞进 module 的东西：调度（`TaskQueue` 领取与 `complete`）、租约与心跳、模型调用、图节点职责、产物可信状态。

## 3. 首批迁移入口（实施范围 3）与策略（实施范围 4）

**本票已接入**

| 入口 | 迁移前 | 迁移后 |
| --- | --- | --- |
| `GenerationRunExecutor._run_turn` 尾段（普通完成、普通失败、停止、残留 `streaming` 兜底、消息缺失竞态） | 4 分支消息状态映射 + `_ensure_terminal_event` + `_finalize_run` | 一次 `converge`，兜底结果 `internal_error_outcome()`，运行耗时仍由执行器测量传入 |
| `_ensure_terminal_event`（约 65 行重复规则） | 自带「消息状态 → 事件种类/载荷/thinking」 | **删除**，由 `converge` 承担 |
| 终态事件载荷装配（`_error_event_payload`、`_persist_event` 的 done/error 分支） | 执行器内两处 | module 的 `done_payload`/`error_payload`；运行期实时事件与收尾补齐同一形状 |

**提交策略：可恢复的分步提交（不是跨对象事务）。** 三个对象各自有持久守卫且顺序固定，任何中断点留下的都是「尚未补齐」形态，重放同一 `converge` 补齐缺失部分且不改写已提交结果；因此不需要引入通用事务框架（PRD 共同约束）。队列确认仍在 `converge` 返回之后（`_queue.complete(claim)` 是尾段最后一步）：结果不可恢复前不会确认业务完成，`test_run_commit_fault_…` 用真实执行器断言故障后队列仍是 `claimed`。

**兼容入口保留**：`turn.finalize_message` 仍是被 `career_plan/service.py`、图、服务层使用的消息收尾入口，本次只让它返回影响行数（`int`），签名与语义不变。

## 4. 仍由 02、03 接管的入口（交付清单）

| 入口 | 位置 | 归属 | 现状 |
| --- | --- | --- | --- |
| 图内提前终止：停止与节点失败 | `graph.py::_GraphDeps.converge_stopped` / `converge_error` | 02 | 仍自行派生 thinking 与中文节点位置文案并 `emit_error` |
| 排队期停止的快速收敛（消息+事件+运行+队列完成） | `run_executor.py::_converge`（及只服务它的 `_finalize_run`） | 02 | 已复用 module 的 `STOPPED_CODE`/`STOPPED_MESSAGE` 常量，尚未改为 `converge` |
| 停止接口的超时兜底收敛 | `service.py::stop_generation` | 02 | 直接用 `finalize_message` |
| 失联收尸的消息收敛与事件补发 | `run_executor.py::_reap_message` | 03 | 仍是自己的消息收敛 + 事件补发；注意它与 `expire_overdue_runs`（先把运行改成 `failed`）组合时，若消息已是 `done` 会追加一条矛盾事件——本票未改（属 03 的恢复矩阵） |
| 读取路径的陈旧收敛 | `service.py::_reconcile_stale_message` | 03 | 直接用 `finalize_message` |

## 5. 验证命令与结果

在 conda `agent` 环境、工作树内运行（`pyproject.toml` 的 `pythonpath = ["src"]` 保证解析本工作树源码；basetemp 一律放仓外）。

| 命令 | 结果 |
| --- | --- |
| `pytest tests/chat/test_terminal_core.py -q` | **9 passed**（本票新增合同用例） |
| `pytest tests/chat tests/plugins -q`（分支） | **140 failed / 549 passed**（6:18） |
| 同上（基线＝主仓 `f04c79c`，同一命令同一跑法） | **140 failed / 541 passed**（5:52） |
| 两者失败名单双向 diff | **仅分支 0 条、仅基线 0 条，md5 相同**；Δ通过 +8 = 本票 8 条新用例（并发用例为评审修正后追加，见第 7 节） |
| `pytest -q`（全量，分支） | **251 failed / 3822 passed / 39 skipped / 0 error**（968 s） |
| `pytest -q`（全量，`f04c79c` 的 detached 工作树 `.worktrees/01-prefix-baseline`） | **252 failed / 3812 passed / 39 skipped / 0 error**（1216 s） |
| 两者失败名单双向 diff | **仅分支 0 条**；仅基线 1 条 `tests/closeout/test_arxiv_worker_reliability.py::test_handshake_timeout_maps_to_arxiv_handshake`（并行满负载下超时抖动，单独复跑 **1 passed**，见第 6 节） |
| 分支失败名单 vs 上轮存储基线 `.tmp/issue06/raw/merged.names` | **逐名完全一致**（`comm -3` 无输出，251 条） |
| `ruff check`（改动文件） | 干净；`src/bridges/chat/` 整包 10 条与基线相同（未新增） |
| `mypy src/bridges/chat/terminal.py src/bridges/chat/run_executor.py` | 本票两个文件 0 条（同命令下的 20 条全在既有文件 `graph.py`/`service.py`） |

账目：Δ通过 +10 = 本票 9 条新用例 + 基线那条抖动用例；Δ跳过 0（两棵树都没有 `apps/web/.next`，两条 `NEEDS_WEB_BUILD` 两侧同样跳过）；两侧 0 error。原始输出与失败名单：`.tmp/codebase-01/raw/`。

## 6. 全量回归的环境事实（本机，2026-09-28）

- 两次全量都在仓外 basetemp 上跑（`%TEMP%\bridges-t01\bt-full-branch` 与 `bt-full-*`），跑完已删（释放 3.7 GB）。
- 基线那条 `test_handshake_timeout_maps_to_arxiv_handshake` 属 closeout 进程类用例：满负载（三份全量/定向 pytest 并行）下握手超时抖动失败，单独复跑 3.08 s 通过。分支侧同一用例在全量里通过，因此它不进任何一侧的逻辑差值。
- 本轮跑全量期间 C 盘一度被临时目录打满（多会话并行 basetemp），已清理自己的部分；结果本身在打满之前各跑完一次、摘要行唯一、进度字符与摘要一致。

## 7. 两轴评审与修正

`/code-review` 两轴（Standards / Spec）各跑一个只读子代理，评审对象为 `f04c79c...87ac420`。

| 发现 | 轴 | 处置 |
| --- | --- | --- |
| 终态事件判重是「先 SELECT 再 INSERT」，两个执行器竞争时可各写一条，与 module 声称的「每个运行至多一条」不符 | 标准轴 | **已修**：仓库新增 `append_terminal_generation_event`（判重与插入同一 `BEGIN IMMEDIATE` 事务），`append_generation_event` 与它共用「已持有事务边界」的插入实现；新增 4 线程并发用例 |
| 新引入的停止常量与执行器 `_converge` 的硬编码字面量重复 | 标准轴 | **已修**：`_converge` 复用 `STOPPED_CODE`/`STOPPED_MESSAGE` |
| `_mode_of` 重造 `ChatMode.COMPANION` 兜底 | 标准轴 | **已修**：复用既有 `CHAT_MODE` |
| 新默认码 `generation_failed` 未映射且不可重试，等于改了错误语义 | 规格轴 | **已修**：删掉该常量，缺错误码时统一按 `internal_error` 派生（有中文映射、可重试，与新兜底路径同合同；该分支当前不可达） |
| 不变量声称的顺序有一个例外（消息已删除）未写明 | 规格轴 | **已修**：不变量 2 写明例外；不变量 3 写明判重与插入同事务 |
| 迁移入口与残余清单未落在交付物里 | 规格轴 | **已修**：见本文档第 3、4 节 |
| `_reap_message`／`_converge` 仍各自拼装跨对象终态（两份规则） | 标准轴 | **不采纳（本票）**：正是 02/03 的迁移范围，工单明确要求逐票迁移；已登记在第 4 节，并注明 `_reap_message` 与 `expire_overdue_runs` 组合时那条矛盾事件 |
| 执行器尾段仍按消息状态选中文摘要标签 | 标准轴（judgement call） | **不采纳**：面向运维的中文摘要属于调用点表现层，放进 `TerminalOutcome` 会把文案耦合进领域结果 |
| `_finalize_run` 在 module 与执行器各有一份 | 标准轴 | **不采纳（本票）**：执行器那份只服务 `_converge`，随 02 迁移一并删除 |

## 8. 提交版本

- 实现：`87ac420`（module + 仓库判重/插入 + 执行器尾段迁移 + 9 条合同用例）
- 评审修正：`7d39c32`（判重与插入同事务、常量复用、默认码、文档）
- 记录：`5d91576`（工单验收勾选与实现记录）

## 9. 未完成与残余风险

- **并发收尾的终态事件已做到至多一条，但「两个执行器同时收尾」的端到端竞争取证仍归 03**（本票只覆盖同一运行的 4 线程并发补齐 + 既有的双执行器领取用例）。
- **`_reap_message` 与 `expire_overdue_runs` 的组合缺口**（消息已 `done` 而运行被收尸成 `failed` 时会追加一条矛盾事件）在本票范围外登记，03 迁移时需按「已提交运行终态优先」裁决。
- 图内 `converge_stopped`/`converge_error` 仍各派生 thinking 与节点文案：终态 module 与它们的文案目前一致但不同源，02 迁移时应收敛为一处。
- 本票未做前端验证（无界面改动），未跑 vitest/tsc（无前端改动）。

## 10. 合并、合并后验证与清理实证（2026-09-28）

- **合并**：`git merge --no-ff codex/01-generation-terminal-core` → `2276122`，零冲突（ort 策略，6 文件 +1094/−186）。合并树 == 分支树 `f8ed5a10a5f77933289bb12fb15a34883ef5d62a`；`git rev-list --count main..branch` = 0；合并结果与分支顶端 `git diff --stat` 为空（无契约/生成物文件，与「零契约改动」一致）。
- **推送**：`f10b9ad..2276122  main -> main`，main == origin/main `2276122`（与合并同一次推送；`f10b9ad` 之后本地原已领先的 `f04c79c` 一并推送）。本票分支从未推送（`git branch -r --list '*01-generation-terminal-core*'` 为空）。
- **合并后定点复跑**（主仓 main，`PYTHONPATH` 清空、仓外 basetemp、`pytest tests/chat tests/plugins -q`）：**140 失败 / 550 通过**（330.13 s）；失败名单去 CRLF 归一化后与分支留档名单逐字节相同（md5 `6575899696db2c25838bf6abfd67f221`）；`--collect-only` 两侧各 690 条且用例 ID 集合相同。（分支那次报 140/549，差的 1 条是运行计数噪声，失败集合无差异。）
- **本票新用例在主仓**：`tests/chat/test_terminal_core.py` **9 passed**（11.36 s）。
- **静态检查（主仓）**：ruff 本票 5 文件 2 条（`N818` `repository.py:152`、`E501` `repository.py:1478`），与 `f04c79c` 文件副本逐条同源（仅行号 1474→1478 位移）；mypy 本票文件 1 条 `turn.py:3921`（`status_map.get(...)` 传 `str | None`），位于未改动的既有代码（本票在 `turn.py` 只改 1901 行附近的 `finalize_message` 返回类型）。
- **清理**：工作树 `.worktrees/01-generation-terminal-core` 普通 `git worktree remove` 一次成功（本轮无常驻受限 ACL 残留）；本地分支 `codex/01-generation-terminal-core` 已删（was `5d91576`）；`git worktree prune --dry-run` 无输出（无失效记录），`.git/worktrees/` 仅余并行会话的 `04-prefix-baseline`、`04-profile-extraction-commit` 两条。
- **留档**：主仓 `.tmp/codebase-01/raw/`（分支与基线全量日志、两套失败名单、合并后定点日志）。
