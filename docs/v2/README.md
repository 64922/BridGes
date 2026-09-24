# BridGes V2 设计与交付索引

> 状态：产品行为已逐项确认，桌面界面原型已获用户认可；生产代码尚未实现 V2。此目录是后续拆分本地 tickets 的输入。
>
> 日期：2026-09-24。目标运行环境：个人电脑上的桌面浏览器；开发使用 conda `agent` 环境。

## 阅读顺序

1. [产品契约](product-contract.md)：用户、范围、模式、模块和退出边界。
2. [桌面交互](interaction.md)：页面、控件、状态与错误处理；对应 [可交互原型](../../apps/web/prototypes/v2-desktop/README.md)。
3. [工作流](workflows.md)：六个日常模块与学习模式的节点、澄清门和输出合同。
4. [可行性与上线门](feasibility.md)：各模块的真实可得性、风险与降级。
5. [技术架构](architecture.md)：LangGraph、会话上下文、存储、画像、知识库、模型与凭据。
6. [迁移与交付](delivery-plan.md)：分期依赖、验收场景、旧功能退出和票据草案。

## 决策效力

本轮经用户确认的 V2 行为覆盖旧 [ADR-0026](../adr/0026-frozen-product-contracts-and-migration-gates.md) 中与之冲突的产品行为，包括纯自然语言能力路由、禁止聊天附件、四类画像、文章人味化与旧学习计划。账户隔离、证据真实性、密钥不泄漏、历史消息保留、迁移可回滚等不冲突的工程约束继续有效。

桌面原型已获批，替代范围由 [ADR-0030](../adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md) 固化。对应纵向切片通过验收前，不把原型当生产实现；旧 ADR 继续描述当前代码的历史合同。

## 外部依据

- [LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)与[子图](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)：会话级检查点、阶段暂停和模块子图的设计依据。
- [百炼查询模型列表](https://help.aliyun.com/zh/model-studio/list-models)：按精确模型 ID 查询模态、能力、上下文长度，并结合真实调用验证。
- 各检索提供方的接口与可得性见[工作流](workflows.md)末尾。外部接口、费率和条款会变化，实施时复核。
