# 13 — 后台生成有界摘要并按来源缓存

**What to build:** 长会话按预算压力整理较早历史，复用有来源的摘要；当前请求与最新纠正不等待后台任务。

**Blocked by:** 04 — 守住最终模型载荷预算与材料权威边界；08 — 保存有来源的跨轮任务、有效条件与澄清；09 — 持久化整次运行预算与有限调整额度；11 — 解析任务指代并补回必要原文

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

逐条截前 160/60 字既丢掉末尾语义又随消息数线性增长，不能靠加大模型窗口保证连续性。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/上下文工程/讨论记录.md](../../../docs/上下文工程/讨论记录.md)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。
- [docs/上下文工程/审查与改进建议.md](../../../docs/上下文工程/审查与改进建议.md)。
- [docs/上下文工程/复核脚本.py](../../../docs/上下文工程/复核脚本.py)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。

## 任务内容

1. 采用近期原文、较早片段结构化摘要、按需原文回补；摘要总长度有上限，不要求每条旧消息永远留一行。
2. 从明确范围的已完成原始消息生成，保留消息范围/ID、对象线索、开放问题、摘要实例版本和覆盖边界。重建回到原文，避免不断压缩上一版积累偏差。
3. 按历史预算压力和新增原文量触发回答后后台准备；正常短对话不调用摘要模型，不用固定轮数或填满硬窗口触发。
4. 下一轮优先复用有效缓存，确需压缩且缓存缺失才同步限时补齐；次数、超时、重试/token 预算有上限，参数按 40 的基线校准。
5. 检查原文出处、数值、否定和纠正关系；摘要只是背景/定位线索，不写成用户事实或改变学习题目/阶段。
6. 来源删除、变更、权限失效或 18 确认的事实抑制变化时失效相关缓存；失败保留预算允许原文与明确缺口，不写入猜测。

## 跨票接缝与责任

拥有摘要任务/缓存/实例版本；08 维护有效条件，11 解析原文，18 接入事实抑制。后台任务复用既有队列与账户/会话隔离。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 121 条长历史最终输入收敛到预算内或明确受限，不随消息数无限追加摘要行。
- [x] 末尾条件、否定、后续纠正可追溯原文，新纠正本轮生效不等待缓存。
- [x] 新增片段只整理需要压缩部分，有效缓存复用，原文重建结果具可核验来源。
- [x] 正常短对话无不必要摘要调用；同步补齐超时/重试耗尽后不会无限等待。
- [x] 来源无效后不复用派生依据，缓存迁移/删除/导出和取消守卫完整。

## 验证与交付证据

可控模型响应模拟摘要正确、捏造、超时及原文变更；测调用次数和缓存实例，真实效果在 40 比较。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 执行与验收记录

### 2026-10-02：实现、两轴评审与修复

- 交付分支 `codex/13-bounded-summary-cache`（工作树 `.worktrees/13-bounded-summary-cache`，基点 main `c870e194`）；实施前核对阻塞票 04/08/09/11 均已实施并有独立验收记录（`.scratch/2/acceptance/04-*.md`、`08-*.md`、`11-*.md`、`.scratch/2/validation/09-run-budget-acceptance.json`）。本记录与实现同一提交。
- 合同：`src/bridges/contracts/summaries.py` 新增 `summary-cache-v1` / `summary-gen-v1`（`HistorySummary` 不可变实例：覆盖边界、来源指纹、实例版本、状态；`SummarySourceMessage`；`SUMMARY_*` 上限常量）。
- 摘要域：`src/bridges/chat/summary.py` 实现 `ConversationSummaryRepository`（`save_if_sources_unchanged` 取消守卫+重叠失效、`invalidate`/`invalidate_conversation` 保留行供审计、`delete_for_conversation` 级联）、`ChatSummaryService`（`valid_summaries` 读取即失效失配、`prepare_sync` 一次限时补齐、`schedule_background` 按新增原文量登记、`run_tick`/`pending_count`）、`validate_summary_output`（结构/长度/数值保真/否定保真）与 `GatewaySummaryExtractor`（已验证额度、`RunBudget` 超时、`ModelRunLockRecorder` 以 `conversation/summarize_history` 业务关联记录锁）。
- 编译：`compile_turn_context(..., summaries=)` 接收有效实例；较早片段优先渲染缓存块（含实例版本与覆盖边界），未覆盖部分用有界确定性回退行（≤12 条/1800 字符），`SUMMARY_VERSION` 升为 `summary-v2`；新增 `summary_cache_hit`/`summary_instance_ids`/`summary_fallback_entries`/`summary_omitted_message_count` 审计字段。摘要总长只随缓存实例数有界、不随消息数线性增长。
- 接缝：`ChatService` 编译时只读缓存，缺缓存且确需压缩做**一次**限时同步补齐后重编译，随后登记回答后后台准备；摘要域任何失败不阻塞回答。`GenerationRunExecutor.run_tick` 在生成 tick 后执行一次摘要 tick，`api/main.py` 装配 `ChatSummaryService`。
- 持久与生命周期：`SCHEMA_VERSION=65` + `MIGRATIONS[65]`（`conversation_summaries` 表、`idx_conversation_summaries_conversation`），登记 `REQUIRED_TABLES`/`REQUIRED_INDEXES`；`lifecycle/catalog.py` 加入 `ACCOUNT_TABLES`（先于 `conversations` 删除）与导出分类 `history_summaries`；`AuditAction` 新增 `HISTORY_SUMMARY_PREPARED`/`HISTORY_SUMMARY_INVALIDATED`；`docs/table-owners.md` 登记表属主。
- 触发与上限参数（暂定，按票面留 40 校准）：新增未覆盖原文 <2 条不调用；同步补齐 1 次调用/8s，后台每任务 ≤4 段/20s，重试 ≤3 次（指数退避）；单段来源 ≤6000 token，单条摘要 ≤1200 字符。
- 两轴评审（标准＋规格并行子代理）发现与处置：① `GatewaySummaryExtractor` 记录锁时漏传 `business_ref`（真实记录器端口会拒绝，替身测试掩盖）→ 修正为 `conversation/summarize_history` 关联并收紧类型，补 1 项端口测试；② 摘要仓库消息排序与属主 `list_messages` 不一致（时间戳并列时切片与守卫可能错位）→ `_message_order`/`_message_slice` 统一为属主排序；③ `invalidate_conversation` 与 `_invalidate_locked` 重复 SQL → 收敛为单实现；④ 未使用字段/属性（`source_tokens`、`covers_message_ids`）删除；⑤ `service.py` 两处重复编译调用收敛为局部 `_compile_turn`；⑥ `table-owners.md` 自相矛盾的「编译只读」表述改为「后台主写入、编译期可一次限时同步补齐」；⑦ 重复的 `"sync"/"background"` 分支收敛为局部变量。未采纳项：覆盖范围含片段内被跳过的非 DONE 助手消息（指纹与条数保守包含、编译配对本身不纳入这些消息，不会产生信息丢失）；单块最坏长度可超 `SUMMARY_RENDER_MAX_CHARS`（实例正文/线索各自有上限，总量仍有界）；`HISTORY_SUMMARY_PREPARED` 在失败时以 `BLOCKED`/`RETRYABLE_FAIL` 记录属「准备尝试」语义。
- 验证（conda `agent`，`C:/Users/33755/anaconda3/envs/agent/python.exe`，`PYTHONUTF8=1`，测试子进程 `CODEBUDDY_SAFE_DELETE_ENABLED=0`，各轮独立 `--basetemp`）：
  - 新增 `tests/chat/test_improvement13_summary_cache.py` 26 项（校验/仓库/复用/失效/守卫/调用上限/锁记录端口）、`tests/chat/test_improvement13_acceptance.py` 8 项（121 条有界、尾部条件与纠正不等缓存、增量片段与来源可核验、短对话零调用、执行器组合 tick、失效与删除级联、导出/删除登记）、`tests/storage/test_schema_v65.py` 1 项（v64→65 无损迁移与幂等）。
  - 定向回归：本票 3 文件＋`test_improvement11_acceptance.py`＋`test_improvement11_reference_resolution.py`＋`test_v2_03_conversation_context.py` 共 **75 passed**；`bridges.chat.summary`/`chat.service`/`api.main` 导入检查通过。
  - 全量对照：分支 **250 failed / 4351 passed / 39 skipped / 1 xfailed**，main 基线 **250 failed / 4328 passed / 37 skipped / 1 xfailed**；失败名单逐项一致（250 项既有失败），多出 2 项跳过为 `tests/runtime/test_runtime_contract.py` 需要被 gitignore 的 `apps/web/.next` 构建产物（工作树未构建，符合其 skipif 语义），passed 差额＝本票新增 25 项测试减去该 2 项环境跳过；评审修复后复跑失败集仍逐项一致。
  - 静态检查：`ruff check` 本票全部改动文件通过；`mypy`（strict）本票新增/改动行无新增错误（service/main 残留错误与 main 基线逐条同名，仅行号位移）。
- 限制：确定性替身只证明机制；真实模型连续性收益与参数真值按票面留 40 比较。权限失效/18 事实抑制目前只提供 `invalidate_conversation` 接缝（18 未实现）；「纠正关系」检查由提示词约束＋11 的原文回补保证，非确定性校验器；导出/备份经生命周期目录登记由既有机制覆盖，未新增本票专用往返测试。
- 状态改 `ready-for-human` 等待人工验收；分支未推送、未合并。

### 2026-10-02：用户授权独立验收与合入

- 两轴独立验收发现并修复后台线程阻塞、永久失败重试、单条原文调用、数字/否定校验、在途失效回写与同步运行预算/恢复机会等缺陷；实例冻结，生成版本升级为 `summary-gen-v2`。详见[独立验收记录](../acceptance/13-bounded-summary-cache.md)。
- 本票修复后 32 项通过，定向回归 142 项通过；扩展检查 224 passed / 5 failed，五项失败在未修改 main 上逐项复现。确定性验收通过，真实模型效果按票面由 40 验证。
- 用户已授权合并、推送与清理；集成期间主线另合入工单 10，需保留两边迁移及内核执行接缝，合入结果记录在验收文件。
- 已以 `e7197892` 合入最新 main 并推送 origin；数据库冲突按主线 v65 内核、本票 v66 摘要合并，合并结果组合回归 58 passed。本票物理工作树目录、本地分支与登记已删除；失效 worktree 记录检查和清理完成。
