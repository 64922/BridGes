# 27 — 按规定、体验和混合问题编排贴吧取证

**What to build:** 华东交通大学吧任务依据问题选择取证顺序，规定与经历分别呈现，冲突和未读范围保持可见。

**Blocked by:** 10 — 以校园通勤贯通持久节点、收据与执行内核；12 — 主智能体理解混合入口与跨轮任务关系；15 — 按解析任务选择检索材料与模块上下文；21 — 按任务、边界与前文适配有分寸表达

**Status:** ready-for-agent

**优先级：** P1

## 背景与需求

固定先搜帖后补官方不适合所有问题；标题或摘要不足以支持回复归纳，官方域名也不等于适用。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/daily-workflows.md](../../../docs/workflow/daily-workflows.md)。
- [docs/workflow/discussion.md](../../../docs/workflow/discussion.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/v2/feasibility.md](../../../docs/v2/feasibility.md)。

## 任务内容

1. 登记 parse/plan/search/read/verify_official/synthesize/verify 节点，识别对象、日期/时效和规定/体验/混合类型。
2. 规定类优先核对任务相关官方主体/校区/用途/日期/版本，贴吧提供办理经历；体验类以实际帖子/回复为主，混合两路独立时可并行。
3. 确认公开帖子属于目标贴吧，记录主帖/回复真实文本、时间、楼层和缺失范围；只有搜索摘要时输出线索/链接。
4. 官方与经历冲突先查时间/适用范围，有明确替代关系才取新规定，否则分别引用保留冲突；不多数表决或把经历自动推翻规定。
5. 用户明确只查贴吧时遵守，规定缺官方证据保持未核实；不绕过登录/访问限制、不承诺完整楼层抓取。
6. 归纳中保留场景与不同意见，不声称吧友普遍认为。模板自然说明查询范围/规定/经历/冲突/阅读限制，真实字段保真。
7. 局部失败只阻塞依赖结论，有限补证和总预算共享；完成收据保存取证范围，不把读事件当作已读证据。

## 跨票接缝与责任

拥有贴吧问题分类/来源量表和冲突裁决，23/38 使用实际状态文案；公共读取/核验接缝复用。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 规定/体验/混合三类执行顺序和来源范围符合目标，独立两路共享预算。
- [x] 仅标题/摘要时不总结未读回复、不称共识。
- [x] 官方与帖子冲突按时间/范围检查并如实分列，官方域名不自动放行。
- [x] 只查贴吧限制不被核验节点绕过，访问不可得按真实层次降级。
- [x] 来源归属、日期、楼层、读取范围与最终引用一致，恢复不重复有效取证。

## 验证与交付证据

覆盖 A14–A15 和只查贴吧/官方失效场景；真实回复/官方可得性在 42。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

### 2026-10-03：实现与验证（待独立验收）

实施提交：`5a960f3d`（实现 + 测试 + 生成契约），分支 `codex/27-question-driven-tieba-evidence`（worktree 同号，基于 main `68357a20`）。前置 10/12/15/21 已按实际接口检查：内核持久节点/收据/守卫、主理解混合入口与任务关系、`module_task_context`、表达模板均已在 main 落地并被本票消费。

**节点与配方**

- 登记 7 节点 `tieba.parse → tieba.plan → tieba.verify_official → tieba.search → tieba.read → tieba.synthesize → tieba.verify`（`tieba-research` / `tieba-recipe-v1`，能力版本 tieba-parse-v2…verify-v2）；四个代码质量门 `tieba.attribution_evidence`、`tieba.read_scope_fidelity`、`tieba.official_scope`、`tieba.conflict_disclosure`，`tieba.verify` 的判定由 `_recomputed_verification` 从依赖产物重算，不接受模型自宣。
- 分类只由确定性词表判定：命中官方触发词且含体验信号为混合，仅官方触发为规定，其余体验。规定类官方先行（`official_query` → `official_fallback_query`，逐页适用性核对），体验类不追加官方核验，混合类两路独立。执行采用**串行**（官方子路径先于贴吧子路径）并共享同一条运行账本；工单措辞「可并行」为许可，串行换取确定的查询顺序与可测恢复，`parallel_evidence` 因而恒为 False（计划文本明示两路独立、共享同一运行预算）。

**只查贴吧与官方适用性**

- graph 在派发前从 `run.config.understanding` 读取 `SOURCE_RESTRICTION`（贴吧/吧里/吧内），向服务传 `TIEBA_ONLY_OFFICIAL_BLOCKED`；verify_official 在阻断下零外部调用并保留 `official_blocked_reason` 与未核实说明，来源限制不能被核验节点绕过。
- 官方域名不自动放行：逐页核对主体（命中名词）、点名校区、用途与日期（版本）；页面未点名问题中的校区时 `applicability.applicable=False` 并给出 `official_unverified_note`。
- 冲突只在该官方摘录与真实回复共享名词且回复有相反说法时成立；按时间与适用范围给出 `OFFICIAL_NEWER`（仅在官方含生效/修订表述且年份不早于帖子）或 `KEPT_BOTH`，双方分列，不做多数表决、不让经历自动推翻规定。

**降级、呈现与恢复**

- 仅标题/摘要不进入确认结果与分段：确认依据只有真的读到帖子页面；未读页面按访问受限/超上限等真实层次降级为候选帖链并写明原因；分段引文必须能回溯到已读回复，模板文字不得出现「普遍认为」类共识表述。
- 恢复不重复有效取证：search/read/verify_official 产物持久化在 `NodeKernelRepository`，`_resume_payload` 按 input_key、能力版本与 `partial` 标志续用已完成查询、已读页面与已抓官方页面（官方发现查询会重跑以补未抓候选，但已抓页面不再重复抓取）。提交守卫内的终态经 `finalize_message` 并入同一事务写入。

**接口/合同变化**

- `TiebaQuestionKind` + `KIND_LABELS`、`TiebaEvidenceRoute`、`TiebaEvidencePlan`、`TiebaOfficialApplicability`、`TiebaConflictResolution`/`TiebaConflictPostRef`/`TiebaConflict`、`TiebaVerification`；`TiebaQuestionAnalysis` 新增 `campus_terms`/`question_kind`/`experience_signals`；`TiebaOfficialCheck` 新增 `applicability`；`TiebaResearchProjection` 新增 `question_kind`/`campus_terms`/`source_priority`/`parallel_evidence`/`plan_rationale`/`official_blocked_reason`/`official_unverified_note`/`conflicts`/`verification`（全部带默认值，旧数据可读）。
- 无数据库迁移：贴吧投影仍存在既有消息 JSON 列，`SCHEMA_VERSION` 保持 68；新增持久状态即节点产物/收据，生命周期由既有内核仓库与提交守卫承担。
- `openapi.json` 与 `packages/contracts/src/generated.ts` 已重新生成（311 paths）。重生成同时对齐了 main 上原有的 paper/resources 契约漂移；`tests/contracts/test_openapi_sync.py` 由基线失败转为 2 passed。前端卡片未改动，新字段由生成类型携带，正式呈现由 38 验收。

**验证**（conda `agent`，Python 3.11.15）

- `tests/tieba`：**76 passed / 2 failed**；两个失败（`test_plain_chat_suggests_tieba_without_searching`、`test_other_modules_still_rejected_and_no_silent_search`）在 main 基线逐项复现（本环境普通聊天路由基线）。
- 新增 `tests/tieba/test_improvement27_question_driven.py` 9 项：规定/体验/混合顺序与来源范围、运行账本共享（同一 run 行 4 次外部调用、active 归零）、只查贴吧阻断（零官方抓取且未核实）、官方未点名校区不适用、冲突按时间/范围两种裁决、仅帖链不总结未读回复、恢复复用检索/读取/官方页面。
- `tests/chat tests/kernel tests/contracts`：1165 passed / 50 failed；50 项与 main 基线逐名一致（career/selections/MCP/attachment/openapi 等既有环境失败），无新增失败。
- `tests/api tests/tasks tests/commute tests/github tests/resources`：421 passed / 1 failed（`test_plain_chat_without_the_module_never_starts_github`，在 main 复现）。
- 静态：ruff 变更文件全部通过（其余既有告警与 main 一致）；`mypy src/bridges/tieba --no-incremental` 22 errors / 10 files，与 main 该命令完全一致，tieba 文件 0 error。

**已知限制**

- 真实检索、官方页面可得性与真实模型体验不在本票证明范围，由 42 评测；本票只证明确定性机制与真实产物一致性。
- 官方恢复轮会重跑发现查询（不重抓已取页面），比贴吧检索/读取的「已完成即跳过」保守；如需彻底免重跑需在产物中持久化候选清单，留待后续按需处理。
- 普通聊天的两个 tieba 基线失败与本环境相关，未在本票修复；不改变账户隔离、模式固定、附件/知识库分域与历史可读/导出。

