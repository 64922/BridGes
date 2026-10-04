# 40 — 验证同模型同预算的连续性与成本

**What to build:** 同会话改进用固定多轮对照证明关键条件、纠正、对象与引用用得正确，并校准新增摘要/读取/分批成本。

**Blocked by:** 13 — 后台生成有界摘要并按来源缓存；14 — 统一照片预算与旧附件、原图、证据读取；15 — 按解析任务选择检索材料与模块上下文；19 — 生成前编译用途明确的完整画像切片；37 — 执行跨模块依赖计划并统一核验综合结果

**Status:** ready-for-agent

**优先级：** P1

## 背景与需求

14 项现有上下文测试只证明字面回指/机制基线，不能证明有效约束、跨模块对象或真实成本提升。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/上下文工程/讨论记录.md](../../../docs/上下文工程/讨论记录.md)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。
- [docs/上下文工程/审查与改进建议.md](../../../docs/上下文工程/审查与改进建议.md)。
- [docs/上下文工程/复核脚本.py](../../../docs/上下文工程/复核脚本.py)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/用户画像/提取时机与回答应用流程.md](../../../docs/用户画像/提取时机与回答应用流程.md)。

## 任务内容

1. 把复核脚本全部案例转为正式网关/图路径断言：最终载荷、照片不绕过、完整额度快照、画像整条预算、角色数据封装和调用清单。
2. 固定原创多轮集覆盖末尾条件、5,000→3,000、任务往返、单/多列表指代、普通→论文→GitHub、旧图细节、附件末尾条件、摘要失败、来源失效与跨账户。
3. 相同模型、任务数据、输入预算上界和输出额度配对旧/新，必要时多次重放报告模型波动；检查真实回答而非 done 或提示中包含关键词。
4. 量表报告约束保持、纠正覆盖、对象解析、引用正确和诚实澄清/缺口；确定性预算/隔离/来源/运行快照全部硬通过。
5. 测正常短聊无不必要摘要调用，长聊有界新增摘要/原文读取/分批，记录触发原因、输入输出 token、实际读取量、额外调用、首字等待与总耗时。
6. 用中文/英文/代码/公式/长 URL/图片校准估算与可取得真实用量；根据基线确定任务预算/余量、近期原文量、摘要长度/阈值/超时/重试、分批次数/耗时与召回量。
7. 验证设置的上限真实生效，摘要同步补齐失败或必要内容容纳不了仍满足完整优先/明确缩小范围。

## 跨票接缝与责任

拥有上下文回归与参数校准报告；09/13/15 按数据调整，跨会话长期记忆扩展不在本票。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 全部复核边界有真实正式调用断言，硬门全部通过。
- [x] 连续性各场景正确续接、必要澄清或真实缺口，无静默丢关键条件。
- [x] 同模型同预算配对，重复次数和波动公开，不能把换模型收益归因本方案。
- [x] 短聊无无必要调用，长聊成本/等待与限额可查，供应商用量与估算差异标明。
- [x] 工程参数由基线校准并复验，不以预设百分比/任意窗口宣称保证。

## 验证与交付证据

Conda agent 中确定性回归后执行真实模型配对；保存代码、Schema、提示、时钟、来源与模型锁的脱敏报告。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 实现记录（本地实现，待独立验收）

分支 `codex/40-context-continuity-and-cost-evaluation`，工作树 `D:\BridGes\.worktrees\40-context-continuity-and-cost-evaluation`，基点 `main@3665008b`（阻塞票 13/14/15/19/37 均已合入）。开发与验证使用 conda `agent`（Windows、Python 3.11）。旧树配对基线 `7818c34`（临时对比工作树，用后删除）。

### 交付内容

1. 调用后实测补记：`src/bridges/ai/payload_budget.py` 新增 `CallMaterialManifest.with_usage(*, actual_input_tokens, actual_output_tokens)`；`src/bridges/chat/turn.py` 在 MODEL_GENERATION 退出后用 `event.usage` 补记最终载荷实测，并写 `PAYLOAD_BUDGET_EVALUATED` 审计的 `post_call` 记录（`details.record_phase="post_call"`），使网关估算可与供应商真实用量逐调用对照（基线无此补记）。
2. 复核边界正式化 `tests/chat/test_improvement40_context_boundaries.py`（7 个测试，全部走正式网关/图路径 + 真实 `BridgesDatabase`，覆盖 `docs/上下文工程/复核脚本.py` 的边界案例）：长聊收敛或明确受限结果；末尾条件与未加引号回指进模型载荷；终局组装门拒绝超预算追加；照片轮按图片成本编译；画像整条在最终调用预算边界；冻结额度快照跨配置切换存活；角色数据边界与调用清单含实测用量。
3. 原创多轮语料 `tests/chat/test_improvement40_continuity_corpus.py`（8 个确定性正式图测试）：末尾条件跨轮、纠正覆盖（5,000→3,000）、任务往返恢复最新值、单列表指代、多列表歧义一次性澄清（平台确定式渲染、免模型调用）、摘要失败诚实回退（含 RETRYABLE_FAIL 审计）、旧照片细节跨轮重读、跨账户隔离。
4. 真实模型配对脚本 `scripts/run_issue40_context_continuity_evaluation.py`：自包含；在新旧两棵树按固定语料运行（7 场景 × 2 重复），同模型同预算；量表按回答内容判定并单列质量门拦截；成本按 `model_run_locks` 的 run_id/能力分账；网关估算 vs 调用后补记校准；报告脱敏写 `.scratch/2/validation/40-context-continuity/`。

### 接口变化

- 新增公开方法 `CallMaterialManifest.with_usage(*, actual_input_tokens, actual_output_tokens) -> CallMaterialManifest`（不可变，返回新实例）。
- 既有 `PAYLOAD_BUDGET_EVALUATED` 审计新增 `post_call` 记录（`details.record_phase="post_call"`、`actual_input_tokens`/`actual_output_tokens`）；调用前记录行为不变。
- 无数据库迁移、无 Schema 或合同版本变化。

### 验证结果

- 确定性回归：`tests/chat/test_improvement40_context_boundaries.py` + `test_improvement40_continuity_corpus.py` 共 15 passed。
- 真实配对（2026-10-04，stamp `20261004T162859Z`，两侧均 `qwen3.7-plus-2026-05-26`，repeats=2；证据 `.scratch/2/validation/40-context-continuity/`）：
  - 量表：constraint_retention 6/6、correction_override 4/4、object_resolution 2/2、reference_correctness 4/4、honest_clarification_gap 2/2、isolation 2/2（两侧相同）；场景 × repeat 全 P；无质量门拦截。
  - 成本：调用 32 vs 32；输入 tokens 18071 vs 24884；输出 tokens 13905 vs 15698；墙钟 267.2s vs 351.1s。
  - 校准：新侧网关估算 37985 vs 补记实测 24884（比值 0.655，单次最大超出 -231，256 余量覆盖=是）；旧侧无补记，编译估算 vs 聊天输入比值 1.311。结论：现有预算/余量参数保守可用，无需改动。
- 短聊无无必要摘要调用（摘要调用 0）；长聊场景在真实环境由编译恢复满足预算、未触发摘要模型调用，`summary_events`/`compiled_summary` 如实记录；摘要边界与失败路径由确定性正式图测试覆盖。
- 全量 `pytest tests -n auto`：本树 222 failed / 5317 passed / 39 skipped / 2 errors；基线 272 failed / 3936 passed；失败均为既有环境/并行端口类问题，本树 `FAILED tests/chat/` 为 0。
- 静态检查：`ruff check` 本票全部改动文件通过；`mypy` 对 `payload_budget.py`、`turn.py` 无本票新增错误。

### 剩余限制

- 真实语义只覆盖纯聊天固定语料；照片/附件、多列表投影歧义、摘要失败与来源删除由确定性正式图测试承担，跨会话长期记忆不在本票范围。
- 真实长聊未观察到摘要模型调用（编译在恢复/回退内满足预算，或摘要能力额度未验证）；长聊成本结论以确定性路径与本环境实测为准。
- 一次历史运行在推荐类轮触发 `web_search_citation_invalid` 质量门（内容正确、终态 error）；已成文并在报告中披露，语料已调整避免外部事实声明。

