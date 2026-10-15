# Issue 02 交付：事务与写入所有权、活动消费者清单、外部兼容义务

本文件回答三个问题：一次画像写入的事务边界在哪、生产环境的全部活动写入
入口如何到达该边界、有哪些对外契约不能因后续替换写模型而改变。所有引用
均为本分支 `cfc224c4` 起点代码上的实际行号（行号以本票最终提交为准）。

## 1. 唯一提交边界：ProfileCommit

归属地：`src/bridges/profiles/commit.py`（348 行）。

- `ProfileCommit.transaction()`（commit.py:164）：按固定顺序并入参与者
  事务——`state`（自动提取记账仓库，可选）→ `records`（四维来源记录）→
  `items`（原子镜像）。SQLite 仓库共用同一 `BridgesDatabase` 时只有第一
  次调用真正 BEGIN，其余并入；内存仓库各自建立可回滚快照，同一组业务
  对象一起回滚（并入规则在仓库 adapter 内部，见
  `src/bridges/profiles/transactions.py` 的 `joined_transaction`）。
- 成对写入只在本模块声明：
  - `write_records()`（commit.py:173）：四维记录 + 原子镜像同成同败，
    记账行由调用方写在同一边界内。
  - `write_correction()`（commit.py:223）：聊天更正先写四维、再镜像，
    首次处理与后台重试共用同一实现。
  - `withdraw_item_sources()`（commit.py:248）与
    `recover_source_withdrawals()`（commit.py:286）：墓碑提交之后的来源
    撤回，逐条隔离且幂等；恢复只凭持久墓碑推导，不依赖进程内对象。
- 保护点语义（commit.py 文档第 29-45 行）：墓碑、换键与抑制键在边界内，
  失败即整批回滚、入口可整轮重试；来源撤回在保护点之后，失败不回滚墓碑，
  由恢复方法幂等补齐。

### 用户权威写入统一进边界（本票改动）

Issue 02 前，原子服务的「记住 / 行内编辑 / 目标生命周期」各自开仓库事务，
删除/忘掉已走提交边界；同一类用户操作存在两种事务开法。本票统一为：

- `AtomicProfileService.modify_item`（atomic.py:2089，边界 2100）
- `AtomicProfileService.remember`（atomic.py:2202，边界 2241）
- `AtomicProfileService._apply_goal_lifecycle`（atomic.py:2409，边界 2431）
- `AtomicProfileService.delete_item`（atomic.py:2175，边界 2184）与
  `forget`（atomic.py:2328，边界 2361）：原本已走提交边界，未改动。

因此「事务参与方只由提交 module 声明一次」对四维 + 镜像的双写与全部用户
权威操作都成立；条目换键、抑制键、墓碑等业务规则仍留在原子画像实现内
（commit.py 文档与 `AtomicProfileService` 类文档均已更新，atomic.py:1774）。

### 生产中的两个 ProfileCommit 实例（已知约束，非本票改动）

- 自动提取编排器：`AutomaticProfileService.__init__`（automatic.py:1489）
  自建实例，参与者 = state + records + items，用于抽取与更正提交。
- 原子画像服务：`AtomicProfileService.__init__`（atomic.py:1799）在组合根
  未注入时自建兜底实例，参与者 = records + items，用于用户权威写入。
  生产组合根（`api/app_setup_core.py:303`）不注入共享实例。

两实例引用同一组 records/items 服务，落库语义相同（SQLite 由 adapter 并入
同一连接事务；内存仓库按同一组对象回滚），但对象不是同一个。未合并的原因
是构造依赖顺序（提交实例需要 items=原子服务，原子服务又需要提交实例），
统一会引入可变的迟到注入；本票不新增该机制。03 替换写模型时应以
`ProfileCommit`（两个实例的公共类型与公共边界）为替换面。

## 2. 活动写入消费者清单（production 路径）

### A. 聊天同步控制（回答生成前必须完成）

| 入口 | 调用链 | 落点边界 |
| --- | --- | --- |
| 聊天预处理 `chat/service_profiles.py:51` | `AutomaticProfileService.process_synchronous_controls`（automatic.py:1834） | — |
| 停止记录指令 | `_repository.block_recording` + `mark_message_tombstone`（automatic.py:1872-1877） | 记账仓库单侧（非双写） |
| 显式「记住/忘掉」 | `_memory_directive_result`（automatic.py:2539）→ `atomic.remember`（2560）/`atomic.forget`（2569） | `ProfileCommit.transaction`（atomic.py:2241/2361） |
| 明确纠正 | `_preprocess_correction`（automatic.py:2387）→ `commit.transaction()`（2414）→ `commit.write_correction`（2353） | `ProfileCommit.transaction` |
| 离线重放/脚本同步入口（`process_message`，automatic.py:1799 区域） | `process_synchronous_controls` + `_run_scheduled_now`（2200）→ `_run_retry_task` | A 的控制分支同上；普通提取见 B |

### B. 后台异步提取（回答完成后）

| 入口 | 调用链 | 落点边界 |
| --- | --- | --- |
| 流式聊天 `chat/service_stream.py:374`、执行器 `chat/run_executor.py:429` | `schedule_message_extraction`（automatic.py:2061）→ `_schedule_ordinary`（2087） | 记账仓库事务（2179、2705、2754 为调度/重试记账） |
| 重试 tick / 执行 | `_schedule_retry`（2681）→ `_run_retry_task`（2831） | 业务写入 `commit.transaction()`（2910、2970）；失败记账与尝试记录在 3016 的仓库事务里（回滚之后另开，语义见 commit.py 文档） |
| 提交实现 | `_commit_output`（3176）→ `commit.write_records`（3310） | 同一 `ProfileCommit.transaction` |

### C. 画像 HTTP 原子路由（`src/bridges/profiles/api.py`，全部按账户鉴权）

| 路由 | 处理 | 落点边界 |
| --- | --- | --- |
| PUT `/profiles/controls`（142） | `set_account_controls`（automatic.py:3735） | 记账仓库单侧（控制开关，无镜像） |
| PATCH `/profiles/items/{id}`（285） | `modify_item`（atomic.py:2089） | `ProfileCommit.transaction` |
| DELETE `/profiles/items/{id}`（309） | `delete_item`（atomic.py:2175） | `ProfileCommit.transaction`（墓碑在边界内、来源撤回在保护点后，2536） |
| POST `/profiles/items/{id}/feedback`（359） | `record_feedback`（atomic.py:2028） | 反馈独立单表（2037），不修改条目 |
| POST `/profiles/items/migration`（387） | `migrate_account`（atomic.py:2947） | 迁移批次自有边界（2976、3060），只写原子仓库 |
| POST `/profiles/items/migration/{run}/rollback`（432） | `rollback_migration`（atomic.py:3197） | 同上（3207） |
| 全部 GET（items/evidence/migration 报告） | `list_items`/`item_evidence`/`latest_migration_report` | 只读，无写入 |

### D. Legacy 四维路由（保留，见第 3 节兼容义务）

| 路由 | 处理 | 说明 |
| --- | --- | --- |
| PATCH `/profiles/four-dimensions/{id}`（170） | `FourDimensionProfileService.modify_record`（four_dimensions.py:1086） | 四维仓库单侧写入（无镜像），既有客户端契约 |
| POST `/profiles/four-dimensions/{id}/withdraw`（193） | `withdraw_record`（1201） | 同上 |
| DELETE `/profiles/four-dimensions/{id}`（216） | `delete_record`（1223） | 同上 |

上述三个入口的自动写入路径（`upsert_automatic_record`，four_dimensions.py:970）
在生产只被 `ProfileCommit.write_records`（commit.py:199）调用；`mirror_record`
（atomic.py:2551）在生产只被 `commit.py:213/245` 调用。即自动双写只有一条
生产路径，全部经过提交边界。

### E. 恢复与启动

| 入口 | 调用链 | 说明 |
| --- | --- | --- |
| API 启动 `api/app_setup_models.py:139` | `AutomaticProfileService.recover_inflight_runs`（automatic.py:1615） | 本票显式化：构造不再触发；启动时对中断运行重新入队（记账仓库事务 1664） |
| 历史重放 `profiles/replay.py:834` | 同一方法（重放队列） | 与启动相同的显式恢复语义 |
| 来源撤回恢复 | `commit.recover_source_withdrawals`（commit.py:286）← `atomic.recover_source_withdrawals`（atomic.py:2538） | 只扫描持久墓碑，幂等；不重写业务结果 |

### F. 非生产消费者（评估/脚本，均为内存或新建库组合）

`bridges/evaluation/profile_quality.py`（258、939 等）、`profile_pairing.py:494`、
`expression_deterministic.py:137`、`expression_real_run.py:158-165` 直接构造
原子服务并调用 `remember/record_feedback/mirror_record`；这些组合不含持久
队列，恢复为 no-op，本票无需改动，行为与 main 一致（见验证记录）。

## 3. 外部兼容义务

- **HTTP 契约不变**：本票不改任何路由路径、方法、请求/响应模型、状态码与
  错误码文案。新增行为只在「恢复动作的触发时刻」上显式化，启动时的可见
  结果（中断抽取重新入队后可被再次处理）与 main 相同。
- **Legacy 四维路由保留**：旧客户端与迁移对账继续按 D 组契约工作；03 的
  写模型替换只发生在自动双写与用户权威路径，不改变 legacy 单侧写入语义。
- **迁移路由保留**：`/profiles/items/migration` 与回滚接口的账户级、可重复
  执行、只删本批次新条目语义不变。
- **Python 组合 API 变更**：`_recover_inflight_runs` 更名为
  `recover_inflight_runs` 并脱离构造函数；生产调用方仅
  `app_setup_models.py` 与 `replay.py` 两处（已随本票更新），无其他仓库内
  调用方（grep 实证）。这是内部组合点，不属于对外契约。
- **数据与 schema 不变**：无新持久化模型、无迁移、无数据回填（本票明确
  不做）。
- **评估脚本与既有证据**：`scripts/run_issue01_profile_baseline.py` 的调用
  方式与输出结构不变，01 的基线证据继续可用作对照。

## 4. Issue 02 实际改动清单

| 文件 | 改动 |
| --- | --- |
| `src/bridges/profiles/automatic.py` | 构造不再调用恢复（1505 区域）；`recover_inflight_runs` 公开化与文档（1615） |
| `src/bridges/api/app_setup_models.py` | 启动时显式恢复（139） |
| `src/bridges/profiles/replay.py` | `_build_service` 显式恢复（834） |
| `src/bridges/profiles/atomic.py` | 用户权威写入统一进提交边界（2100、2241、2431）；类文档（1774） |
| `src/bridges/profiles/commit.py` | 文档声明用户权威写入归属本边界（第 16-24 行） |
| `tests/profiles/test_issue02_memory_write_boundary.py` | 新增回归 7 项 |
| `tests/profiles/test_automatic_profile_extraction.py` | 重启恢复测试改为「构造纯净 + 显式恢复」两段断言 |

## 5. 03 的替换面（消费核对）

03 替换写模型时只需要在一个地方改变「四维记录 + 原子镜像如何写」：
`ProfileCommit` 的成对写入实现（`write_records`/`write_correction`）与
用户权威入口所在的事务边界（本文件第 2 节 A/B/C 组全部路径都会到达）。
A/B/C 组清单与代码实引一一对应，可直接作为替换后的消费者核对表；
D/E/F 组不经过自动双写，不需要随 03 改变。
