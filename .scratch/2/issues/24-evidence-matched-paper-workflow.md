# 24 — 论文语义匹配、分层阅读与可恢复交付

**What to build:** 用户按研究问题取得有证据的论文推荐或解释，实际阅读范围明确，选定论文身份可供后续实现搜索。

**Blocked by:** 10 — 以校园通勤贯通持久节点、收据与执行内核；12 — 主智能体理解混合入口与跨轮任务关系；15 — 按解析任务选择检索材料与模块上下文；21 — 按任务、边界与前文适配有分寸表达

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

原词覆盖和综述标签不能证明论文相关或入门适配；父图预算/上下文未贯穿论文独立概述调用。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/daily-workflows.md](../../../docs/workflow/daily-workflows.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/workflow/discussion.md](../../../docs/workflow/discussion.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。
- [docs/v2/feasibility.md](../../../docs/v2/feasibility.md)。

## 任务内容

1. 迁移 paper.parse/plan/search/screen/read/enrich/evaluate/verify 为登记的持久节点；解析主题/领域/具体论文、阅读目标、原话锚点及硬条件，实质领域歧义先问。
2. 精确查询与受约束同义扩展保留原目标；年份/来源等代码过滤，专业角色将需求对应标题/摘要/全文证据，原词不字面命中仍可相关，命中词但无支持者排除/未确认。
3. 登记且可用的 arXiv/Crossref/OpenAlex 等适配器才可使用，不以文档列名新增未实现供应商。学术标识去重，发表/版本/引文补充通常可选。
4. 先标题/摘要/作者/年份筛选，重点候选按结论需要读对应正文。方法/实验/局限/复现断言须有正文，只有摘要交付摘要级概述；引用量不等于质量或入门性。
5. 普通查找保留 3–5 篇习惯，真实不足如实交付；空/相关性不足在整次运行一轮调整内改同义术语/问题/登记来源，不静默放宽年份/指定论文。
6. 输出标识/链接、选择依据、阅读顺序/范围和未确认项；题目/搜索线索单列。选定论文产物保存可核实身份，不靠下个模块猜自然语言摘要。
7. 既有用户可见概述调用使用 21 表达和 04 最终预算，实际输出额度单独预留；模块返回待交付产物，内核核验，独立复核按复杂比较/冲突触发。
8. 旧结果兼容，新产物输入依赖/收据/查询/时间与内容版本进入恢复和失效。

## 跨票接缝与责任

拥有论文领域配方/量表/产物身份；26/37 消费选定论文产物，公共内核和画像接缝保持唯一。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 同义相关候选通过证据匹配，关键词假阳性排除，硬条件不放宽。
- [x] 未读全文不宣称精读，方法与实验断言定位正文，摘要级降级诚实。
- [x] 默认真实数量/空结果/可选 enrich 失败分别交付，必要身份缺失阻塞对应结论。
- [x] 进程恢复只执行未完成有效节点，实际调用清单/预算/表达策略可追溯。
- [x] 论文标识可供 GitHub 精确引用，旧结果可读导出，账户和来源范围受控。

## 验证与交付证据

固定学术响应覆盖筛选/读取/失败与节点故障；真实全文可得性在 42，语义对照在 42。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 执行与验收记录

### 2026-10-03：实现、两轴评审与修复

- 交付分支 `codex/24-evidence-matched-paper-workflow`（工作树 `.worktrees/24-evidence-matched-paper-workflow`，基点 main `806600ce`）；本记录与实现同一提交。
- 内核迁移：新增 `src/bridges/paper/kernel.py`——八节点配方 `paper.parse→plan→search→screen→read→enrich→evaluate→verify`（能力/产物类型/输入键/恢复分支/质量门登记），执行体复用既有解析/规划/来源/排序并接入证据筛选与分层读取；`paper.identity_present` 阻塞身份缺失，`paper.claims_have_evidence`/`paper.hard_conditions_hold` 阻塞无正文依据断言与硬条件违背，独立复核为可选门。
- 证据匹配：新增 `src/bridges/paper/screening.py`——年份硬条件代码过滤、需求→标题/摘要证据匹配（`synonym` 视同相关、`keyword_only` 排除/未确认）、可注入 `judge` 接缝。
- 分层阅读：新增 `src/bridges/paper/reading.py`——按阅读目的选择正文小节、`FullTextReader` 登记接缝、失败/未接入诚实降级摘要级、`supported_claims` 只认实际取得的正文小节。
- 服务与交付：`src/bridges/paper/service.py` 重写为 NodeKernel 驱动；成功/主题不匹配/空/澄清/停止/失败六类收敛复用既有消息终态；嵌套事务规避——最终写入改用 `finalize_message(final_content=...)`，失败路径先在同一事务写 ERROR 终态再在事务外抛 `PaperModuleError`；停止由仍持租约的执行者如实收敛。
- 接缝：`presenting.py` 概述调用承载 Issue 21 表达策略（`ExpressionPolicy`，system_block + 单独输出额度）；`chat/graph.py` 传 `writing_policy` 并把 `PaperSupersededError` 映射为 `DailyGraphSuperseded`；`api/main.py` 注入 `task_version_provider`；`chat/service.py` 显式论文模块派发把主题词包装为可执行计划，并按 `paper→paper_search` 精确别名取任务上下文（不扩大其它模块语义）。
- 两轴评审（标准＋规格并行子代理）发现与处置：
  - 修复：`paper.screen` 未执行冻结的初筛上限 → 按 `PaperBudget.screen_max` 截断并在说明中如实标注（新增回归测试）。
  - 修复：检索首次被预算拒绝时 `assert` 会伪装成节点异常 → 明确返回 `run_budget_exhausted` 阻塞（新增回归测试）。
  - 修复：`paper.claims_have_evidence` 门在真实流程不可达（`unsupported_claims` 恒空、判定条件互斥）→ 「宣称正文级阅读但未定位到小节证据」即阻塞，并保留「摘要范围却带正文断言」的跨版本防线；`passed_checks` 不再写入空字符串（新增回归测试）。
  - 修复：`expression: Any` → `ExpressionPolicy` Protocol；`ranking` 私有 `_matches/_primary_keyword` 公开为 `matched_keywords/primary_keyword` 供 `screening` 使用。
  - 未采纳/后续：`_load_budget` 等与通勤样板重复（内核跨域泛化按 10 号票记录留给后续迁移）；`PaperReadScope` 与产物载荷 `scope: str` 双表示保持现状；最终写事务内只走纯函数停止收敛（与通勤一致）。
  - 规格缺口记为限制：具体论文/来源硬条件未解析（本票文档已不再过度声称，仅年份/综述偏好与已登记来源）；空/不足的一轮调整目前为「去扩展词再召回」，未更换来源；独立复核接缝已测但生产未装配，未执行时如实标注（真机体感按 42）。
- 验证（conda `agent`，Windows）：
  - `tests/paper` 54 passed（`tests/paper/test_paper_issue24.py` 14 项，含三条评审回归）。
  - 定向回归 `tests/paper + tests/chat/test_improvement15_*` 100 passed；`tests/resources + tests/commute + tests/tieba + tests/github + improvement15` 326 passed / 4 failed（4 项均为 main 既有失败）。
  - 全量 `pytest -q`：228 failed / 4872 passed / 39 skipped / 2 errors；与 main `806600ce` 全量（243 failed / 4843 passed）逐项对比：修复 15 项（14 项论文 e2e + 1 项 GitHub 前文论文锚点），**0 新增失败**（首次全量暴露的 GitHub 澄清回归由别名收窄修复并复核）。
  - `ruff check` 本票文件全过（`chat/graph.py` 两处 N818、`api/main.py` 111 条均 main 既有）；`mypy src/bridges/paper` 本票文件 0 错误（其余 20 条为 main 既有）。
- 限制：确定性模型/来源替身只证明机制；真实全文可得性与真实模型概述体验按 42 实测；具体论文解析与来源硬条件未实现（见上）。
- 状态改 `ready-for-human` 等待人工验收。

### 2026-10-03：独立验收与修复

- 独立两轴验收发现并修复 7 项阻塞：同义证据端到端交付、指定论文/来源硬条件、生产 judge/reviewer 装配、概述逐字证据绑定、独立复核触发与裁决健壮性、一轮调整不换源不放宽硬条件、恢复失效策略；新增 `tests/paper/test_paper_issue24_acceptance.py` 31 项回归（含生产装配全链路）。
- 验收验证：`tests/paper` 85 passed；关联回归 384 passed / 4 failed，4 项失败在 main `806600ce` 同名同因复现，main 上另有 15 项论文链路失败由本票修复，0 新增失败；ruff 全过；mypy 本票 0 错误且其余与 main 逐条一致。
- 机制验收通过，详见[独立验收记录](../acceptance/24-evidence-matched-paper-workflow.md)；本节及验收记录取代原交付报告中「具体论文/来源硬条件未解析、独立复核生产未装配」的限制结论。

