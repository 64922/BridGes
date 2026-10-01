# BridGes 改进 tickets 与并行执行建议

日期：2026-09-30。审查与当前代码基线：`7818c34`。本批共 **43 张 ticket**，放在 [issues](issues/)；结构借鉴 [.scratch/bridges-v2/issues](../bridges-v2/issues/README.md)。本文和 [COVERAGE.md](COVERAGE.md)是执行说明，不计入票数。本次仅拆票，未改业务代码或声称能力已经实现。

用户在本次拆票过程中确认：**保持可独立验收的功能各一票；画像先建立规格确认票，后续画像实现依赖它。** [02](issues/02-profile-specification-decisions.md) 因而保持 `needs-info`。其余 42 张标为 `ready-for-agent`，含义是描述完整，仍须全部 `Blocked by` 已实施并验收才能领取。创建票据不代表批准画像未决选项。

**2026-09-30 后续交付：** 上段记录拆票时状态；02 已完成 R01–R08 及用户整体确认，规格验收完成，状态改为 `ready-for-agent`。正式依据为 [profile-contract-v1](../../docs/用户画像/画像规格与验收合同.md)和 [ADR-0032](../../docs/adr/0032-profile-facts-controls-and-history-suppression.md)；仅解除 02 的规格依赖，不代表后续画像业务已实现。

## 范围与合同协调

四个目录的 **21 个文件**已全部阅读：人味化 6、上下文工程 4、用户画像 4、workflow 7，包括三个只读脚本及人味化复核 JSON。逐文件、逐决策、逐缺陷与 39 个 A/L/R 场景映射见 [覆盖清单](COVERAGE.md)。重复要求只指定一个负责票，各消费者在自己的纵向切片中接线验收。

- **路由合同**：workflow D01/D15 明确批准混合启动、正文优先。[01](issues/01-confirmed-contracts-and-expand-migration.md) 记录对旧显式 module_id 限制的正式替代，[12](issues/12-main-agent-hybrid-entry-and-task-relations.md) 实施；上下文文档涉及的旧派发限制按这项有明确替代说明的工作流定稿协调。读取已有材料仍不自动授权新目标或外部刷新，用户“只查论文／不要联网”始终是硬条件。
- **调用分工**：人味化在原用户可见生成中适配，固定说明用模板，零人味专属追加调用；历史摘要可按需调用并缓存，普通画像提取回答后异步，专业/独立核验按任务与风险启用。调用目的分别计量，普通陪伴保持轻量一次生成。
- **画像确认**：D01–D09 保留，02 已补齐 R01–R08 并整体定稿。[07](issues/07-profile-controls-and-immediate-commands.md)、[16](issues/16-atomic-fact-identity-and-coexistence.md)–[20](issues/20-profile-evidence-disclosure.md) 与消费者采用 profile-contract-v1；其他直接/传递前置仍按实际实现验收检查。
- **共同状态**：[08](issues/08-source-bound-task-state-and-waits.md) 拥有有来源的有效任务状态，[11](issues/11-reference-resolution-and-original-recovery.md) 定位对象，[13](issues/13-bounded-summary-cache.md) 摘要仅作派生线索。画像治理沿用已有跨会话长期信息，上下文本次不扩展跨会话召回。
- **兼容迁移**：新合同/投影先与旧读取并存，模块分批迁移后 [43](issues/43-integrated-migration-and-release-regression.md) 收口。保持账户隔离、模式固定、单教材小节、附件/知识库分域、编辑权威/墓碑、历史可读导出；退役创作/媒体/提醒/用户插件/项目工作台保持退役。

## 领取与交付规则

1. 完整阅读票内必读文档、[AGENTS.md](../../AGENTS.md)、[CONTEXT.md](../../CONTEXT.md)与本 README。先检查阻塞票的实际实现、验收和落地接口，不能只看编号或状态。
2. 在 conda `agent` 开发/验证，每票交付可验证的纵向路径：需要的数据、服务、API、正式界面和适用测试一起落实。内部节点通过真实调用/收据/恢复验证，不以函数支持参数证明接线。
3. 新增持久状态在自己票内完成适用的版本化迁移、备份/导出、删除、审计、失败恢复；最终票只复核整体，不补前票欠缺。
4. 完成后在票末记录代码/合同版本、接缝、迁移、实际验证和限制。沿用本仓库五种分诊状态；02 得到确认后再改 ready-for-agent，验收记录说明前置是否完成。
5. 复核脚本/历史通过数是合成或既有基线；真实模型收益、浏览器生命周期、外部服务可得性分别验证。密钥和完整私人正文不进票据/普通日志，工程阈值先测基线。

## 完整票据与硬依赖

| 编号 | 交付 | Blocked by | 初始状态 |
| --- | --- | --- | --- |
| [01](issues/01-confirmed-contracts-and-expand-migration.md) | 记录已确认合同替代与增量迁移接缝 | 无 | ready-for-agent |
| [02](issues/02-profile-specification-decisions.md) | 补齐画像剩余规格与验收决策（规格已验收） | 无 | ready-for-agent |
| [03](issues/03-model-quota-and-call-snapshots.md) | 锁定完整模型额度与每次调用版本 | 无 | ready-for-agent |
| [04](issues/04-final-payload-budget-and-data-boundary.md) | 守住最终模型载荷预算与材料权威边界 | [03](issues/03-model-quota-and-call-snapshots.md) | ready-for-agent |
| [05](issues/05-intent-bound-fact-protection.md) | 按保留意图绑定事实片段，修复盲替换 | 无 | ready-for-human |
| [06](issues/06-stream-replay-and-terminal-consistency.md) | 统一流式正文、终态存储与断线重放 | [05](issues/05-intent-bound-fact-protection.md) | ready-for-agent |
| [07](issues/07-profile-controls-and-immediate-commands.md) | 分开画像记录与使用控制，保证即时撤回 | [02](issues/02-profile-specification-decisions.md) | ready-for-agent |
| [08](issues/08-source-bound-task-state-and-waits.md) | 保存有来源的跨轮任务、有效条件与澄清 | [01](issues/01-confirmed-contracts-and-expand-migration.md) | ready-for-agent |
| [09](issues/09-shared-persistent-run-budget.md) | 持久化整次运行预算与有限调整额度 | [03](issues/03-model-quota-and-call-snapshots.md)、[08](issues/08-source-bound-task-state-and-waits.md) | ready-for-agent |
| [10](issues/10-resumable-commute-kernel-pilot.md) | 以校园通勤贯通持久节点、收据与执行内核 | [04](issues/04-final-payload-budget-and-data-boundary.md)、[08](issues/08-source-bound-task-state-and-waits.md)、[09](issues/09-shared-persistent-run-budget.md) | ready-for-agent |
| [11](issues/11-reference-resolution-and-original-recovery.md) | 解析任务指代并补回必要原文 | [04](issues/04-final-payload-budget-and-data-boundary.md)、[08](issues/08-source-bound-task-state-and-waits.md) | ready-for-agent |
| [12](issues/12-main-agent-hybrid-entry-and-task-relations.md) | 主智能体理解混合入口与跨轮任务关系 | [01](issues/01-confirmed-contracts-and-expand-migration.md)、[04](issues/04-final-payload-budget-and-data-boundary.md)、[08](issues/08-source-bound-task-state-and-waits.md)、[09](issues/09-shared-persistent-run-budget.md)、[10](issues/10-resumable-commute-kernel-pilot.md)、[11](issues/11-reference-resolution-and-original-recovery.md) | ready-for-agent |
| [13](issues/13-bounded-summary-cache.md) | 后台生成有界摘要并按来源缓存 | [04](issues/04-final-payload-budget-and-data-boundary.md)、[08](issues/08-source-bound-task-state-and-waits.md)、[09](issues/09-shared-persistent-run-budget.md)、[11](issues/11-reference-resolution-and-original-recovery.md) | ready-for-agent |
| [14](issues/14-multimodal-and-saved-evidence-reuse.md) | 统一照片预算与旧附件、原图、证据读取 | [04](issues/04-final-payload-budget-and-data-boundary.md)、[11](issues/11-reference-resolution-and-original-recovery.md) | ready-for-agent |
| [15](issues/15-task-aware-retrieval-and-module-context.md) | 按解析任务选择检索材料与模块上下文 | [04](issues/04-final-payload-budget-and-data-boundary.md)、[11](issues/11-reference-resolution-and-original-recovery.md)、[14](issues/14-multimodal-and-saved-evidence-reuse.md) | ready-for-agent |
| [16](issues/16-atomic-fact-identity-and-coexistence.md) | 以完整事实身份保存画像并处理并存更新 | [02](issues/02-profile-specification-decisions.md)、[07](issues/07-profile-controls-and-immediate-commands.md) | ready-for-agent |
| [17](issues/17-asynchronous-evidence-based-profile-extraction.md) | 回答后异步提取有精确证据的完整事实 | [02](issues/02-profile-specification-decisions.md)、[03](issues/03-model-quota-and-call-snapshots.md)、[04](issues/04-final-payload-budget-and-data-boundary.md)、[16](issues/16-atomic-fact-identity-and-coexistence.md) | ready-for-agent |
| [18](issues/18-profile-validity-and-semantic-revocation.md) | 治理画像范围、有效期和语义撤回传播 | [02](issues/02-profile-specification-decisions.md)、[07](issues/07-profile-controls-and-immediate-commands.md)、[16](issues/16-atomic-fact-identity-and-coexistence.md) | ready-for-agent |
| [19](issues/19-purpose-aware-profile-slice.md) | 生成前编译用途明确的完整画像切片 | [04](issues/04-final-payload-budget-and-data-boundary.md)、[11](issues/11-reference-resolution-and-original-recovery.md)、[16](issues/16-atomic-fact-identity-and-coexistence.md)、[18](issues/18-profile-validity-and-semantic-revocation.md) | ready-for-agent |
| [20](issues/20-profile-evidence-disclosure.md) | 在简洁画像列表中按需查看依据与时效 | [16](issues/16-atomic-fact-identity-and-coexistence.md)、[18](issues/18-profile-validity-and-semantic-revocation.md) | ready-for-agent |
| [21](issues/21-context-sensitive-companion-expression.md) | 按任务、边界与前文适配有分寸表达 | [04](issues/04-final-payload-budget-and-data-boundary.md)、[05](issues/05-intent-bound-fact-protection.md)、[11](issues/11-reference-resolution-and-original-recovery.md) | ready-for-agent |
| [22](issues/22-unified-profile-expression-adoption.md) | 统一画像用途与表达策略采用快照 | [19](issues/19-purpose-aware-profile-slice.md)、[21](issues/21-context-sensitive-companion-expression.md) | ready-for-agent |
| [23](issues/23-natural-state-copy-and-templates.md) | 覆盖固定文案、澄清、进度与错误提示 | [21](issues/21-context-sensitive-companion-expression.md) | ready-for-agent |
| [24](issues/24-evidence-matched-paper-workflow.md) | 论文语义匹配、分层阅读与可恢复交付 | [10](issues/10-resumable-commute-kernel-pilot.md)、[12](issues/12-main-agent-hybrid-entry-and-task-relations.md)、[15](issues/15-task-aware-retrieval-and-module-context.md)、[21](issues/21-context-sensitive-companion-expression.md) | ready-for-agent |
| [25](issues/25-goal-driven-learning-resources.md) | 按目标组织证据支持的精简资料路径 | [10](issues/10-resumable-commute-kernel-pilot.md)、[12](issues/12-main-agent-hybrid-entry-and-task-relations.md)、[15](issues/15-task-aware-retrieval-and-module-context.md)、[21](issues/21-context-sensitive-companion-expression.md) | ready-for-agent |
| [26](issues/26-github-requirement-evidence-matrix.md) | 以必要功能证据矩阵推荐 GitHub 项目 | [10](issues/10-resumable-commute-kernel-pilot.md)、[12](issues/12-main-agent-hybrid-entry-and-task-relations.md)、[15](issues/15-task-aware-retrieval-and-module-context.md)、[21](issues/21-context-sensitive-companion-expression.md) | ready-for-agent |
| [27](issues/27-question-driven-tieba-evidence.md) | 按规定、体验和混合问题编排贴吧取证 | [10](issues/10-resumable-commute-kernel-pilot.md)、[12](issues/12-main-agent-hybrid-entry-and-task-relations.md)、[15](issues/15-task-aware-retrieval-and-module-context.md)、[21](issues/21-context-sensitive-companion-expression.md) | ready-for-agent |
| [28](issues/28-auditable-job-sample-analysis.md) | 用可核验岗位样本交付职责与薪资分析 | [10](issues/10-resumable-commute-kernel-pilot.md)、[12](issues/12-main-agent-hybrid-entry-and-task-relations.md)、[15](issues/15-task-aware-retrieval-and-module-context.md)、[21](issues/21-context-sensitive-companion-expression.md) | ready-for-agent |
| [29](issues/29-evidence-based-personal-career-gap.md) | 依据最小背景区分个人差距与待确认项 | [19](issues/19-purpose-aware-profile-slice.md)、[28](issues/28-auditable-job-sample-analysis.md) | ready-for-agent |
| [30](issues/30-resumable-study-page-recognition.md) | 按页恢复书页识别并定位关键材料疑点 | [04](issues/04-final-payload-budget-and-data-boundary.md)、[09](issues/09-shared-persistent-run-budget.md)、[10](issues/10-resumable-commute-kernel-pilot.md)、[14](issues/14-multimodal-and-saved-evidence-reuse.md) | ready-for-agent |
| [31](issues/31-study-scope-and-preview.md) | 核验知识范围覆盖并提交辅助预习 | [21](issues/21-context-sensitive-companion-expression.md)、[30](issues/30-resumable-study-page-recognition.md) | ready-for-agent |
| [32](issues/32-study-question-level-evidence-and-tutoring.md) | 按问题充分性逐层补证并自然辅导 | [15](issues/15-task-aware-retrieval-and-module-context.md)、[21](issues/21-context-sensitive-companion-expression.md)、[31](issues/31-study-scope-and-preview.md) | ready-for-agent |
| [33](issues/33-preverified-study-questions-and-rubrics.md) | 出题前冻结并核验覆盖计划与评分要点 | [12](issues/12-main-agent-hybrid-entry-and-task-relations.md)、[31](issues/31-study-scope-and-preview.md) | ready-for-agent |
| [34](issues/34-verified-study-grading-and-feedback.md) | 幂等判定作答并执行一次覆盖反馈 | [33](issues/33-preverified-study-questions-and-rubrics.md) | ready-for-agent |
| [35](issues/35-study-pause-and-appended-page-versions.md) | 恢复暂停复盘并原子更新追加书页版本 | [30](issues/30-resumable-study-page-recognition.md)、[31](issues/31-study-scope-and-preview.md)、[33](issues/33-preverified-study-questions-and-rubrics.md)、[34](issues/34-verified-study-grading-and-feedback.md) | ready-for-agent |
| [36](issues/36-evidence-bound-study-summary.md) | 按本次表现总结并单独恢复总结失败 | [32](issues/32-study-question-level-evidence-and-tutoring.md)、[34](issues/34-verified-study-grading-and-feedback.md)、[35](issues/35-study-pause-and-appended-page-versions.md) | ready-for-agent |
| [37](issues/37-composite-plans-and-verified-synthesis.md) | 执行跨模块依赖计划并统一核验综合结果 | [12](issues/12-main-agent-hybrid-entry-and-task-relations.md)、[24](issues/24-evidence-matched-paper-workflow.md)、[25](issues/25-goal-driven-learning-resources.md)、[26](issues/26-github-requirement-evidence-matrix.md)、[27](issues/27-question-driven-tieba-evidence.md)、[28](issues/28-auditable-job-sample-analysis.md)、[29](issues/29-evidence-based-personal-career-gap.md) | ready-for-agent |
| [38](issues/38-trusted-progress-and-result-projection.md) | 在正式界面展示真实进度、可信结果与恢复操作 | [06](issues/06-stream-replay-and-terminal-consistency.md)、[22](issues/22-unified-profile-expression-adoption.md)、[23](issues/23-natural-state-copy-and-templates.md)、[36](issues/36-evidence-bound-study-summary.md)、[37](issues/37-composite-plans-and-verified-synthesis.md) | ready-for-agent |
| [39](issues/39-human-expression-blind-evaluation.md) | 盲评有人味表达收益与交流边界 | [38](issues/38-trusted-progress-and-result-projection.md) | ready-for-agent |
| [40](issues/40-context-continuity-and-cost-evaluation.md) | 验证同模型同预算的连续性与成本 | [13](issues/13-bounded-summary-cache.md)、[14](issues/14-multimodal-and-saved-evidence-reuse.md)、[15](issues/15-task-aware-retrieval-and-module-context.md)、[19](issues/19-purpose-aware-profile-slice.md)、[37](issues/37-composite-plans-and-verified-synthesis.md) | ready-for-agent |
| [41](issues/41-profile-quality-and-personalization-evaluation.md) | 评测画像提取、治理与实际回答改善 | [17](issues/17-asynchronous-evidence-based-profile-extraction.md)、[18](issues/18-profile-validity-and-semantic-revocation.md)、[19](issues/19-purpose-aware-profile-slice.md)、[20](issues/20-profile-evidence-disclosure.md)、[22](issues/22-unified-profile-expression-adoption.md)、[29](issues/29-evidence-based-personal-career-gap.md) | ready-for-agent |
| [42](issues/42-workflow-evaluation-and-external-capability-gates.md) | 验证工作流质量、预算和外部真实可得性 | [36](issues/36-evidence-bound-study-summary.md)、[37](issues/37-composite-plans-and-verified-synthesis.md)、[38](issues/38-trusted-progress-and-result-projection.md) | ready-for-agent |
| [43](issues/43-integrated-migration-and-release-regression.md) | 完成兼容迁移、全路径回归与可恢复交付 | [39](issues/39-human-expression-blind-evaluation.md)、[40](issues/40-context-continuity-and-cost-evaluation.md)、[41](issues/41-profile-quality-and-personalization-evaluation.md)、[42](issues/42-workflow-evaluation-and-external-capability-gates.md) | ready-for-agent |

编号按依赖先后排列，不是严格串行步骤。每张票只有一条主要交付路径；公共基础通过普通回答或通勤试点贯通，领域票分别迁移自己的消费者。若某票实施范围明显超过一个新上下文窗口，应保留验收目标与依赖后再按实际接缝拆分，不能删掉需求缩票。

## 动态并行批次

表中每项前置只约束对应票，满足即可开始，不必等待同一行其他分支。

| 已验收前置 | 可推进票据 | 并行方式与重点 |
| --- | --- | --- |
| 无 | 01、03、05；02 可准备规格 | 合同、模型快照、事实保护可并行；02 的未决产品选择保持待确认。 |
| 03／05／01 分别完成 | 04／06／08 | 预算、流式、任务状态分别推进，聊天入口的共享修改顺序合入。 |
| 02 完成 | 07 → 16 | 按用户确认先补画像规格，再做控制与完整事实写模型。 |
| 03、08 完成 | 09 | 可与 04 并行，之后所有节点使用同一账本。 |
| 04、08 完成 | 11 | 对象解析可与通勤节点骨架并行，任务合同先冻结。 |
| 04、08、09 完成 | 10 | 通勤首条持久节点/收据/核验/提交路径，验收后再扩展领域。 |
| 04、08、09、11／04、11／04、05、11 分别完成 | 13／14／21 | 摘要、原材料读取、交流策略可分责任域并行。 |
| 04、11、14 完成；12 自身前置完成 | 15、12 | 检索入口与主理解可并行，前者不另造路由/等待权威。 |
| 16 及各票其他前置完成 | 17、18 | 异步候选提取与生命周期并行前约定事实合同、提交守卫及墓碑检查。 |
| 16、18 及各票其他前置完成 | 19、20 | 切片与依据页面分工，共享 API 投影顺序合入。 |
| 19、21／21 分别完成 | 22／23 | 画像表达与固定文案；领域后续接线各自模板。 |
| 10、12、15、21 完成 | 24、25、26、27、28 | 五个日常领域独立工作树，公共注册表由集成人员维护。 |
| 19、28 完成 | 29 | 个人职业规划与其他日常领域并行。 |
| 04、09、10、14 完成 | 30 → 31 | 学习书页、范围/预习可与日常模块并行。 |
| 31 及各票其他前置完成 | 32、33 | 辅导与评分计划可并行，范围版本/来源共用。 |
| 33 完成 | 34 → 35 | 判定边界验收后做暂停和追加版本。 |
| 32、34、35 完成 | 36 | 总结独立恢复，可与日常综合任务并行。 |
| 12、24–29 完成 | 37 | 跨模块调度与综合门，验证单轮调整和依赖失效。 |
| 17、18、19、20、22、29 完成 | 41 | 画像真实评测可早于学习与界面收尾，实验版本冻结。 |
| 13、14、15、19、37 完成 | 40 | 上下文配对可与界面收尾并行。 |
| 06、22、23、36、37 完成 | 38 | 正式界面和全自然语言来源覆盖验收。 |
| 38 完成，42 自身其他前置完成 | 39、42 | 人味盲评与编排/外部可得性分开评测，共用可追溯运行锁。 |
| 39–42 完成 | 43 | 最终迁移、回滚、三个桌面视口与覆盖对账，顺序收口。 |

建议同时执行 **2–3 张不同责任域的票**，一个集成人员按硬依赖顺序合入。一条公共运行/上下文线、一条表达/画像线，内核验收后第三条承担日常领域或学习阶段。场景与只读标注可提前准备；新方案正式配对等待对应实现和版本冻结。

## 接缝所有权与合入冲突

| 接缝 | 负责票 | 消费者 | 建议 |
| --- | --- | --- | --- |
| ADR、V2 产品/API/交互、领域词汇 | 01；02 仅画像剩余选择 | 所有实施票 | 原讨论保留，记录替代链，正式合同更新集中复核。 |
| 模型网关/额度/最终材料 | 03、04 | 09、13–19、24–36 | 冻结接口与测试，消费者不复制预算器，门后不得追加材料。 |
| 任务、等待、运行账本、数据库迁移 | 08、09、16 | 10–12、17–20、24–37 | 协调迁移编号，每票负责自己生命周期，共享 Schema 顺序合入。 |
| 节点内核、注册/配方、父图、终态 | 10、12、37 | 六领域与学习 | 各领域只注册自己节点，核心修改集中，先验收通勤试点。 |
| 原文、摘要、图片和检索 | 11、13–15 | 19 与领域节点 | 共同对象/来源/采用清单，不另造状态权威。 |
| 画像事实、候选、墓碑与切片 | 07、16–19 | 17、20、22、29 | 16 冻结事实身份；17/18 并行前约定提交守卫；撤回时旧切片失效。 |
| 表达策略、正文 SSE、前端聊天页 | 05、06、21–23、38 | 全部呈现 | 06 唯一正文协议，38 只增加可信块，模板由领域负责接线。 |
| 教学写模型、小节/题目/判定 | 30–36 | 12、38 | 范围/评分先冻结，呈现/判定/总结分别提交，不新增权威状态机。 |

各智能体使用独立 Git worktree 和 `codex/` 分支。派发前先让四目录文档与本批票据成为工作树可见的共同基线：**当前四目录尚未提交，Git worktree 默认不会复制它们**。协调保存/提交共同文档后再开工作树，不能让执行者缺失必读资料。并行开发不等于并行合入，后合入票基于已验收前置复核接口和相关测试；只暂存本票变更，保留原有工作区内容。本次没有创建工作树、提交或派发实施智能体。

## 完成定义

完成 43 张票须有规格确认、各纵向路径和持久状态生命周期验证、共同不变量、真实配对报告与外部可得性证据。人味化先通过帮助/分寸非劣再验证自然度；上下文通过硬门并证明连续性与成本；画像体现可撤回的有效事实及具体回答改善；编排通过节点恢复/预算/领域质量门。每项证据不足或必要门失败时明确未开放范围，不以消息 DONE、引用存在、模型一致或旧测试数量代表成功。

## 本次拆票检查

在 conda `agent` 环境完成文档检查：43 张编号连续、每票必需字段/验收完整；依赖顺序合法且无环，43 的传递前置覆盖其余 42 张；所有画像实现通过依赖等待 02；21 个源文件均有覆盖映射并成为相关票必读资料；127 条需求映射、41 项逐项选择与 39 个工作流场景均登记，1303 个本地链接有效。各票约 3.4–5.1 KB。以上是票据结构和覆盖对照检查，实施与真实效果的验收由各票后续执行。
