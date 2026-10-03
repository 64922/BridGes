# 23 — 覆盖固定文案、澄清、进度与错误提示

**What to build:** 全部用户可见自然语言说明更自然清楚，状态、引用、数据与真实恢复能力保持准确。

**Blocked by:** 21 — 按任务、边界与前文适配有分寸表达

**Status:** ready-for-human

**优先级：** P2

## 背景与需求

“每一句回复”包括模板和状态文案；只改轻量提示词无法覆盖通勤、资料、贴吧、职业结果或错误提示。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/人味化/讨论记录.md](../../../docs/人味化/讨论记录.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/人味化/审查与改进建议.md](../../../docs/人味化/审查与改进建议.md)。
- [docs/workflow/daily-workflows.md](../../../docs/workflow/daily-workflows.md)。
- [docs/workflow/study-workflow.md](../../../docs/workflow/study-workflow.md)。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。

## 任务内容

1. 建立正式路径文案清单：路由澄清、工具进度、等待/停止、错误、空结果、部分成功/降级和六模块/学习固定说明。
2. 模型自然语言由原调用适配，固定文案由状态/真实结果选择中文模板；代码、公式、引用原文、工具数据和状态枚举不交给模型全文重写。
3. 错误说明发生什么、对用户影响和确实可用的恢复操作；未读取、不支持、未配置、限流/超时和不可核实分开，不用安抚掩盖失败。
4. 正文先给用户需要的结论，细节复用既有卡片/展开；产品合同要求的字段和流程不擅自隐藏，不新增工作台。
5. 统一收尾与伙伴分寸，避免机械推销/追问。对可定位来源、路线/价格/时间/薪资等字段用确定性渲染保真。
6. 登记模板版本/路径/真状态，使各模块完成后可复核清单；内部分析或排名调用不为覆盖率强行加人格规则。

## 跨票接缝与责任

本票拥有公共状态文案和模板清单；领域模板由 24–36 按实际状态落地，38 完成前端交付投影的自然说明。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 每个用户可见来源都有对应文案/生成策略，成功/部分/失败/空/停止逐项可验证。
- [x] 路线、引用、价格、时间、岗位和题目状态在模板调整前后语义与数据一致。
- [x] 错误只承诺真实可用恢复能力，不伪装成功，不固定追加建议/问题。
- [x] 无人味化额外调用，机器字段与内部分析未被改写。
- [ ] 正式桌面页面展示的中文文本与真实服务状态相符（本次独立验收验证服务与模板；正式桌面全来源投影按 38、43 验证）。

## 验证与交付证据

状态矩阵驱动模板/正式页面检查，对确定性数据做前后比较；最终消费者完整性在 38、43 检查。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 执行与验收记录

### 2026-10-03：实现与两轴评审修复

- 交付分支 `codex/23-natural-state-copy-and-templates`（工作树 `.worktrees/23-natural-state-copy-and-templates`，基点 main `bd014bd6`）；阻塞票 21 `2b3dc57c` 经 `git merge-base --is-ancestor` 核对已带入 main。
- 环境：Windows + conda `agent`（Python 3.11.15）；确定性替身只证明机制，真实模型体验与外部可得性按评测票 39/40/42 验证。
- 新包 `src/bridges/state_copy/`（版本 `state-copy-v1`，纯数据与确定性渲染，不发起模型调用）：
  - `types.py`：`CopyCategory`（澄清/进度/等待/停止/错误/空/部分/降级/模块/学习）、`CopyStrategy`（固定模板/确定性渲染/模型适配）、`FailureClass`（未读取、不支持、未配置、限流/超时、不可核实、不可达、内部、状态冲突、停止）、`RecoveryAction`（重试/等待/重配/调整请求/刷新状态/无）、`CopyEntry`、`ErrorTemplate`（`shared` 共享模型码、`contextual` 携带上下文的领域码）。
  - `catalog.py`：152 条登记（错误 82 条＝聊天映射 62＋共享 14＋上下文型 6，公共模板与六模块/学习清单 70 条）；模块/学习条目只登记既有渲染器路径与真实状态，不虚构未落地的状态。
  - `registry.py`：`STATE_COPY_REGISTRY`、`render_state_copy`、`state_copy_manifest`（每条含 version/path/category/owner/strategy/states/text/renderer/note）、`validate_state_copy_registry`（重复路径、类别/状态/失败类别覆盖缺口一次报全）、检索充足性文案由注册表派生。
  - `error_taxonomy.py`：`error_template` / `error_failure_class` / `error_recovery` / `client_error_<status>` 动态模板。
- 错误文案与分类（任务 3）：不可达（离线/连接/DNS/提供方不可用）与限流/超时分开；补登 `web_search_provider/connect/dns/quota/contract/redirect/response_too_large/source_conflict/cancelled/stage_timeout/internal` 等聊天与公网搜索码；`unregistered_capability` 改为「核心对话能力未就绪，请联系管理员。」；主/备权限失败统一为 `NOT_CONFIGURED/RECONFIGURE`；领域多变体文案（同一码在不同提供方/上下文的措辞）按主路径真实字面量登记，变体收敛留给 24–36 接入注册表时一并处理。
- 接线：`ai/errors.py`（共享模型码由注册表派生）、`chat/turn.py`（`_ERROR_MESSAGES`、断流/路由拒绝/思考摘要、公网搜索取消/阶段超时/内部异常改注册表常量）、`chat/terminal.py`（停止文案）、`chat/graph.py`（节点失败按错误码真实恢复方式渲染：重试/等待/调整/联系管理员；无恢复方式不追加伪承诺；点击重试仍以真实 `retryable` 为准）、`chat/understanding.py`、`routing/service.py`、`chat/service.py`（等待文案）、`retrieval/service.py`、`web_search/service.py`；`CONTEXT.md` 补「固定状态文案注册表」术语。范围边界：领域模板由 24–36 按实际状态落地，前端投影由 38 完成。
- 两轴评审（标准＋规格并行子代理）发现与处置：①公网搜索取消/阶段超时/内部异常未登记 → 已登记并在 turn/service 接线为单一来源；②注册表 15 处领域文案与真实字面量不一致 → 对齐主路径字面量（credentials/permission/dns/offline/connect/parse/request/redirect、configuration、challenge、evidence/no_results）；③离线误标 `RATE_LIMIT_TIMEOUT` → 新增 `UNAVAILABLE` 并重分类离线/连接/DNS/提供方不可用/`region_error`/`transient`；④节点失败固定追加「请调整后重试」与 `NONE/REFRESH_STATE` 登记冲突 → 按真实恢复方式渲染，无恢复方式不追加；⑤`unregistered_capability` 措辞对终端用户不可执行 → 改「请联系管理员」；⑥主/备 permission 分类不一致 → 统一；⑦CONTEXT.md 词汇缺口 → 已补术语。未采纳（记录为判断项）：`_RETRYABLE_CODES` 与注册表 `recovery` 强制合一（前者是聊天终态 UI 投影、后者是语义与跨端恢复描述，收敛属 38 前端投影票）；未登记的既有死码（`humanizer_*` 等）清理；多变体领域文案 1:1 收敛（见上）；`states` 由裸字符串改枚举（渲染器状态由领域所有，改动面超出本票）；`_error` 辅助函数、catalog 集中登记、`state_copy` 命名均判为可接受设计取舍。模块分状态缺口（github/tieba 无 partial、career 无 failure、commute 无 clarification/empty/partial）属领域模块当前无对应渲染器，未虚构登记，由 24–36 按实际状态补。
- 验证：
  - 新增 `tests/state_copy/` 22 项（注册表结构/清单/分类与恢复真实性/接线/公网搜索投影），全部通过。
  - 定向回归（state_copy、routing、ai、web_search、retrieval、聊天错误映射/终端/改进 12/21/V2 持久运行）：531 passed / 2 skipped；2 项 `tests/retrieval` 失败为 main 既有退役来源失败（聊天附件/项目文件已退役），非本票引入。
  - 全量（排除 `tests/integration/test_runtime_smoke.py`，本机在 main 与分支同样挂起）：分支 `272 failed / 4725 passed / 39 skipped / 1 xfailed`；main 同命令 `272 failed / 4703 passed`；失败集逐项一致（0 新增失败，+22 为本票新增用例）。
  - `ruff check` 变更文件 17 条诊断均为 main 同名既有（routing E501/N818、graph N818），0 新增；`mypy` 21 条与 main 基线逐条一致（0 新增）。
- 已知限制：多变体领域文案在注册表中按主路径字面量登记，完全收敛待领域票据接入；错误类别与恢复方式已登记但终端 UI 的点击重试投影仍由 `_RETRYABLE_CODES` 决定；真实模型体验与公网搜索外部可得性分别按评测票验证。
- 状态改 `ready-for-human` 等待人工验收；分支未推送、未合并。

### 2026-10-03：独立验收与修复

- 原交付存在 5 类问题：非法路径空段漏检；论文/资料主题不匹配误登部分成功；复盘完成误登停止；学习固定说明来源漏登；备用搜索缺凭据丢失管理员恢复指引。均已修复，新增 8 项回归，注册表现为 158 条。
- 两轴结论：标准轴无阻断规范违规，恢复语义与 UI 重试资格双源作为 38 的具体接缝记录；规格轴 4 类问题修复后通过本票公共模板与清单验收。未新增模型调用、持久状态或机器数据改写。
- 独立验证：公共路径 380 passed / 2 skipped；父图、终态、检索及前置表达回归 242 passed / 2 个主线既有失败；学习实际入口与领域不匹配 58 passed / 1 个主线既有失败。3 个行为失败全部在 main 同环境逐项复现。注册表与学习接线 ruff 通过，mypy 20=20，逐项无新增诊断。
- 本次未重跑全仓测试或正式桌面视觉验收；前端全来源投影及真实体验继续按原跨票分工验收。保留五种分诊状态，`ready-for-human` 下以本记录注明本票独立验收已通过。
- 详见 [独立验收记录](../acceptance/23-natural-state-copy-and-templates.md)。

### 2026-10-03：合入、推送与清理完成

- 验收修复提交 `767bc85a`，无冲突合并到 main，合并提交 `789ef23b`；该合并树与已验收分支完全一致。
- 推送前检测到并行任务又合入 Issue 20，保留其提交，基于当前 `main@c68f8cdd` 复跑接缝回归：公共文案、路由、AI、公网搜索、错误映射、聊天终态、前置表达及学习书页/辅导/复盘共 **511 passed / 2 skipped**。
- 通过系统网络代理 `http://127.0.0.1:7890` 同步 `origin/main`，远端已包含本票合并，推送确认 `Everything up-to-date`。
- Issue 工作树 `.worktrees/23-natural-state-copy-and-templates` 已删除，本地分支 `codex/23-natural-state-copy-and-templates` 已按 `git branch -d` 删除。
- 已运行 `git worktree prune --verbose`，再次 dry-run 无失效记录；复核仅剩主工作树与活跃的 Issue 30 工作树。

