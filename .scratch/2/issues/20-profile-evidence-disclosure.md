# 20 — 在简洁画像列表中按需查看依据与时效

**What to build:** 用户保留无类别逐条列表，能展开单条查看原话、发生时间、范围和有效期，并直接修改或删除。

**Blocked by:** 16 — 以完整事实身份保存画像并处理并存更新；18 — 治理画像范围、有效期和语义撤回传播

**Status:** ready-for-human

**优先级：** P2

## 背景与需求

当前页面只展示来源数量，用户难判断为什么这么记、范围是否适用或已过期。D08 已确认按需展开交互。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/用户画像/讨论记录.md](../../../docs/用户画像/讨论记录.md)。
- [docs/用户画像/提取时机与回答应用流程.md](../../../docs/用户画像/提取时机与回答应用流程.md)。
- [docs/用户画像/审查与改进建议.md](../../../docs/用户画像/审查与改进建议.md)。
- [docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)。

## 任务内容

1. 依据 02/16 的事实投影与 18 的状态，新增按需展开/来源定位 API 与正式桌面列表交互；修改和删除始终可见，行内保存/取消和原有删除确认保留。
2. 展开显示实际原话和来源时间、适用范围、明确有效期/状态及来源可访问时的定位；原消息删除/不可读如实说明，不伪造引语。
3. 同步更新 D08 对 ADR-0030“每行仅修改删除”的交互扩展与正式验收，页面不增加固定分组、哈希、内部类别或逐条授权确认弹窗。
4. 用户反馈可区分事实记错、过期、范围不适用、回答没执行偏好；正确事实不因后两类反馈被自动删除。
5. 账户作用域和来源访问再次校验；展开只取本条必要信息，键盘操作/折叠、空态和错误文案均中文。

## 跨票接缝与责任

只拥有画像依据读 API/列表投影，不另建画像中心或用户工作台；16/18 继续拥有修改和生命周期语义。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [x] 每条可展开真实依据、时间、范围与有效期，原文定位不跨账户。
- [x] 无分类列表、始终可见修改删除、行内保存取消及删除确认可操作。
- [x] 来源已删/不可读时提示准确，不把数量当作全部依据。
- [x] 四类反馈不会错误删除仍正确的事实，变更即时反映在列表与切片。
- [x] 正式桌面浏览器与 API 权限/契约验收通过，新增字段进入生成合同。

## 验证与交付证据

用含多来源、用户编辑、过期和删除来源的账户进行 API 与真实页面验收。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 实施记录（2026-10-02）

**代码/合同版本**：分支 `codex/issue-20-profile-evidence-disclosure`，基线 `main@bd014bd6`；数据库 `SCHEMA_VERSION` 67 → 68（迁移 68 新建 `profile_item_feedback`）。开发/验证均在 conda `agent`（Windows；本工作树无 `.venv`，验证显式 `BRIDGES_PYTHON` 指向 conda 解释器、`PYTHONPATH` 指向工作树 `src`、UTF-8）。

### 实现说明

- **依据读 API**：`GET /profiles/items/{item_id}/evidence` 返回 `AtomicProfileItemEvidenceProjection`，只取本条必要信息：精确原话（未保存原话时 `evidence_quote_status=not_recorded` 如实说明，显式「记住」不伪造聊天引语）、来源消息时间、来源可访问状态、适用范围、`validity_phrase` 与由 18 的 `valid_from/valid_until/goal_state` 推导的当前时效（未说明期限／尚未生效／当前有效／已过期／目标暂停／目标完成）。
- **来源诚实与定位**：来源状态 available/deleted/unreadable；聊天消息物理删除，读取器查无即 deleted；未接线来源读取器时如实 unreadable。仅 available 返回 `conversation_id`/`message_id` 供定位 `/chat/{conversation_id}?message={message_id}`；已删除/不可读不返回定位。按合同「删除聊天原文时同步失效其引用和派生原话副本」，没有可读来源时不回传保存的原话（`evidence_quote_status=source_unavailable`），页面同时拒绝渲染无可读来源的原话。来源读取端口 `ProfileEvidenceSourceReader` 在组合根 `_atomic_profile_source_reader` 用会话仓库注入（与自动画像提取复用同一实例）。
- **四类反馈**：`POST /profiles/items/{item_id}/feedback` 区分事实记错／信息过期／范围不适用／回答没执行偏好；`(account_id, profile_item_id, kind)` 唯一约束保证同条同类幂等，效果映射为建议更正／建议复核时效／不改事实，**绝不自动删除或改写事实**，修改与删除仍由用户行内操作；反馈只记类型与可选备注，审计 `PROFILE_EVIDENCE_FEEDBACK` 不含正文。
- **生命周期**：反馈表纳入版本化迁移、必备表/列/索引清单、删除顺序、账户导出（profile 分类）与逻辑摘要；账户作用域在领域层按 `account_id` 过滤，跨账户读写不可达。
- **前端列表**：保持无类别单列；行尾新增可聚焦「查看依据／收起依据」（`aria-expanded`/`aria-controls`，面板 `role=region`），来源计数文案由「来自 N 条对话记录」改为「有 N 条对话来源，展开可核对」；面板可再次收起；错误与空态中文；修改/删除始终可见、行内保存/取消与删除确认保持不变。

### 接口/迁移变化

- 合同：新增 `AtomicProfileEvidenceSourceStatus`、`AtomicProfileEvidenceSource`、`AtomicProfileValidityStatus`、`AtomicProfileEvidenceQuoteStatus`、`AtomicProfileFeedbackKind`、`AtomicProfileFeedbackEffect`、`AtomicProfileItemFeedbackRequest`、`AtomicProfileFeedback`、`AtomicProfileFeedbackProjection`、`AtomicProfileItemEvidenceProjection`；`AuditAction` 新增 `PROFILE_EVIDENCE_FEEDBACK`。
- 迁移 68：新建 `profile_item_feedback`（`(account_id, profile_item_id, kind)` 唯一约束 + `(account_id, profile_item_id, created_at)` 查询索引；与既有个表一致，沿用应用层账户过滤，不加数据库外键）登记进 `REQUIRED_TABLES`/`REQUIRED_INDEXES`/`REQUIRED_TABLE_COLUMNS` 与 lifecycle catalog。
- `openapi.json`（311 paths）与 `packages/contracts/src/generated.ts` 已重生成，`tests/contracts` 校验再生成一致。

### 跨票接缝

- **16/18**：原话与事实身份取自 16 的条目字段，时效取自 18 的期限/目标状态；本票不新写生命周期语义。
- **19/22/29/37/41**：反馈是质量信号与表达依据，不改变切片编译；来源可访问状态只反映聊天原文实际留存情况，不宣称收回已发送上下文。

### 验证结果

- 本票测试：`tests/profiles/test_issue20_profile_evidence_disclosure.py` 18 passed；`tests/storage/test_schema_v68.py` 2 passed（内存与 SQLite 同场景）。
- 回归：`tests/profiles tests/storage tests/contracts tests/lifecycle` **704 passed / 6 failed**；6 项（聊天纠正平局 1 + `test_lifecycle_api.py` 会话 409 共 5）在基线 `main@bd014bd6` 独立复跑同名同因。自审修复后追加 `tests/api` 重跑 **704 passed / 1 failed**（仍为既有聊天纠正平局，main 复现）。
- 正式桌面验收：新增 `apps/web/e2e/issue20-profile-evidence-disclosure.spec.ts`，真实浏览器 + 真实 API、零 mock，**1 passed**（注册；两条显式「记住」；删除一条来源会话后展开核对原话/时间/范围/期限/已删除提示；可访问来源的「查看原文」定位；四类反馈提交后事实仍在；行内修改与删除确认）。
- Web：`npm run test:unit` **217 passed**；`npm run typecheck` 与 eslint 干净；既有画像相关 e2e（issue21/25/26/27）与基线逐名对比一致（19 failed 同名同因；另一次并发全量中附件用例抖动，单独复跑 3/3 通过）。
- 静态检查：变更实现文件 ruff 零新增（`main.py` E402/I001、`database.py` E501 与 main 基线逐项相同）；新增测试与合同文件零诊断；mypy 仅剩 `main.py` 健康检查既有错误（main 同名同因）。
- 送验前自审修复：原话在无任何可读来源时改为失效不回传（此前仍回传并标注「保存时的记录」，违反合同删除失效要求）；未知期限文案不再断言「长期有效」，改为「没有明确期限……据此制定具体计划前会先与你确认」（R01）；每次重新展开都重取依据（避免来源在两次展开之间被删后仍显示旧状态）；内存反馈仓库与 SQLite 唯一约束对齐；去掉无消费方的 `role` 字段与前端重复映射。

### 限制与边界

- 确定性规则与桩只证明机制；真实模型抽取质量与反馈闭环效果按 41 评测票验证。
- 未接线来源读取器的部署形态下仅能如实显示「不可读」，不推断来源。
- 显式「记住」路径没有聊天原话字段，面板如实显示「没有保存聊天原话」，属合同行为而非缺失。
- **原话精确区间/消息版本**：合同「证据」要求的 Unicode `[start, end)` 区间与来源消息版本留在 16/17 的写入模型，原子条目当前只持久化 `evidence_quote`（240 字截断由 17 控制），本票依据读投影按现有字段如实展示；区间级核对属 16/17 后续接缝。
- **来源删除的存储级失效**：本票在读取/展示层失效派生原话副本（无可读来源即不回传、不渲染）；把聊天删除事件接入画像存储清理（物理清空副本并停用唯一证据事实）属 18/43 的生命周期接缝，未在本票实现。

### 独立验收与修复（2026-10-03）

- 原交付尚有多来源原话失效、列表 API 绕过失效、依据刷新和反馈提交竞态问题，已修复；原话只在当前可读来源中精确核对后由按需接口返回。
- 补齐四类反馈实际提交、多来源原话、已过期及删除原话来源后的真实桌面验收。后端回归 770 passed / 6 已有失败（main 同名同因），本票/迁移/合同 26 passed；Web 219 passed；Chromium 2 passed；类型、受改前端 ESLint 和相关后端 Ruff 通过。
- 验收结论为修复后通过；本段及[独立验收记录](../acceptance/20-profile-evidence-disclosure.md)取代实施记录中“任一可读来源即可回传原话”以及旧浏览器脚本已提交四类反馈的口径。存储级清理与召回生命周期仍按 18/43 接缝，不宣称本票已完成。

