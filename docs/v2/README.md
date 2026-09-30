# BridGes V2 设计与交付索引

> 状态更新（2026-09-27）：V2 的六个日常模块、书页学习、会话附件、原子画像与凭据／主模型设置已接入正式代码；Issue 21 已记录旧入口退出和桌面验收。此目录保留 2026-09-24 的设计基线，文中“当前”“待实施”等表述应按设计时点阅读；安装和当前使用说明见[项目 README](../../README.md)。外部服务可得性仍须按各模块实测，不能以设计获批或本地测试代替上线门。
>
> 日期：2026-09-24。目标运行环境：个人电脑上的桌面浏览器；开发使用 conda `agent` 环境。

## 阅读顺序

1. [产品契约](product-contract.md)：用户、范围、模式、模块和退出边界。
2. [桌面交互](interaction.md)：页面、控件、状态与错误处理；可验收状态由获批交互脚本 `apps/web/e2e/issue21-desktop-acceptance.spec.ts` 核对。
3. [工作流](workflows.md)：六个日常模块与学习模式的节点、澄清门和输出合同。
4. [可行性与上线门](feasibility.md)：各模块的真实可得性、风险与降级。
5. [技术架构](architecture.md)：LangGraph、会话上下文、存储、画像、知识库、模型与凭据。
6. [迁移与交付](delivery-plan.md)：分期依赖、验收场景、旧功能退出和票据草案。

## 决策效力

本轮经用户确认的 V2 行为覆盖旧 [ADR-0026](../adr/0026-frozen-product-contracts-and-migration-gates.md) 中与之冲突的产品行为，包括纯自然语言能力路由、禁止聊天附件、四类画像、文章人味化与旧学习计划。账户隔离、证据真实性、密钥不泄漏、历史消息保留、迁移可回滚等不冲突的工程约束继续有效。

桌面原型已获批，替代范围由 [ADR-0030](../adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md) 固化。对应纵向切片通过验收前，不把原型当生产实现；旧 ADR 继续描述当前代码的历史合同。

## 同步说明（2026-09-30，票 01）

工作流、上下文、人味化与画像定稿修改了若干 V2 合同。正式替代关系、旧数据解释与负责票记录在 [ADR-0033](../adr/0033-confirmed-workflow-contract-replacements-and-incremental-migration.md)；本目录保留 2026-09-24 设计基线，下列条款按 ADR-0033 的目标行为执行，由对应实现票验收：

| 本目录的旧条款 | 已确认目标行为 | 负责票 |
| --- | --- | --- |
| [产品契约](product-contract.md) §2、[工作流](workflows.md) §1、[技术架构](architecture.md) §2.1 逐消息 `module_id` 固定派发 | 保留请求中的模块提示，正文明确时按正文路由，另记实际路由来源（D01、D15） | 12 |
| [产品契约](product-contract.md) §2 未选模块只建议启动 | 明确意图可直接启动或组合模块，歧义仍澄清一次（D01、D03） | 12 |
| [工作流](workflows.md) §1、[技术架构](architecture.md) §2.1 模块自行结束消息、模块内串行黑盒 | 显式持久节点与完成收据，模块返回产物、共享内核统一收敛（D06、D17） | 10、12、37 |
| [工作流](workflows.md) §1、§2 原词硬覆盖 | 证据约束的语义匹配加确定性条件过滤（D10、D11） | 15、24–28 |
| [产品契约](product-contract.md) §2、[工作流](workflows.md) §4 固定 2 书 3 视频 | 目标驱动的精简学习路径（D12） | 25 |
| [产品契约](product-contract.md) §3、[工作流](workflows.md) §8 作答后即时生成评分标准 | 出题前建立并核验私有评分要点（D09） | 33、34 |
| [技术架构](architecture.md) §2.1 日常/学习分别维护执行规则 | 共享执行内核，模式策略与领域状态分别管理（D17） | 10、12、37 |
| [工作流](workflows.md) §5 统一贴吧检索主线后再补官方核验（执行顺序调整） | 按规定/体验/混合问题分别安排来源顺序与核验（D14） | 27 |

上述变化尚未实现；本目录不因此成为已上线能力的描述。画像合同替代由 [ADR-0032](../adr/0032-profile-facts-controls-and-history-suppression.md) 记录，不在上表重复。

## 外部依据

- [LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)与[子图](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)：会话级检查点、阶段暂停和模块子图的设计依据。
- [百炼查询模型列表](https://help.aliyun.com/zh/model-studio/list-models)：按精确模型 ID 查询模态、能力、上下文长度，并结合真实调用验证。
- 各检索提供方的接口与可得性见[工作流](workflows.md)末尾。外部接口、费率和条款会变化，实施时复核。
