# 10 — 以校园通勤贯通持久节点、收据与执行内核

**What to build:** 校内通勤中定位完成、路线失败后只重试路线；取消或旧租约晚返回不能改任务状态，结果地图与时间一致。

**Blocked by:** 04 — 守住最终模型载荷预算与材料权威边界；08 — 保存有来源的跨轮任务、有效条件与澄清；09 — 持久化整次运行预算与有限调整额度

**Status:** ready-for-agent

**优先级：** P0

## 背景与需求

模块 service.run 黑盒中的节点事件不构成恢复边界。通勤有明确工具链，适合先证明最小执行内核。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/daily-workflows.md](../../../docs/workflow/daily-workflows.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/workflow/review.md](../../../docs/workflow/review.md)。
- [docs/v2/feasibility.md](../../../docs/v2/feasibility.md)。

## 任务内容

1. 将解析、地点消歧、范围/方式验证、路线请求、缓冲、核验和呈现变为真实持久节点。注册配方的 Schema/模式/必经顺序/必要与可选门/恢复分支；由代码拒绝循环、未知能力和跳过前置。
2. 工作节点接收最小任务/版本/运行/节点引用及剩余预算，输出类型化证据与结果；专业角色不能自主委派，通勤计算保持确定性。
3. 节点局部事务保存产物、输入依赖/哈希、质量裁决、完成收据与待投递事件；检查点存引用。产物先于检查点落盘时恢复从收据回填。
4. 本地消息/附件/产物效果以唯一键、预期版本和终态守卫防重复；外部请求允许至少一次，不承诺跨系统恰好一次。提交前检查租约、停止、任务版本和权限。
5. 保持华东交通大学校内/校门范围和步行/自行车/电动车。缺项只问关键一项，已确认地点不重复问；方式变更复用定位并使路线/时间失效。
6. 按 Asia/Shanghai 求解时间快照执行 07:50、09:30、10:00、12:00、14:20、16:00、16:30、18:10 前后 10 分钟加 5 分钟；标为约定缓冲。无对应方式真实路线不替代计算；地图不足按既有合同给真实定位点/文字限制。
7. 模块只返回待交付产物，内核核验并用既有终态服务提交；旧通勤结果和运行谱系保留兼容解释。

8. 产物至少区分草稿、证据已绑定、合格、冲突、失效；质量门结构化返回通过、待输入、可修复失败、阻塞、失效。节点输入/输出含 Schema 与能力版本、来源/读取范围、输入依赖、需求覆盖、未确认项及错误，不用自由文本代替状态。

## 跨票接缝与责任

本票拥有最小节点执行/配方/收据/统一提交接口，采用通勤这一条实际纵向路径。24–36 迁移其他领域，不先建设巨大通用平台。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 地点成功/路线失败后只恢复路线，受影响依赖外的节点不重跑。
- [x] 收据已提交而检查点未保存时重启不重复本地效果；旧租约和停止后的迟到结果被拒绝。
- [x] 地点、范围、方式、真实路线、距离/时间单位、缓冲和地图文字一致；POI 成功不被当成路线成功。
- [x] 高峰边界和缺路线场景通过，真实服务可得性由 42 单独实测。
- [x] 运行成功、产物合格与任务完成分别表达；事件只报告真实节点，旧结果可读可导出。
- [x] 新增产物/收据有账户隔离、迁移、删除/导出和恢复验证。

## 验证与交付证据

先用固定高德响应跑全链和收据/检查点间故障注入；对迟到、停止、方式改动及高峰边界做确定性验证。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 执行与验收记录

### 2026-10-02：实现、两轴评审与修复

- 交付分支 `codex/10-resumable-commute-kernel-pilot`（工作树 `.worktrees/10-resumable-commute-kernel-pilot`，基点 main `c870e194`）；本记录与实现同一提交。
- 内核：新增 `src/bridges/kernel/`——`contracts.py`（产物/收据/质量裁决/配方合同，产物身份 `(account, conversation, node, input_key)`、收据身份 `(account, run_id, node, input_key)`）、`registry.py`（能力/门成员校验、必经顺序、循环/重复/跳过前置/未知能力拒绝）、`repository.py`（产物/收据/外箱同一节点局部事务；完成收据不被覆盖；账户作用域强制）、`guard.py`（提交前校验租约/停止/运行终态/消息归属/任务版本；运行与消息快照经属主 repository 只读接口，不直连）、`executor.py`（收据优先恢复、跨运行产物回填、质量门、失效、交付前最终校验、外箱投递）。
- 通勤迁移：新增 `src/bridges/commute/kernel.py`（七节点配方 `route.parse→resolve→validate→request→buffer→verify→present`，语义输入键：resolve 不含方式以支持方式变更复用，validate 失效 `route.request/buffer/verify/present`；“地图文字/缓冲/单位/方式”结构化质量门）；`src/bridges/commute/service.py` 重写为内核驱动，四类交付（成功/澄清/停止/失败）复用既有消息终态收敛，SUPERSEDED 经 `CommuteSupersededError` 抛出。
- 迟到结果防线：`chat/graph.py` 新增 `DailyGraphSuperseded`（`_invoke_commute_module` 捕获、`run_daily_turn` 不写终态）；`chat/run_executor.py` 在 `run_graph_turn` 后校验租约归属，租约已转给其他执行者则不收敛/不确认队列（本回合内部已收敛、租约被清空时仍走幂等收尾，关闭预算账本）。
- 任务版本接缝：`api/main.py` 用 `_commute_task_reference`（基于 `TaskService.current_reference_context`）向 `CommuteService` 注入 `task_version_provider`。
- 迁移与治理：`storage/database.py` `SCHEMA_VERSION=65`，迁移 65 建 `node_artifacts` / `node_receipts` / `node_outbox` 与索引并入 `REQUIRED_TABLES`；`lifecycle/catalog.py` 三表入账户域导出/删除/摘要，新导出分类 `node_kernel`（“通勤节点产物与收据”）；`docs/table-owners.md` 增 `kernel/` 属主行。
- 与规范的两处有意偏差（已在评审中复核）：①“检查点存引用”由产物/收据表承担——内核把引用（`artifact_id`/`input_key`）即持久节点状态，父图模块节点不再另存检查点状态；“收据已提交而检查点未保存”的回放/故障交错由同一运行重放与跨运行回填测试覆盖。②`QualityVerdict.INVALIDATED` 在通勤只在取消路径产生（`amap_cancelled`），服务按既有停止合同收敛正确；`ArtifactTrust.CONFLICTED` 合同已支持但通勤暂无冲突产出路径。
- 两轴评审（标准＋规格并行子代理）发现与处置：
  - 修复：复用路径完全绕过提交守卫（全量收据/产物命中时旧租约迟到仍会交付）→ `NodeKernel.execute` 返回 `COMPLETED` 前做最终 `guard.verify()`（同一事务），失败走 `REJECTED`/`STOPPED`；新增“最后节点提交后转租约”确定性测试。
  - 修复：`RunCommitGuard` 直连 `generation_runs`/`messages` 违反表属主纪律 → 改为注入 `CommitScopeReader` 协议（属主 `ConversationRepository` 提供快照）；同时删除未被使用的 `allow_stopped`/`require_message_active`/`captured`/缓存过期字段与未用的 `RecipeInputs.dependency()`。
  - 修复：失效产物行会被同输入重跑原位覆盖，原注释“保留供审计”不成立 → 失效事实随收据写入外箱事件 `node_artifacts_invalidated`（含节点与产物 ID，可导出），并修正注释；新增外箱审计断言。
  - 未采纳（判定为可接受/后续票）：节点执行体的能力/版本常量映射与 `_digest` 与合同重复、`attempt=1` 固定、`_failure_execution` 冗余参数——无行为影响，属通勤试点实现细节；`kernel` 对其他领域的泛化（含检查点引用统一化）按票面留给 24–36 迁移。
- 验证（conda `agent`，`PYTHONUTF8=1`）：
  - 新增测试：`tests/kernel/test_node_kernel.py` 12 项（登记拒绝、收据复用、失败收据只重跑该节点、必要门失效、`invalidate_nodes` + 外箱审计、停止/旧租约/最后节点后转租约/消息终态拒绝、跨运行复用与输入变化、账户隔离）；`tests/storage/test_schema_v65.py` 4 项（建表/REQUIRED_TABLES、v64→v65 保留数据、账户隔离/删除/导出/摘要/快照恢复）；`tests/commute/test_commute_kernel_recovery.py` 2 项（路线失败只重跑路线：`place_calls` 不变、resolve 收据 1、request 收据 2、present 1；方式变更复用定位并使步行产物失效）。
  - 定向回归：`tests/commute` + `tests/kernel` + `tests/storage/test_schema_v65.py` + `tests/chat/test_improvement09_run_budget_integration.py` 共 128 passed；既有通勤 e2e 合同保持通过。
  - 全量 `pytest -q -n 4`：分支 250 failed / main 250 failed，失败集逐项一致，**0 新增失败**（既有环境性失败与本票无关）；期间 `tests/closeout/test_arxiv_worker_reliability.py` 出现一次并行负载下的计时抖动，单测与整文件连续 3 次均通过（14 passed×3）。
  - `mypy src`：98 条错误/35 条唯一信息，与 main 集合逐条一致（既有 `graph.add_node`/`api.main` 健康投影问题）；kernel/commute/run_executor 无错误。`ruff check` kernel/commute/run_executor/新测试全过；`graph.py` 新增 `DailyGraphSuperseded` 的 N818 与同文件既有 `DailyGraphStop` 一致。
- 限制：本票只证明确定性机制（固定高德响应与确定性执行），真实高德可得性/限流体验由 42 实测；持久节点内核目前服务于通勤试点，其他领域迁移与统一检查点引用按 24–36。交付前校验与消息终态守卫之间仍有跨层窗口，由执行器租约复核与终态“最多一次”守卫兜底。
- 状态改 `ready-for-human` 等待人工验收；分支已提交、未推送、未合并。

### 2026-10-02：用户授权独立验收

- 已独立验收并修复合同版本复用、历史产物覆盖、方式回切失效、求解时间快照和最终交付守卫窗口；新增七个回归参数实例并扩展方式回切测试。
- 最终核心 135 passed；扩大回归分支 1047 passed / 105 failed / 1 xfailed，主线 1020 passed / 106 failed / 1 xfailed，分支失败均为主线既有失败，无新增失败。
- 六项验收标准按确定性路径确认通过；用户已授权合并、推送及清理。历史交付记录保留，本次修正与证据以[独立验收记录](../acceptance/10-resumable-commute-kernel-pilot.md)为准。
- 已以 `7621f1c6` 无冲突合入 main，合并后专项 135 passed，并成功推送 origin。Issue 10 工作树与本地分支已删除，worktree 失效记录检查/清理完成；Issue 13、14 有效工作树保留。
