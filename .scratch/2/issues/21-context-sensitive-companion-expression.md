# 21 — 按任务、边界与前文适配有分寸表达

**What to build:** 日常回答具体回应用户请求与交流意愿，倾诉可陪聊或轻问，详细任务不被默认简短截断。

**Blocked by:** 04 — 守住最终模型载荷预算与材料权威边界；05 — 按保留意图绑定事实片段，修复盲替换；11 — 解析任务指代并补回必要原文

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

情绪关键词误判话题、引语和否定；固定确认收尾及必须行动与用户认可的伙伴定位冲突。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/人味化/README.md](../../../docs/人味化/README.md)。
- [docs/人味化/讨论记录.md](../../../docs/人味化/讨论记录.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/人味化/审查与改进建议.md](../../../docs/人味化/审查与改进建议.md)。
- [docs/人味化/复核脚本.py](../../../docs/人味化/复核脚本.py)。
- [docs/人味化/复核结果.json](../../../docs/人味化/复核结果.json)。

## 任务内容

1. 沿现有轻量策略编译少量本轮交流约束，优先级为事实/权限/任务合同与当前明确要求，再是默认偏好；新策略版本保存快照，失败使用简洁安全基线。
2. 识别不想建议/不要安慰/只给答案/详细讲/别追问等强限制，区分引用、翻译和文章材料中的情绪与用户状态。
3. 利用 11 的当前任务、相关前文和纠正；“继续”“你漏答了”接回真实请求，不复制完整历史另建情绪块，不额外调用分类模型。
4. 保留 ChatResponseForm 为粗粒度审计，弱分类不强制安慰/建议/提问；主要任务可结合可选具体承接，焦虑又请求排查应完成排查。
5. 去掉解释必问解决与情绪必给行动，任务完成可自然结束；倾诉先具体承接，按语境陪聊或轻问，不固定问“聊聊还是建议”。允许有依据不同意见，不认同错误事实或自我贬低，不伪造经历/关系。
6. 篇幅和结构服从真实任务；核对输出额度与网关约束，显式长文/推导采用合理任务上限并受 04，而非无界提高所有回复。
7. 在正式普通聊天链接入实际工具成功/部分/错误和有策略依据的拒答信号；未发生的模型拒答不能在生成前假定。对后续模块提供同一表达约束接口。

## 跨票接缝与责任

本票拥有共用行为策略和普通聊天接入；22 统一画像采用，23 固定模板，24–36 在已有生成调用接入。策略回滚与 05/06 缺陷修复解耦。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 5 公里直接给 5000 米；倾诉不强制建议；概念焦虑/翻译引语不推断用户情绪。
- [x] 混合意图完成排查/建议主请求，用户感谢已解决时不机械追问。
- [x] 跨轮继续和纠正接回遗漏任务，详细推导/两千字任务不会被默认短答规则抵消。
- [x] 真实工具超时/部分/成功信号决定准确说明，失败不伪装成功。
- [x] 新任务用新策略，正常重试保留策略快照；异常降级可完成任务。
- [x] 无人味专属第二次生成或分类调用，规则/夹具原创；事实保护与预算验证通过。

## 验证与交付证据

按人味化反例对策略与正式图路径测试；精确措辞不作为字符串金标准，真实自然度由 39 盲评。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 执行与验收记录

### 2026-10-02：实现与两轴评审修复

- 交付分支 `codex/21-context-sensitive-companion-expression`（工作树 `.worktrees/21-context-sensitive-companion-expression`，基点 main `83c27f2f`）；阻塞票 04 `dec5751b`、05 `fc1a3546`、11 `7ad8bd31` 经 `git merge-base --is-ancestor` 核对已带入 main。
- 环境：Windows + conda `agent`（Python 3.11.15）；确定性替身适配器只证明机制，不声称真实模型体验。
- 轻量表达策略（`src/bridges/chat/lightweight_policy.py`，版本 `global-chat-lightweight-v3`，快照新增 `constraints` 与 `output_tokens` 字段，旧快照反序列化按默认值复用）：
  - 本轮交流约束：`no_advice/no_comfort/answer_only/detail_requested/no_follow_up/missed_part/partial_results/closing` 由确定性正则识别；固定优先级 `missed_part→no_follow_up→no_advice→no_comfort→answer_only→detail_requested→partial_results→closing`，提示词与审计只取前 `_MAX_CONSTRAINT_RULES=3` 条；检测本身保留全部信号，形态与额度不因渲染截断而回退。
  - 引用与否定边界：约束识别先剥离引号内话语，建议/纠错形态只看用户自己的话；“不用详细讲/别讲得太详细/不要展开”不触发 `detail_requested`。
  - 形态：新增 `ChatResponseForm.DIRECT_TASK`；移除解释必问（`no-forced-check`）与情绪必行动（`stay-without-forcing` 取代），新增 `honest-disagreement`、`status-accurate`、`failure-not-feigned`；概念焦虑、翻译引语、感谢收尾与混合意图按实测反例落位。
  - 续接：`continuation_source_text` 有界（最多回看 6 条）定位最近实质用户请求，`detect_turn_constraints(text, continuation_text=...)` 与形态/额度沿用原任务；不复制历史正文进提示词、不发起第二次分类调用。
  - 工具信号：`ToolOutcome`（none/success/partial/error）与 `turn_tool_outcome` 只消费真实投影（web/arxiv/检索/材料缺口）；`turn_tool_refusal` 只认工具权限拒绝；错误/拒答 → `error_refusal`，成功/部分 → `tool_result`。
  - 额度：`output_tokens_for_request` 仅显式详细/长文/推导（`detail_requested`）用有界上限 `EXTENDED_OUTPUT_TOKENS=2048`，其余保持 `DEFAULT_OUTPUT_TOKENS=1024`；仍受工单 04 最终载荷门约束；默认额度单一事实源在轻量策略模块（`turn.CHAT_OUTPUT_TOKENS` 为兼容别名）。
- 正式生成链接入（`src/bridges/chat/turn.py`、`src/bridges/chat/service.py`）：普通聊天在策略编译前汇总工具状态与拒答并写入快照，载荷 `max_tokens` 与上下文输出预留共用快照 `output_tokens`；`compile_turn_context` 优先沿用本轮已固化的完整策略快照额度（旧版完整快照无该字段时按默认额度），未固化时按同一确定性函数计算。重试复用原快照；编译异常静默回退安全基线。策略接口（`GlobalWritingPolicyCompiler.compile` 透传 `continuation_text/tool_outcome/refusal`）供后续模块接入，本票未改动学习/职业等专用路径。
- 两轴评审（标准＋规格并行子代理）发现与处置：①提示词渲染的约束条数与审计快照不一致 → 二者统一取固定优先级前三条，检测保留全量信号；②“不用详细讲”被当长文请求 → 增加否定守卫；③引语中的“别追问/不想听建议”被当本轮限制 → 约束识别剥离引号，形态路由的建议/纠错判断只看用户自己的话；④`output_tokens_for_request` 对短翻译也提额 → 收窄为仅 `detail_requested`；⑤两处 `1024` 常量漂移 → 轻量策略为单一事实源，turn 保留兼容别名；⑥上下文预留与重试快照额度可能不一致 → 预留优先用完整快照；⑦未用的 `max_hops` 参数与不可达约束回退 → 收敛为模块常量与固定优先级；⑧职业规划路径的接线超出本票范围（24–36 各自接入）→ 撤回，保持可回滚。未采纳（记录为判断项）：`turn_tool_outcome` 内聚工具状态分类（新来源状态应在本模块接线）；约束用字符串表 + 枚举 ID（表驱动便于审计）；兼容层保留旧 `tool_error/tool_result` 布尔参数（旧快照与旧调用方）；`compile` 内纯函数重复分类（无模型调用、确定且有界）。
- 已知限制：无引号的第三人称转述（“他说：我不想听建议”）仍可能被识别为本轮约束（有引号形式已覆盖）；续接分类用最近实质用户请求的确定性回看，不消费工单 11 的 `ReferenceResolution.task`（正文补回由工单 11 在编译载荷完成）；真实自然度与真实工具体验按评测票 39/40 验证。
- 验证：
  - 新增 `tests/chat/test_improvement21_contextual_expression.py` 43 项：形态/边界/引语/否定/续接/工具状态/额度/重试快照/父图端到端（`sqlite_app`+`client` 夹具驱动日常父图）与元数据一致性。全部通过。
  - 定向回归：轻量策略、表达任务契约、事实保护、预算（改进 04）、模型额度（改进 03）、指代补回（改进 11）、V2 持久运行、聊天 API 与既有服务测试；除 main 已存在的两项 `test_chat_service` 失败外全绿。
  - `tests/chat` 全量：分支 `100 failed / 869 passed / 1 xfailed`；main 同命令 `100 failed / 826 passed / 1 xfailed`，失败集逐项一致（0 新增失败，+43 为本票新增用例）。
  - 非 chat 全量（排除 `tests/integration/test_runtime_smoke.py`，本机在 main 与分支同样挂起）：分支 `159 failed / 3564 passed / 39 skipped`；main `161 failed / 3564 passed / 37 skipped`；本票失败集为 main 的严格子集（main 多出的两项为 `tests/runtime` 端口占用/重启旅程环境性用例），0 新增失败。
  - `ruff check` 改动文件全部通过；`mypy` 改动文件与 main 同名错误逐条一致（20=20，无新增）；复核脚本重生成 `docs/人味化/复核结果.json`（策略版本 v3、新增续接/工具/否定/引语反例）。
- 状态改 `ready-for-human` 等待人工验收；分支未推送、未合并。


