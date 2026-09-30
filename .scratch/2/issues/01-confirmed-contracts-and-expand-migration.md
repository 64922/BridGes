# 01 — 记录已确认合同替代与增量迁移接缝

**What to build:** 实施者能依据一套明确的生效与迁移关系开展改进，旧消息、运行和结果保持可解释；本票只整理正式合同与兼容接缝，不提前实现所有能力。

**Blocked by:** 无 — 可立即开始

**Status:** ready-for-human

**优先级：** P0

## 背景与需求

工作流已定稿并明确修改显式模块启动等 V2 合同；上下文和人味化也已定稿，画像整体规格仍待细化。先消除实施者读到多份文档后执行相反行为的风险。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/README.md](../../../docs/workflow/README.md)。
- [docs/workflow/discussion.md](../../../docs/workflow/discussion.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/用户画像/讨论记录.md](../../../docs/用户画像/讨论记录.md)。
- [docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)。
- [docs/adr/0031-runtime-qwen-credential-and-main-model-activation.md](../../../docs/adr/0031-runtime-qwen-credential-and-main-model-activation.md)。
- [docs/adr/0011-clean-room-humanizer-and-license-boundary.md](../../../docs/adr/0011-clean-room-humanizer-and-license-boundary.md)。

## 任务内容

1. 新增正式 ADR，逐项记录工作流 D01–D17 的替代关系：自然语言混合启动、正文优先于模块提示、语义证据匹配、资料数量随目标、贴吧按问题取证、评分依据前置、共享内核与模式领域状态分离；同步相关 V2 产品、API、交互及领域术语。
2. 将上下文九项原则、人味化六项原则与工作流统一到同一任务语义。工作流明确批准的混合启动替代旧的显式 module_id 固定派发限制；读取已有材料仍不自动授权刷新外部来源，用户“只查论文／不要联网”等硬限制继续有效。
3. 区分普通回复无额外人味化生成、按需历史摘要、普通画像后台提取、按风险独立核验；按调用目的计数，不能把允许的摘要调用说成人味化二次润色。
4. 列出旧/新消息投影、任务/产物/题目版本的 expand–migrate–contract 兼容方案。先允许新字段与旧投影并存，各模块切片迁移后再收敛写路径；不以全库重写为前置。
5. 逐项列出画像已确认 D01–D09 与未决事项，承接 02；保持讨论原记录，不把尚未确认的建议升级为 accepted。

## 跨票接缝与责任

本票拥有正式合同替代说明；各实现票仍负责自己的接口、迁移和验收。后续票引用新增 ADR 的实际编号，不能预设不存在的文件。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 合同变更矩阵覆盖上述八类 V2 变化，明确目标行为、旧数据解释和负责票。
- [x] 模式固定、单会话单教材小节、账户/附件分域、原子画像编辑权威、删除墓碑、历史可读导出与退役能力边界均保留。
- [x] 画像未决细节保持待定并指向 02；不引入旧 WorkOrder 的项目审批流程。
- [x] 文档链接和术语一致，设计目标与当前已实现状态分开陈述。

## 验证与交付证据

逐项对照 D01–D17、上下文决策 1–9、人味化 D1–D6 与画像 D01–D09；检查正式文档的替代链和相对链接。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 执行与验收记录

### 基线版本与环境

- 分支点 `main` = `d4c16ee`（工作树干净）。实现分支 `codex/01-confirmed-contracts-and-expand-migration`，工作树 `.worktrees/01-confirmed-contracts`（仓库约定 `/.worktrees/`，已 gitignore）。开发与验证使用 conda `agent`。
- 本票只改文档，无业务代码、接口或数据库迁移。

### 交付内容

- 新增 [ADR-0033](../../../docs/adr/0033-confirmed-workflow-contract-replacements-and-incremental-migration.md)：合同变更矩阵（八类 V2 变化 + D14 执行顺序调整）、D01–D17 覆盖表、统一任务语义、调用目的分类、expand–migrate–contract 增量兼容方案、保留不变量、画像 D01–D09 承接。
- 同步 [CONTEXT.md](../../../CONTEXT.md) 领域术语：新增混合启动、模块提示、跨轮任务、任务版本、节点完成收据、共享执行内核，并在能力路由补充「模块提示只作理解提示」。
- 同步 V2 基线：[README](../../../docs/v2/README.md) 增加八行替代表；[产品契约](../../../docs/v2/product-contract.md)、[工作流](../../../docs/v2/workflows.md)、[交互](../../../docs/v2/interaction.md)、[技术架构](../../../docs/v2/architecture.md) 在冲突段落追加「已确认替代（待实施）」说明，保留原文。

### 合同变更矩阵与负责票

八类 V2 变化（逐消息 module_id 固定派发 / 未选模块只建议 / 模块自行结束 / 模块内串行黑盒 / 原词硬覆盖 / 固定 2 书 3 视频 / 作答后即时评分 / 日常学习分别维护规则）在 ADR §1 各行给出目标行为、旧数据解释与负责票；D14 作为执行顺序调整单列第 9 行。负责票引用真实票号（12、10/12/37、15/24–28、25、33/34、27）。

### 调用目的分类

ADR §3 区分普通回复生成（一次生成、无额外人味化追加调用）、按需历史摘要（可调用并缓存，按「摘要」计量）、普通画像后台提取（正常完成后异步）、按风险独立核验（按触发条件）；按目的分组计量，摘要调用不冒充人味化二次润色。

### 增量迁移接缝

ADR §4 给出消息投影、任务/运行/产物版本、学习题目与评分要点版本的 expand–migrate–contract 方案：先新字段与旧投影并存，各模块切片迁移后收敛写路径；不以全库重写为前置。各票在自己的持久状态内完成迁移、备份/导出、删除、审计与失败恢复，不留给 43。

### 画像承接

ADR §6 逐项列出 D01–D09 并指向 02 与 [ADR-0032](../../../docs/adr/0032-profile-facts-controls-and-history-suppression.md)；未决细节保持待定、指向 02，仅剩工程参数由实施/评测票实测。保留 Q09 历史回答，不升级未确认建议；不引入旧 WorkOrder 项目审批流程。

### 验证记录

- 相对链接：conda `agent` 下检查 7 个变更 Markdown 文件、66 个本地链接；除既有断链 `.scratch/final/PRD.md`（未触碰的历史引用，main 上同样缺失）外全部通过。
- 引用完整性：ADR 引用的 ADR 编号（0011/0022/0026/0030/0032/0033）与票号均存在，未预设不存在的文件。
- `git diff --check` 通过；`PYTHONPATH=src pytest tests/contracts -q` **3 passed**（openapi.json 未改，作非回归对照）。
- 纯文档变更，无适用类型检查或新增行为测试；未重跑既有全量测试（本票不触碰业务代码，沿用 02 票的同类限制记录），因此不把历史基线通过数当作本次通过证据。

### 评审与修正（/code-review 两轴）

| 发现 | 轴 | 处置 |
| --- | --- | --- |
| `docs/v2/architecture.md` 仍以 module_id 为固定派发、未加同步说明 | Spec | 已修：§2.1 追加替代说明并纳入 v2/README 同步表 |
| 任务内容点名的「贴吧按问题取证」未进矩阵 | Spec | 已修：新增第 9 行（D14），标注为执行顺序调整 |
| 负责票冲突（ADR 行 4 写 10、37，V2 同步写 10、12、37） | Spec | 已修：统一为 10（通勤试点）、12、37 |
| v2/README 同步表不全（漏未选模块只建议、共享内核两行） | Spec | 已修：补齐为八行 + D14 行 |
| 共享内核、任务版本等核心术语未进 CONTEXT.md | Standards | 已修：新增共享执行内核、任务版本词条 |
| v2/README 多一个空行 | Standards | 已修 |
| 三张表重复陈述替代关系 | Standards（判断项） | 不采纳：三表分别回答「V2 合同变化 / D 决策覆盖 / 旧 ADR 替代」，各有读者，保留交叉引用 |

### 限制与未完成

- 本票只记录合同与接缝，不实现任何 V2 变化；矩阵中的目标行为均为待实施设计。
- API 语义（模块提示 vs 实际路由、任务/产物字段）由 12 等实现票更新 `openapi.json` 与接口测试，本票未改接口。
- 预算数值、语义阈值与外部服务可得性按各票实测，本票不作性能或效果承诺。

## Comments

### 2026-09-30：交付（合同替代与增量迁移接缝）

- 以 worktree `codex/01-confirmed-contracts-and-expand-migration` 交付，实现提交 `a9132b9`；随后按两轴评审修正并补记录。
- 交付 ADR-0033、CONTEXT.md 术语同步与 V2 基线同步说明；本票只整理合同与接缝，未实现能力，状态改 `ready-for-human` 等待人工验收。
- 验证见上节；相对链接、引用完整性与 `git diff --check` 通过，`tests/contracts` 3 passed。既有断链 `.scratch/final/PRD.md` 为未触碰的历史引用，未在本票改写。

### 2026-09-30：合并、推送与工作树清理

- 合并：`main` 基于 `d4c16ee`，`git merge --no-ff` 合入 `cc40a53` 得合并提交 `0e9ba90`，**无冲突**；合并树 `61152770` == 分支树 `61152770`，故分支上的全部验证结论对 `main` 适用。
- 推送：`git push origin main` → `3d0c983..0e9ba90`；`origin/main` 与本地 `main` 一致（0/0）。
- 清理：`git worktree remove .worktrees/01-confirmed-contracts` 成功（工作树干净、无残留目录）；`git branch -d codex/01-confirmed-contracts-and-expand-migration` 删除（安全删除通过，3 个提交经合并提交可达）；`git worktree prune` 无失效记录。
- 另发现并清理遗留空目录 `.worktrees/02-generation-stop-and-graph-errors`（不在 `git worktree list` 中、无 `.git`、内容为空），用 `rmdir` 删除（非空会失败，零数据风险）。清理后 `.worktrees/` 仅剩 `03-model-quota`、`05-intent-bound-fact-protection` 两个有效工作树。


