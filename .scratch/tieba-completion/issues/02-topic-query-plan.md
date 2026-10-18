# 02 — 自然语言话题生成有效且不重复的检索计划

**What to build:** “我想获取关于宿舍的讨论”以宿舍、寝室等真实话题检索，指令词不进入查询，校区与时间条件保持有效。

**Blocked by:** 无 — 可立即开始

**Status:** ready-for-human

## 实施边界与关键决策

区分动作表达、主题、显式约束及原话题内的别称。复用当前解析与查询计划；在最终发送给贴吧的查询层去重，而非只比较带site前缀的展示字符串。检索计划记录每轮目标和真实执行词。

## 验收条件

- [x] 目标句提取主题宿舍，不包含获取；“帮我收集”“想了解”等等价请求覆盖，真实名词不被逐字误删。
- [x] 原词、同义表达、相关子问题只在原话题内扩展，不把明确校区、时间等约束静默放宽。
- [x] 去掉贴吧等装饰词后相同的查询只执行一次；零结果后的调整确实改变检索表达。
- [x] 只有动作词、没有话题时请求澄清；继续原任务与新话题在任务上下文中正确区分。
- [x] 从聊天输入经实际浏览器参数转换到检索记录有行为测试；界面范围说明与执行词一致。

## 验证方式

参数化自然语言样例 → 真实计划与端口参数捕获 → 聊天投影；不调用生产模型即可验收本票。

## 不在本票范围

不新增通用语义解析框架，不用固定宿舍特例代替普遍的动作/主题区分。

## 通用执行与交接要求

- 执行前阅读本工作流执行方案及领域合同；复核当前实现，已有通过能力优先复用，不能依据历史问题描述重建已完成模块。
- 使用 conda agent 环境。先建立能捕获本票缺陷/行为的测试，再实现；每票包括所需公开投影、中文界面状态和端到端验证，不以单层完成替代行为交付。
- 外部协议须由真实响应验证；若主路径不能满足合同，说明验证过的备选与剩余差距，不静默缩减范围。不得搬运参考项目源码或引入其业务客户端。
- 共享预算、账户、任务版本、租约与证据边界保持一致；内部旧路径直接移除，不新增长期双实现。稳定API及历史数据按外部合同评估。
- 提交前执行项目完整检查，前端变更额外构建，协议变更同步生成；schema/存量数据变化才执行对应迁移。变更源码及测试单文件不超过500行，必要拆分只限本票职责。
- 交接记录实际行为、测试命令及退出码、真实/模拟证据、未验证项、回滚方法、公开契约与版本变化。完成实现但待人工复核可转 ready-for-human；只有验收证据合格才解除下游阻塞。

## Comments

- 2026-10-18：依据当前代码、既有ADR和本次运行账本故障分析形成待确认草案；未执行开发。


- 2026-10-18：用户确认拆分，正式发布为 ready-for-agent；编号、内容范围和依赖保持已批准方案，未开始业务开发。

- 2026-10-18：实现与验证完成，分支 `02-topic-query-plan`（基于 `main@6c966e86`），状态转 ready-for-human，待人工复核后再解除下游阻塞。

  **实际行为**

  - 动作词（获取/收集/搜集/了解/查找/查阅等）不再进入主题词；去掉时间条件后再抽取主题，避免“近三个月”产生“近三”碎片；`国庆节` 统一映射为 `国庆`。
  - 查询计划改为 `TiebaQueryRound(goal, query, execution_terms)`；`execution_terms` 去掉固定来源前缀及“贴吧/吧/吧里/帖子/贴子”等装饰词，作为真实执行词和去重键。公共索引仍发送带 `site:` 的完整查询，浏览器路径发送同一组最小执行词。
  - 计划按真实执行词去重：`宿舍` 与 `宿舍 贴吧` 只执行一次；备用放宽词只有真正改变表达时才追加，零结果后的下一轮确实使用另一条表达。
  - 明确校区、时间条件保留在主题/过滤中，不被同义扩展或放宽词静默移除；只有动作词、无主题时进入澄清且不产生查询轮次；任务上下文继续原任务与新话题分别处理。
  - 每轮诊断文案使用计划中的真实目标；聊天结果/空结果增加“检索计划：原词查询「宿舍」；常用说法查询「寝室」”，前端详情新增“检索计划（每轮目标与实际检索词）”。

  **公开契约与版本**

  - `TiebaEvidencePlan.tieba_queries: list[str]` 移除，替换为 `query_rounds: list[TiebaQueryRound]`；`TiebaResearchProjection` 新增 `query_rounds`；已重新生成 `openapi.json` 与 `packages/contracts/src/generated.ts`。
  - 配方升级：`tieba-recipe-v7`；能力版本 `parse-v6`、`plan-v6`、`search-v9`、`synthesize-v7`。旧版本产物按现有版本门控不再复用，不涉及数据库 schema 或存量数据迁移。

  **验证命令与退出码**

  - `conda run -n agent --no-capture-output python -m pytest tests/tieba tests/contracts tests/chat/test_improvement15_module_acceptance.py -q` → 退出 0，264 passed（其中 `tests/tieba` 245 passed）。
  - `python -m ruff check <本票改动文件>` → 退出 0，All checks passed。
  - `python -m mypy src/bridges/tieba` → 退出 1，仅 10 个既有错误且均不在 `src/bridges/tieba` 改动文件；本票文件无 mypy 报错。
  - `npm run test:unit -- src/components/bridges/chat/TiebaAnswer.test.tsx`（`apps/web`）→ 退出 0，9 passed；`npm run typecheck`、`npm run lint` 退出 0（lint 仅有既有 `<img>` 警告）；`npm run build` 退出 0，路由与静态页生成完成（Junction 下 standalone 符号链接复制报 EPERM 警告，不影响构建成功）。
  - `pytest -q` 全量 → 退出 1：4 个 `tests/career_plan` 收集错误及 328 个既有失败（career/retirement/MCP/security 等），与 tieba 无关；已在 `main` 上复现代表性失败（如 `test_legacy_extension_routes_are_safe_410`、`test_repository_contains_no_committed_secrets`）。全仓库 ruff 同样存在 458 个既有问题，本票改动文件已清零。

  **证据性质与未验证项**

  - 行为证据为参数化自然语言样例 + `_FakeSearchPort` 捕获实际发送查询 + 聊天投影断言；浏览器 `browser_terms` 参数转换有单测，不调用生产模型。
  - 未对本票执行真实贴吧浏览器联网验证；本票未改外部协议，相关真实路径仍由既有贴吧测试覆盖。全量测试红灯为基线既存问题，需由对应票单独处理。

  **回滚方法**

  - 回滚合并提交或删除 `02-topic-query-plan` 分支即可恢复 v6 行为；无数据库、对象存储或存量数据变更。回滚后旧 v6 配方产物重新可用。
