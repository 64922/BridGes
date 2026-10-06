# 38 — 在正式界面展示真实进度、可信结果与恢复操作

**What to build:** 主对话显示真实能力与简短进度，合格结果可逐步呈现，待输入/部分/阻塞/取消可理解，刷新重连状态一致。

**Blocked by:** 06 — 统一流式正文、终态存储与断线重放；22 — 统一画像用途与表达策略采用快照；23 — 覆盖固定文案、澄清、进度与错误提示；36 — 按本次表现总结并单独恢复总结失败；37 — 执行跨模块依赖计划并统一核验综合结果

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

事实草稿流出后再用结尾修正不能补救；用户应看到实际路由、有效部分和可执行恢复，不读内部控制细节。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。
- [docs/用户画像/提取时机与回答应用流程.md](../../../docs/用户画像/提取时机与回答应用流程.md)。

## 任务内容

1. 请求模块提示与实际执行能力分别投影，模式仍只读；菜单保留，建议点击绑定任务版本，历史标识不重写。
2. SSE 报实际节点、真实已核实数据和通过质量门结果块；待核验事实草稿内部保存不发布。轻量聊天沿用 06 的正文流式。
3. 最终完成只在原子提交后发出，澄清成功消息对应待输入任务，部分结果/阻塞/取消和运行状态/可信状态分开呈现。
4. 采用 23 自然文案说明缺口与真实恢复，停止后不自动续跑，用户明确继续创建新运行；有真实恢复时间才按用户时区展示。
5. 来源、查询原话/实际词、阅读范围和最小上下文采用说明复用既有详情/渐进披露，不增加调试工作台或思维链。
6. 直接 API、刷新和游标重放不重复消息/附件/判定，不泄露未来题/私有评分依据；新旧结果投影兼容。
7. 检查正式图分派的全部生成/模板路径覆盖，复用一致表达/画像快照和真实工具/错误信号；内部分析不用聊天人格。
8. 桌面三个既有验收视口测试交互、键盘操作与停止，UI/事件/落库正文一致。

## 跨票接缝与责任

拥有公共结果/进度前端与实际路径覆盖；领域票提供真实投影，06 拥有正文事件协议，20 拥有画像依据页面。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 用户可见实际能力、来源和读取层次，历史 module_id 不重写。
- [x] 待核草稿不流出；通过门结果块和轻量正文使用正确协议。
- [x] 完成/待输入/部分/阻塞/取消及恢复操作真实，停止后无新调用。
- [x] 浏览器刷新、重放、终态加载与存储一致，无未来题/提前答案泄露。
- [x] 普通聊天、学习辅导/题目/判定/总结、论文/GitHub 解读、其余模板/澄清/错误/进度全部有正式路径接入证据。
- [x] 三个桌面视口与真实 API/执行器测试通过，中文文案和数据语义准确。

## 验证与交付证据

按来源清单和状态矩阵跑正式浏览器端到端，覆盖局部失败/断线/取消/恢复与私有字段；不以旧 TurnOrchestrator 单测替代现行图验收。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 实施记录（2026-10-05）

分支 `codex/38-trusted-progress-and-result-projection`，worktree
`.worktrees/38-trusted-progress-and-result-projection`，基点
`main@9f27c49bbf0eb7113a06a2910320df89bfce32c7`（前置票 06/22/23/36/37
已合并并独立验收）。

### 实现说明

- **回合结果投影（读取时确定性推导）**：新增 `chat/turn_result.py`，按消息
  终态、错误码、路由快照、六个领域投影与运行等待缘由推导 `TurnResultProjection`
  （交付分类/可信状态/请求与实际能力/已交付与被阻塞块/缺口/真实恢复/等待）。
  历史消息 `turn_result` 列为空时读取推导，零回填迁移。固定文案全部来自
  `state_copy` 注册表（工单 23），不调用模型、不读取证据原文。
- **复合运行精确结果（提交事务内）**：`orchestration/production.py` 新增
  `turn_result_for_outcome`，把复合结果收敛为公开交付面：只有
  `COMPLETED 且 trust_state=qualified` 的步骤进入已交付块；未合格完成/失败/
  阻塞/失效步骤只作为阻塞项与真实原因；`graph.py::_persist_composite_result`
  在同一守卫事务内把它与正文、领域投影、终态一起提交。设计（待核验）草稿
  不进入任何投影输入。
- **领域状态映射**：`clarification→needs_input`、`error→failed`、
  `stopped→cancelled`、`links_only/metadata_only/unverified→partial+evidence_bound`、
  成功→`complete+qualified`；不可恢复全部失败→`blocked`。恢复方式经登记的
  `error_recovery` 分类，仅 `web_search.cooldown_until` 为真实可用时刻。
- **前端正式路径**：新增 `TurnResultCard`（交付分类/可信状态/能力/已交付/
  未完成/缺口/恢复），在 `MessageList` 随权威历史渲染；`chat-thread.tsx`
  修复停止消息被折叠成普通完成的问题并透传 `turn_result`；`page.tsx`
  `load()` 不再无条件清空进行态，避免轮询/错误收敛刷新触发重复订阅与正文
  回退（仍有活跃运行的同一消息保留既有订阅，从服务端游标续读）。
- **接口/迁移变化**：`contracts/chat.py` 新增 `TurnOutcome`、`ResultTrust`、
  `TurnResultBlock`、`TurnRecoveryProjection`、`TurnResultProjection`；
  `ChatMessageProjection` 新增 `turn_result`。数据库迁移 69：
  `messages.turn_result TEXT`（SCHEMA_VERSION 69）。导出/删除走
  `lifecycle/catalog.py` 的 messages 通用表序列化，备份为整库快照，新列自动
  覆盖，无需额外登记。OpenAPI 与 `packages/contracts/src/generated.ts` 已再生成。
- **SSE 协议**：不新增正文事件类型（06 拥有）；结果块在原子提交后经
  `done` 事件载荷/权威历史发布，进度沿用真实 `node`/`stage` 事件。

### 验证结果

- 后端单测 `tests/chat/test_issue38_turn_result.py`：**13 passed**（推导映射、
  受阻/恢复/冷却时刻、复合门合格块、持久值优先+路由补齐、损坏载荷回退、
  迁移 69 旧库保留、终态事务内持久化与对话读取）。
- 前端单测（vitest）：`TurnResultCard.test.tsx` + `chat-thread.test.tsx`
  全量 **229 passed**；`npm run typecheck` 仅剩 main 既有测试类型错误
  （与 main 同）；`npm run lint` 无新增；`npm run build` 成功。
- E2E `apps/web/e2e/issue38-trusted-progress.spec.ts`：**6 passed**
  （1280×720 / 1440×900 / 1920×1080 各 2 条）——终态历史结果卡与键盘可达的
  继续入口；真实 API+执行器+SSE 流式进度、Esc 停止、取消投影、停止后无新
  调用、刷新一致与三视口无横向溢出。
- 回归：`tests/contracts`+`tests/storage` **109 passed**；`tests/orchestration`
  **46 passed**；`tests/chat` 49 failed 与本机 `main` 失败清单逐项一致（旧
  插件/MCP/生涯测试与 main 同）；其余目录按目录执行，失败集合与 `main`
  基线一致（security 比 main 少 1 条 flaky）；`tests/integration` 排除本机
  桌面凭据导致的 2 条挂起用例后 **3 failed / 275 passed / 26 skipped**，
  与 main 的 3 条真实失败一致。
- 环境：conda `agent`；Windows/PowerShell。

### 剩余限制

- 结果块只在终态原子提交后发布，未增加每步渐进 `result` SSE 事件（执行器
  多线程下可能先于提交发布未过门结论）；流式进度仍由真实 `node`/`stage`
  事件承担。
- 单模块路径的可信状态按领域终态分类推导（仅显式 `success` 视为合格，
  `empty` 归入未完成，其余与未知状态按证据绑定），复合路径以门与
  `trust_state` 为准；未新增独立可信元数据。
- 历史 `module_id` 只读展示，建议点击绑定任务版本；本票不重写历史标识。
- `tests/integration` 的
  `test_start_fails_with_empty_global_key_before_spawning` /
  `test_start_fails_when_global_key_env_absent` 在本机因桌面数据目录已有
  可用凭据而进入真实启动监督（main 同样挂起；在 main 基线运行中因锁占用
  快速失败），按环境限制排除。
- 确定性脚本适配器只证明机制；真实模型体验与外部来源可得性按评测票验证。

### 代码评审批次与修复（2026-10-05，提交 2–4）

两轴评审（standards/spec）确认并修复：

- **流式消息误投影**：`_turn_result_projection` 增加终态守卫，`streaming`
  消息不发布结果投影，最终完成只在原子提交后出现。
- **空结果/未知状态分类**：`empty` 归入未完成阻塞项；只有显式 `success`
  视为已核验，未知状态按证据绑定交付，不再冒充合格。
- **固定文案注册**：新增 `chat.result.blocked_detail` 取代 `turn_result.py`
  与 `production.py` 两处硬编码回退；移除 `MessageList` 中与
  `chat.result.outcome.cancelled` 重复的硬编码停止行，停止说明统一由结果卡
  注册文案呈现。
- **单一源与精简**：模块短标签复用 `COMPOSITE_MODULE_LABELS`；移除推导入口
  未被调用的推测参数（task/version/gaps）。
- **前端表达**：复合步骤状态补中文短标签（completed/failed/blocked/
  invalidated）；冷却未到时禁用「继续生成」，到点后恢复可用。
- **复核澄清**：建议点击的任务版本绑定已由既有 `_bind_suggestion_to_task`
  （工单 12）实现（冲突返回 `task_state_conflict`），非本票缺口；
  逐步 `result` SSE 仍为记录在案的剩余限制。
- **修复后回归**：`tests/chat` 49 failed（与当前 main 逐一相同）/1351 passed
  （含本票 16 条）；`tests/orchestration` 46 passed；`tests/contracts`+
  `tests/state_copy` 33 passed；前端单测 231 passed；typecheck 仅 main 既有
  错误；lint 无新增；`npm run build` 成功；e2e 6 passed（39.1s）。

## 独立验收（2026-10-06）：通过

原交付 `f2fb5c9e` 独立两轴初审不通过。修复 `ced063ae` 后复验达标；
完整规范轴、需求轴、逐项 AC、真实路径、失败基线及限制见
[独立验收记录](../validation/38-acceptance/README.md)。

上方实施记录为历史事实，其中“逐步 result SSE 尚未实现”和“专业路径证据不足”
已在本次验收关闭：增加质量门 Claim 的原子快照/游标事件、学习终态冻结，
并补无浏览器响应 mock 的论文/GitHub/学习三视口真实 HTTP/执行器/现行图验收。
另外修复资料候选可信状态、澄清刷新和首轮续接、总结失败恢复与反馈正文可见性、
冷却到期和错误原文/控制节点公开问题。

最后学习接缝 337 passed，冻结核心 14 passed，新列生命周期 1 passed，
前端 241 passed，浏览器 30 个独立用例通过，最后学习三视口再次通过。
全量分支 5436 passed / 201 failed / 37 skipped；main 5400 / 202 / 37，
无新增失败。Mypy/Ruff/TS 与 main 逐项无新增；lint/build 成功。
两侧相同排除两条本机桌面凭据影响的 runtime_smoke；真实模型体验与外部可得性
继续按评测票验证，不把确定性机制测试当作模型评测通过。

交付：已以 `08a65d19` 无冲突合入 main，合并后 127 项复验通过，经系统代理推送
并核对远端一致；本票工作树、本地分支已按顺序删除，失效 worktree 记录检查及
清理完成。其他任务产物与分支保留，原始证据已校验归档；详情见独立验收记录。

