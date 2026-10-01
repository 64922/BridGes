# 16 — 以完整事实身份保存画像并处理并存更新

**What to build:** 用户列表保存可理解的完整事实，不同属性与并行目标同时保留；只有明确同事实变化才更新对应信息。

**Blocked by:** 02 — 补齐画像剩余规格与验收决策；07 — 分开画像记录与使用控制，保证即时撤回

**Status:** ready-for-human

**优先级：** P1

## 背景与需求

“喜欢 Python”与“正在学习 Python”被合并；年级与专业、考研与六级被同维度后值覆盖。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/用户画像/讨论记录.md](../../../docs/用户画像/讨论记录.md)。
- [docs/用户画像/提取时机与回答应用流程.md](../../../docs/用户画像/提取时机与回答应用流程.md)。
- [docs/用户画像/审查与改进建议.md](../../../docs/用户画像/审查与改进建议.md)。
- [docs/用户画像/复核脚本.py](../../../docs/用户画像/复核脚本.py)。

## 任务内容

1. 落实 02 确认的最小事实合同：完整正文、主体/关系/对象/适用范围身份、精确来源、用户声明/编辑与自动来源、版本及状态；界面继续无固定分类。
2. 同一事实补充证据，不同事实并存；年级大二→大三更新年级，专业保留，新增六级不覆盖考研；明确不考研转就业仅替代对应目标。
3. 近义/语义相似只用于提出身份候选，有明确依据才合并；不确定同一性不自动覆盖/恢复。事实身份为 18 的撤回边界提供依据。
4. 用户编辑优先，编辑换值抑制旧身份/证据；自动提交重新核对预期版本。事实、观察、当前情境及知识状态不能混成长期用户标签。
5. 扩展现有原子存储/镜像接缝；消除以维度作为冲突身份的写入行为。旧记录来源保留可对账迁移，不能伪造旧记录未保存的原话或关系。
6. 迁移采取新形态并存、逐步迁移、最后收敛；有迁移报告、幂等、失败恢复和回滚，低可靠度旧来源不因迁移获得使用资格。

## 跨票接缝与责任

拥有画像写模型/身份/版本与迁移；17 提交经检验候选，18 管生命周期，19/20 读投影。共享数据库迁移由本票协调编号。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [ ] 喜欢 Python 与正在学 Python 分别保留关系，不推出已掌握。
- [ ] 年级/专业和考研/六级均并存，明确变更只替代对应事实。
- [ ] 事实来源、编辑权威和被替代版本可追溯；旧重放不增加证据次数或覆盖用户编辑。
- [ ] 无固定分类 UI，不以迁移重新开放四维用户页面。
- [ ] 账户隔离、对账、导出、删除、迁移失败/回滚和新旧数据兼容通过。

## 验证与交付证据

重放关系丢失、学业覆盖、目标覆盖和近义去重探针，用正式保存/列表/API 和迁移报告断言。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 实施记录（2026-10-01）

**代码/合同版本**：分支 `codex/issue-16-atomic-fact-identity-and-coexistence`，基线 `main@f52c74cc`，实施 `dd701cb9` + 评审修复 `f674ef31`；`ATOMIC_PROFILE_MIGRATION_VERSION` 保持 `profile-atomic-v1`；数据库 `SCHEMA_VERSION` 63 → 64（本票协调编号）。开发/验证均在 conda `agent`。

### 实现说明

- **事实身份**：`AtomicProfileFactIdentity`（主体/关系/对象/范围）+ `fact_key` 决定同事实合并；`identity_key`（账户 + 规范化正文）保留为旧墓碑兼容的文本抑制键。单值属性槽（identity/grade/major）同槽新值替代旧活动条目；interest/learning/research/goal 与 statement 默认并存。
- **并存与替代**：`mirror_record` 先按来源记录、再按 `fact_key`、最后按文本键查重；同一事实只补证据，来源值更新走“旧版本退休（SUPERSEDED）+ 新条目 + supersedes_id/superseded_by_id 替代链”；单值槽冲突由 `_reconcile_explicit_change` 替代，明确收回（纯否定或“不 X 了，转为 Y”）只替代对象完全一致的旧事实。
- **用户权威**：用户编辑换值同时抑制旧文本键与旧事实身份（同一事实仅换措辞时只抑制旧正文，不封锁事实身份）；按事实身份命中的用户编辑条目不再被自动新说法改写；新条目不继承旧版本原话（原话只来自本次来源记录）。
- **自动抽取**：`ProfileExtractionItem.fact_text`（本地规则对兴趣/学习/研究句提供完整分句）与 `identity_discriminator`（写入四维来源记录键）保留「喜欢 Python」与「正在学习 Python」两条关系；删除 STAGE_GOAL/ACADEMIC_STATUS 的维度强制 CREATE→UPDATE，学业阶段与阶段目标交由事实身份处理。
- **迁移**：`migrate_account` 在新形态并存迁移后为既有条目补齐身份（`_backfill_item_identities`，幂等、不改版本/正文/来源/编辑权威，墓碑无正文不伪造），报告新计数 `identity_backfilled` 与计算字段 `converged`；逐条对账与旧报告字段不变。

### 接口/迁移变化

- 合同：`AtomicProfileItemStatus.SUPERSEDED`；`AtomicProfileItem` 新增 `fact_subject/fact_relation/fact_object/fact_scope/fact_key/evidence_quote/supersedes_id/superseded_by_id`；`AtomicProfileItemProjection` 新增 `evidence_quote/supersedes_id`；`AtomicProfileMigrationReport` 新增 `identity_backfilled` 与只读 `converged`；`ProfileExtractionItem` 新增 `fact_text`；`ProfileRecordSubmission` 新增 `fact_text/identity_discriminator`。
- 迁移 64：`profile_items` 增加上述八列（NOT NULL 列带安全默认：`user`/`statement`/空/`long_term`/空）与 `idx_profile_items_account_fact_key`；`profile_item_migrations` 增加 `identity_backfilled`（默认 0）。列与索引纳入 `REQUIRED_TABLE_COLUMNS`/`REQUIRED_INDEXES`；新增 `MIGRATION_ADDED_COLUMNS` 使升级前结构校验排除“待迁移补加”的列，健康旧库不再被误判为结构被顶替。
- `openapi.json` 与 `packages/contracts/src/generated.ts` 已重生成（309 paths）。

### 跨票接缝

- **17**：抽取侧只需提供 `fact_text`（完整事实）+ `identity_discriminator`；写入/合并/替代全在原子层完成。
- **18**：事实身份（`fact_key`）、精确来源（`evidence_quote`）与替代链（`supersedes_id/superseded_by_id`）是语义撤回与生命周期治理的依据。
- **19/20**：页面投影新增 `evidence_quote` 与 `supersedes_id`；切片读取仍只包含活动条目。

### 验证结果

- 新增测试 20 项全绿：`tests/profiles/test_issue16_atomic_fact_identity.py` 16（身份解析；喜欢/学习、年级/专业、考研/六级并存；近义不合并；明确变更/纯否定只替代对应目标；单值槽替代链与投影证据；用户编辑优先且旧身份不复活；迁移补齐、幂等与 `converged`；SQLite 重启回读；重放去重）与 `tests/storage/test_schema_v64.py` 4（新列/索引、启动清单、v63→v64 无损升级与默认身份）。
- 探针重放（`conda run -n agent python docs/用户画像/复核脚本.py`）：关系丢失与合并 `["我正在学习Python", "我喜欢Python"]`；不同学业属性覆盖 `["软件工程专业", "大二学生"]`；并行目标覆盖 `["通过英语六级", "考研"]`；近义去重 2 条；删除后近义新表述恢复为 1 条。
- 回归：`tests/profiles` 405 passed / 1 failed、`tests/storage`+`tests/contracts` 156 passed；唯一失败 `test_chat_correction_uses_latest_record_and_is_idempotent` 在基线 worktree 单独复跑同样失败（Windows 时钟精度导致最新记录排序平局），与本票无关。聊天全目录失败名单与基线逐名 diff 一致（100 = 100，既存失败）；mypy 受检文件零新增错误；ruff 变更文件零新增诊断（automatic.py 13 项 E501 为既存）。
- 既有测试适配：`_MirrorFailsOnce` 等镜像桩同步 `fact_text` 形参；`test_later_explicit_stage_goal_updates_in_place` 的抽取桩显式返回 `action=update`（写入层不再按维度强制覆盖）；兴趣条目正文保留完整关系分句（`["我喜欢跑步"]`）；`test_schema_v63.py` 版本断言改为 `>= 63`。

### 限制与边界

- **回滚边界**：`rollback_migration` 仍只删除本批新建条目；身份补齐是既有条目的幂等元数据补写（不改正文/来源/版本），不随回滚撤销，撤销报告保留 `identity_backfilled` 计数。如需回退补齐需另行迁移。
- **“更喜欢”**：保留既有明确偏好变更的 UPDATE 语义（消息自身携带变更词时），未改成事实身份替代；普通「喜欢/正在学」不受影响。
- **同句多项**：本地规则对「我喜欢跑步，也喜欢游泳」仍只抽第一条，属抽取范围（17），本票不扩。
- 既有 Windows 环境失败（聊天纠正最新记录平局、生命周期 409、chat 全目录 100 项、evaluation 文件锁、knowledge_base/ingestion/mcp/plugins 既存失败）均与本票无关，未修复。
- 模型响应为确定性桩/本地规则，只证明写入模型与身份机制；真实模型抽取质量与外部可得性按 17/41 评测票验证。

