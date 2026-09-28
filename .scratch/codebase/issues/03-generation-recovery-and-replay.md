# 03 — 接入失联恢复并收敛所有终态入口

**What to build:** 后台执行器失联或终态提交被中断后，用户重连仍能获得唯一、一致、可结束的结果；恢复已有终态不重新调用模型，达到既有恢复上限后按原规则收敛为可重试失败。

**Blocked by:** 02 — 接入图异常与用户停止。

**Status:** ready-for-human

## 背景

执行器 `_reap_message`、`_ensure_terminal_event` 和 `_finalize_run` 分别掌握失联消息、事件补齐与运行完成规则。前两票迁移正常退出后，本票完成异常恢复这条端到端路径，并使内部终态规则真正只有一个归属。

## 实施范围

1. 将失联回收、残留 streaming 兜底及终态事件补齐迁入共同 module，保留现有领取、租约时长、最大尝试次数和队列语义。
2. 设计与 01 提交策略匹配的恢复验证：若所有终态写入在同一事务内，证明中途失败完整回滚；若允许分步提交，证明残留状态可被幂等修复。
3. 检查旧持久记录可能留下的消息/运行/事件不完整组合，支持现有可恢复形态，不通过批量删除旧运行解决问题。
4. 逐一清点生成完成、图异常、停止和失联调用入口，删除本轮重构产生的内部兼容转发或重复规则。历史外部入口仍遵守迁移门，不因引用变少直接删除。
5. 更新职责说明，确保后续维护者从一个 interface 找到终态不变量与恢复测试。

## 验收标准

- [x] 执行器失联后仍按既有上限领取恢复；达到上限后，消息与运行收敛为一致的可重试失败，并保留部分正文。
- [x] 覆盖消息终态写入前后、事件持久化前后、运行提交及队列确认附近的实际故障窗口；对不可观察的同事务窗口验证回滚即可。
- [x] 恢复已有终态不会再次调用模型、创建助手消息或重复增加有效完成事件。
- [x] 两个执行器竞争领取或修复同一运行时，不产生冲突终态；恢复尝试不破坏在途模型锁。
- [x] 从不同已保存游标重新订阅均能正确回放并结束，游标单调，停止/失败/完成的终态各自保持原传输合同。
- [x] 运行或会话已被合法删除时，迟到恢复不复活数据、不跨账户读取，也不产生永久悬挂队列项。
- [x] 所有当前生成终态入口已接入共同规则；剩余例外有可核验的历史合同理由，不能只留下“以后迁移”的正常生产路径。
- [x] 01、02 及持久生成、停止、执行器竞争、断连重连回归通过。

## 验证建议

在真实临时 SQLite 中执行故障注入，关闭后重新建立仓库/执行器再验证，避免只证明同一内存实例可恢复。优先复用已有失联恢复与终态回放测试；新测试应覆盖原先没有证明的跨对象窗口，不复制辅助函数的实现步骤。

## 范围限制与交付

不引入新的后台平台或默认重试次数，不承诺模型外部副作用恰好执行一次。该票保证的是持久终态的一致性和恢复幂等。交付包含入口收口清单、故障矩阵、回归结果及恢复策略说明。

## Comments

现有失联测试证明部分恢复行为；本票要求补足跨对象故障证据，不将现有实现描述为全面无恢复能力。

# 实现记录（2026-09-28）

**Status:** ready-for-human

## 1. 入口收口清单（实施范围 4 / 验收标准 7）

终态规则的单一归属是 `src/bridges/chat/terminal.py`。逐入口核查结果（括号内为该入口的调用点）：

| 入口 | 位置 | 接入方式 | 归类 |
| --- | --- | --- | --- |
| 正常完成/失败收尾（执行器跑完一轮） | `run_executor.py:284` `converge` | 固定顺序：消息 → 终态事件 → 运行 | 01 已接入 |
| 领取后判定「结果已提交」 | `run_executor.py:239` `recover_committed_result` | 消息已终态 → 只补齐事件与运行，不再调用模型 | 本票新增 |
| 执行器失联收尸 | `run_executor.py:128` `reap_lost_run` | 选取与终态写入分离（`repository.list_overdue_runs` 纯选取） | 本票迁移 |
| 排队期停止 / 图边界停止 | `run_executor.py:250`、`service.py:1568` | `converge` + `stopped_outcome` | 02 已接入 |
| 图异常（含未知异常兜底） | `graph.py:286/306` `converge_error` | `converge` + 节点定位文案 | 02 已接入 |
| 读取路径残留 streaming | `service.py:697` `reconcile_stale_message` | 有运行 → 从运行派生；无运行（迁移前遗留）→ 断流兜底 | 本票迁移 |
| 运行期实时事件载荷 | `run_executor.py:321/328` `runtime_error_payload`/`done_payload` | 与补齐通道同一套码/文案/可重试规则 | 本票收口 |
| 终态事件判重（每运行至多一条） | `repository.py:1803` `append_generation_event` | 判重与插入同一事务；`append_terminal_generation_event` 删除 | 本票收口 |
| 图校验节点重抛已终态消息 | `graph.py:744` | 默认码由字面量 `generation_failed` 改引 `INTERNAL_ERROR_CODE` | 本票收口 |

**剩余例外与可核验理由**：

1. **消息的主写入方**——`chat/turn.py`（44 处 `finalize_message`，含 1 处包装调用）与各模块工作流（`study/service.py:870-890` 及 `commute`/`career_plan`/`github`/`paper`/`resources`/`tieba` 的 service）在自己的回合里提交消息终态并发实时事件。它们不是第二套终态规则：①跨对象一致性仍由 module 从**消息**派生——执行器在回合返回后 `converge`（`run_executor.py:284`）；②每运行至多一条终态事件由存储入口 `append_generation_event` 保证，迟到收尾写不进第二条（本票新增的守卫，正是靠它抓到僵尸回合重复终态）；③「消息状态 → 运行状态」映射全仓只出现在 `terminal.py:186-189`（`grep ChatRunStatus.(FAILED|DONE|STOPPED)` 无第二处）。
2. **`repository.py:34` 的 `_TERMINAL_EVENT_KINDS` 字面量表**——存储层守卫不反向依赖领域 module（依赖方向是 `chat.terminal → chat.repository`），故留在写入入口就地判重，不抽到 terminal.py。

## 2. 故障矩阵（验收标准 2）

全部为真实临时 SQLite + 真实服务/执行器，跨进程恢复用「同一数据库文件上重建仓库/服务/执行器」表示（不是同一内存实例）；用例集中在 `tests/chat/test_terminal_recovery_and_replay.py`。

| 故障窗口 | 注入方式 | 期望与断言 | 用例 |
| --- | --- | --- | --- |
| 消息终态已提交，事件未持久化、运行未提交 | 事件通道在收尾前中断 + 领取租约过期 | 重启后按消息补齐事件与运行；不再调用模型；模型锁保留 | `test_message_commit_window_recovery_repairs_without_second_model_call` |
| 运行终态已提交，队列行未确认 | 在 `queue.complete` 处抛错（执行器失联） | 重领该行只确认：不调模型、不追加第二条终态、事件流与投影逐字段不变 | `test_queue_confirmation_window_keeps_result_and_confirms_row` |
| 收尸撞上「消息其实已完成」 | 对已完成运行直接收尸 | 保持 `done`、不追加矛盾 `error`、队列行被确认 | `test_reap_of_committed_result_keeps_done_without_conflicting_event` |
| 两次尝试都失联（恢复预算耗尽） | 两次租约过期 + 正文分块阻塞 | 按既有上限领取恢复；达上限收敛为可重试失败并保留部分正文；迟到收尾不改写终态 | `test_lost_worker_recovers_within_cap_then_converges_with_partial_content` |
| 收尸写入故障（存储不可写） | `finalize_generation_run` 抛 `StorageError` | 执行器不退出、运行留在待收敛形态（仍可被再收尸）；下一轮即收敛 | `test_reap_write_fault_keeps_loop_alive_and_repairs_on_next_tick` |
| 竞争执行器遇到在飞运行 | 另一执行器持有契约内租约 | 不提前确认队列行（重领通道保留） | `test_rival_executor_leaves_recovery_channel_intact` |
| 两个执行器同时收尸同一运行 | 同库两个执行器先后收敛（同账户与另一账户各有一条在途运行作对照） | 恰一条终态事件（SQL 计数亦为 1），消息与运行同源同码；两条在途运行逐字段不变、其消息仍 `streaming`、`model_run_locks` 计数不变（恢复不产生锁、不破坏在途锁） | `test_competing_recovery_of_same_run_yields_one_terminal` |
| 运行被删除后的迟到恢复 | 删除运行行、遗留队列行 | 完成队列行、不复活运行、不写事件 | `test_late_recovery_after_run_deleted_completes_queue_without_revival` |
| 会话被删除（孤儿运行） | 删除会话，消息与运行残留（另一账户有在途运行作对照） | 只收敛运行、不补发事件（不复活数据）；另一账户不受影响 | `test_late_recovery_after_conversation_deleted_converges_run_without_events` |
| 断点续订回放（完成/失败/停止） | 从运行期保存的游标重订阅 | 游标单调、回放以该运行唯一终态结束、传输种类不变 | `test_replay_from_saved_cursors_is_monotonic_and_ends[done/error/stopped]` |

**同事务窗口**：本票不引入跨对象事务（三写仍分步提交），因此「回滚即可」不适用，验证方式是上表的幂等修复；`run_lock_tracer` 的既有同事务回滚证据见 `tests/chat/test_run_lock_tracer.py:267`。

## 3. 恢复策略说明（实施范围 2–3）

1. **分步提交 + 持久守卫**：顺序固定为 消息 → 终态事件 → 运行，每步自带守卫（消息只从 `streaming` 进终态、事件每运行至多一条、运行只从 `queued/running` 进终态），任意中断点留下的都是可判别残留，重复调用只补缺的那一步。
2. **消息是唯一真相源**：恢复一律从已提交消息派生结果，只有消息仍 `streaming` 时才用调用方的兜底结果。收尸因此不会把已完成的回合改写成失败（`expire_overdue_runs` 由「选取即改表」改为纯选取，终态写入归 module）。
3. **沿用既有上限与租约**：`MAX_EXECUTION_ATTEMPTS = 2`、租约时长、队列语义均未改；未耗尽预算的失联运行走重领（需要队列行），耗尽后走收尸。收尸写入失败不留半终态、执行器不停摆。
4. **不批量清理旧记录**：`TerminalOutcome.from_run` 覆盖迁移前「运行已终态而消息仍 streaming」的半写形态（运行 `done` 而消息从未提交结果 → 内部错误，绝不显示成功）；消息/会话已删除时只收敛运行、不补发事件。

## 4. 实现中发现并修掉的结构问题

- **迟到收尾能写出第二条终态事件**（本票新用例抓到，非既有测试覆盖）：被收尸释放的僵尸回合仍走到自己的收尾并追加 `done`，订阅端回放出现「失败之后又完成」。修法：判重并入唯一写入入口 `append_generation_event`（同一事务），删除 `append_terminal_generation_event`。
- **队列行是在飞运行唯一的重领通道**：竞争执行器原先「跳过并确认队列行」会让未耗尽预算的失联运行既领不回、也收不了尸（收尸只选取预算耗尽者），永久停在 `running`；现改为跳过时不动队列行。
- **实时错误载荷与补齐载荷不是同一套规则**：缺码时实时通道写 `generation_failed`（未映射、不可重试），补齐通道写 `internal_error`（已映射、可重试）——同一次失败读到什么取决于谁先写；现统一走 `runtime_error_payload`。
- **收尸不再伪造失败**：收尸前先判定消息是否已提交结果；运行 `done` 时只补运行与事件，不改写为失败。

## 5. 验证命令与结果

conda `agent` 环境、工作树内运行（`pyproject.toml` 的 `pythonpath=["src"]`）；basetemp 一律仓外（`C:/Users/33755/Desktop/BridGes/.tmp/codebase-03/bt-*`）；两侧同一命令同一跑法（`-q -p no:cacheprovider`，不 ignore，除下表所述 deselect 外不裁剪）。

| 命令 | 结果 |
| --- | --- |
| `pytest tests/chat/test_terminal_recovery_and_replay.py -q` | **13 passed**（本票新增；11 个用例函数 + 1 个三参数回放用例） |
| `pytest tests/chat/test_terminal_core.py ... test_run_lock_tracer.py test_v2_02_resumable_runs.py test_chat_api.py test_chat_service.py -q`（01/02 与持久生成、停止、竞争、重连回归定点） | **101 passed / 2 failed**；两条失败（`test_conversation_lifecycle_persists_pin_rename_project_and_order`、`test_study_mode_initial_thinking_mentions_learning_contract`）在主仓 `cb10ae8` 逐名复现（2 failed in 2.90s），是预存在失败 |
| `pytest -q` 全量（分支 `6ef3771`，`--deselect` 三条 `tests/integration/test_runtime_smoke.py::test_start_fails_*`） | **249 failed / 3881 passed / 39 skipped / 3 deselected / 0 error**（1254 s） |
| `pytest -q` 全量（基线＝主仓 main `cb10ae8`，同一命令但不 deselect） | **251 failed / 3869 passed / 39 skipped / 0 error**（1404 s） |
| 失败名单双向 diff（基线名单扣除三条 `start_fails`） | **两侧各 249 条，`diff` 与 `comm -3` 均为空**（分支名单 md5 `bad2eb5fd8c80efa00a3ce543fee3cde`，基线 `6604806b62d092cc1d12eaff58ffc02c`） |
| 账目闭合 | Δ失败 −2 = 两条 `start_fails` 由「失败」变「deselect」；Δ通过 +12 = 本票 13 条新用例 − 1（第三条 `start_fails` 在基线是通过，现将 deselect）；Δ跳过 0；Δ收集 +13 = 本票新用例数（分支选中 4169 + deselect 3 = 4172，基线 4159） |
| 进度字符单写者校验（两侧） | 基线 `.`3869 + `F`251 + `s`39 = 4159 = 摘要总数；分支 `.`3881 + `F`249 + `s`39 = 4169 = 摘要总数（+3 deselect 未计入进度），两侧日志都只有一个写者 |
| `ruff check`（本票改动文件 + 新测试文件） | 干净；`src/bridges/chat/` 整包 9 条与主仓逐条相同（`routing.py` 5、`repository.py` 2、`checkpoints.py` 1、`graph.py` 1，均预存在） |
| `mypy --no-incremental`（本票 4 个源文件） | 与主仓逐条同名同数：`terminal.py`/`repository.py`/`run_executor.py` **0 条**，`service.py` 5 条（行号位移、签名相同）。分支侧多出的 15 条全在本票未触动的文件里（`checkpoints.py`/`graph.py`/`turn.py`/`contracts/career.py`/`image`/`mcp`/`runtime`/`video`），在主仓把这些文件当根文件直接检查时**逐条复现**（15 条 md5 相同）——差异来自「主仓 src 是 editable 安装路径，被跟随模块按 site-packages 静默」这一环境原因，与本票无关 |

**跑法备注（两条实证）**：

1. **满负载下 `test_start_fails_*` 会挂死**：本票第一次分支全量（16:09 启动）在 53% 处挂死 14 分钟，进程树显示 `bridges.cli.main start` → `api`/`worker`/`scheduler` 四服务已起、用例停在监督循环里；同一时段另有并行会话的全量在跑（`--basetemp=…/bt06-base`）。按既有做法两侧对称 deselect（02 轮同样处理）后重跑即通过。基线侧本轮独自跑完（1404 s）未触发。
2. **全量结果必须验单写者**：两侧都用「进度字符数 = 摘要总数」校验（日志可能被残留进程续写），本轮两侧都通过。

**留档**：主仓 `.tmp/codebase-03/raw/`（`full-base.log`、`full-branch.log`、`base.names.txt`、`branch.names.txt`、`mypy-*.txt`）。

## 6. 两轴评审与修正

按 `code-review` skill 跑两轴（固定点 `cb10ae8`，两个并行只读子代理，分别带仓库文档化标准 + Fowler 坏味道基线、工单全文）。

**Standards 轴**（硬性违规 1 条、判断性 5 条）：

| 发现 | 处置 |
| --- | --- |
| `run_executor.py` 注释断言「队列行是该运行唯一的恢复通道（重领或收尸都要先领回）」——收尸（`list_overdue_runs` + `reap_lost_run`）不经队列行，断言不成立 | **已修**：注释改为真实约束「预算未耗尽时唯一的重领通道」，并说明提前确认会让运行既领不回、也收不了尸（收尸只选取预算耗尽者） |
| 工单文件未随提交更新（状态/验收/交付物） | 本节与第 1–5 节即为此补全 |
| 终态 module 内四处同形状的失败终态构造 | **已修**：抽 `_error_outcome`，三条兜底结果共用 |
| 实时事件载荷与补齐载荷两套映射 | **已修**（见第 4 节第 3 条） |
| `_TERMINAL_EVENT_KINDS` 与 `TerminalOutcome.event_kind` 两处维护终态种类 | **接受**：存储层守卫不反向依赖领域 module（依赖方向 `chat.terminal → chat.repository`），第 1 节列为可核验例外 |
| 读取路径 `TerminalCommit` 未携带提交后记录，调用方二次读库 | **接受**：单行主键读，且回执的语义是「本次真正写了什么」而非记录快照，携带记录会让回执与库状态耦合 |

**Spec 轴**（缺失 3 条、越界 3 条、语义出入 2 条，另有重复映射 2 条）：

| 发现 | 处置 |
| --- | --- |
| 交付物（入口收口清单/故障矩阵/恢复策略说明）未落盘 | **已补**（第 1–3 节） |
| 验收 7「所有终态入口接入共同规则」在 `turn.py` 仍有 22 处 `kind="error"`、9 处 `kind="done"` 与 44 处 `finalize_message` 自写终态 | **部分接受 + 立据**：这些是消息主写入方（回合编排），跨对象一致性仍由 module 从消息派生、终态事件唯一性由存储入口守卫；第 1 节给出可核验理由，避免「以后迁移」式空话 |
| 验收 2 缺队列确认窗口的故障注入 | **已修**：新增 `test_queue_confirmation_window_keeps_result_and_confirms_row` |
| 越界：竞争执行器不再确认在飞运行的队列行 | **保留**：这是验收 2/6「不产生永久悬挂队列项 + 失联后仍按既有上限领取恢复」的必要条件（否则预算未耗尽即永久 `running` 且队列项悬挂），第 4 节记录 |
| 越界：`run_tick` 新增收尸异常捕获与摘要拼接 | **保留**：`StorageError` 属可重试故障，放任其终止执行器会让其余运行无人收敛；摘要改为与 profile-extraction 拼接，避免收尸故障被吞进日志 |
| 越界：收尸不再把 `model_id`/`run_lock_id` 置空 | **保留且有测试**：置空会丢掉消息与在途模型锁的关联（锁无法按消息释放）；`test_message_commit_window_recovery_repairs_without_second_model_call` 断言恢复后消息的 `run_lock_id` 与运行的 `model_lock_id` 仍是失联前提交的那把锁 |
| 语义：收尸丢弃 `run.error_code`/`run.duration_ms` 的旧回退 | **已核验为死路径**：`generation_runs.error_code/duration_ms` 的唯一写入点是 `finalize_generation_run`（`repository.py:2027/2028`），而它在分步顺序里晚于消息提交——收尸窗口内这两列必为 NULL |
| 语义：孤儿运行（会话已删）仍被写 `failed` | **保留**：不写终态会让运行永久 `running`；收敛只改运行行，不补发事件、不复活消息（用例断言事件集不变） |
| 重复映射：`graph.py`/`run_executor.py` 的 `generation_failed` 字面量 | **已修**：两处均改引 `INTERNAL_ERROR_CODE`（全仓 `grep '"generation_failed"'` 为空） |
| 重复映射：`study/service.py` 等模块服务自写终态 | **接受**：与 `turn.py` 同类（模块工作流的主写入方），第 1 节例外 1 覆盖 |

## 7. 提交版本

- `6a1d6d9` feat(chat): 生成终态失联恢复与回放接入共同终态 module（codebase issue 03）——实现主体（4 个源文件 + 13 条新用例）。
- `6ef3771` refactor(chat): 评审修正——实时错误载荷与补齐同规则、收口重复默认码（codebase issue 03）——两轴评审后的修正。
- 分支 `codex/03-generation-recovery-and-replay`，分支点 `cb10ae8`（= 合并前 main）。

## 8. 未完成与残余风险

- **模型外部副作用不保证恰好一次**（工单范围限制）：恢复只保证持久终态一致与幂等，第二次尝试可能重复调用外部检索/工具；进程外副作用不在本票承诺内。
- **模块服务的主写入路径未迁移**：`turn.py` 与各模块 service 仍自写消息终态（第 1 节例外 1 已立据）。它们的文案/可重试性已共用同一映射表，但若将来新增映射规则，需同时检查这些入口。
- **`_TERMINAL_EVENT_KINDS` 字面量表**：新增终态种类时要同步改 `repository.py` 与 `terminal.py` 两处（第 1 节例外 2 已立据）。
- **收尸故障只在下一轮重试**：`run_tick` 每轮只尝试一次，若存储持续不可写，运行会持续停在待收敛形态（不产生错误终态，也不丢失），需运维侧修复可写性后自愈。
- **测试用真实临时库、非真实进程击杀**：「进程重启」以同一数据库文件重建仓库/服务/执行器表示；真实 `kill -9` 与 Windows 句柄释放时序未覆盖。

## 9. 合并、合并后验证与清理实证（2026-09-28）

- **合并**：`git merge --no-ff codex/03-generation-recovery-and-replay` → `a8d222a`（ort 策略，零冲突，8 文件 +1417/−295）。**合并树 == 分支树 `1e36e07ea7c8122ae44579dc58032c3e0fc57f13`**、`rev-list main..branch` = 0、`git diff --stat main branch` 为空 ⇒ 分支全量结论（249F/3881P/39S/3desel）对 main 直接成立。合并前 main 仍是分支点 `cb10ae8`（并行票 06 在另一工作树进行中，未推进 main），故无需跨票对账。
- **合并后定点复跑**（主仓 main，仓外 basetemp）：`tests/chat/test_terminal_recovery_and_replay.py + test_terminal_core.py + test_terminal_stop_and_graph_errors.py + test_v2_02_resumable_runs.py` **39 passed**（77 s：本票 13 + 01 的 9 + 02 的 7 + 持久运行 10）。
- **推送**：`cb10ae8..a8d222a main -> main`（走本地代理），main == origin/main `a8d222a`；本票分支从未推送（`ls-remote --heads origin '*03-generation*'` 为空）。
- **零迁移**：`git diff --stat cb10ae8..HEAD -- src/bridges/storage/` 为空，`SCHEMA_VERSION` 仍 59（下一票 60）。
- **清理**：`git worktree remove .worktrees/03-generation-recovery-and-replay` **一次成功**（本轮测试都以仓外 basetemp + `-p no:cacheprovider` 运行，目录无受限 ACL、无句柄占用）；本地分支已删（was `893c2b2`）；`git worktree prune -v` 无输出、`.git/worktrees/` 只剩并行票的两条。`.worktrees/` 下仍留有 02 轮的惰性空目录 `02-generation-stop-and-graph-errors`（不挂 git、不影响命令，02 记录已如实登记）。
- **留档**：主仓 `.tmp/codebase-03/raw/`（两侧全量日志与失败名单、mypy 对照文件）。
