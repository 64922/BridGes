# 01 — 记录已确认合同替代与增量迁移接缝

**What to build:** 实施者能依据一套明确的生效与迁移关系开展改进，旧消息、运行和结果保持可解释；本票只整理正式合同与兼容接缝，不提前实现所有能力。

**Blocked by:** 无 — 可立即开始

**Status:** ready-for-agent

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

- [ ] 合同变更矩阵覆盖上述八类 V2 变化，明确目标行为、旧数据解释和负责票。
- [ ] 模式固定、单会话单教材小节、账户/附件分域、原子画像编辑权威、删除墓碑、历史可读导出与退役能力边界均保留。
- [ ] 画像未决细节保持待定并指向 02；不引入旧 WorkOrder 的项目审批流程。
- [ ] 文档链接和术语一致，设计目标与当前已实现状态分开陈述。

## 验证与交付证据

逐项对照 D01–D17、上下文决策 1–9、人味化 D1–D6 与画像 D01–D09；检查正式文档的替代链和相对链接。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

