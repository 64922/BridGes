# 11 — 按任务召回记忆并追踪回答用途

**What to build:** 用户换一种说法也能用到相关记忆，时间约束和表达偏好真实影响回答；采用依据可按需查看，无关或失效信息不被注入。

**Blocked by:** [07 — 时间与当前状态](07-time-and-current-state.md)；[09 — 敏感用途](09-explicit-sensitive-memory.md)。

**Status:** ready-for-agent

**发布状态：** 2026-10-15 用户已确认拆分与依赖，正式发布；尚未实施。

**对应合同：** D01、D04、D06、D11；T03、T11、T13、T17、T20。参见[定稿](../PRD.md)、[共同执行约束](../README.md)。

## 用户演示

用户在新对话换说法请求学习安排，回答遵守此前每天半小时及先给例子的偏好；展开说明能看到实际采用条目，无关爱好和过期目标不出现。

## 依赖与实施范围

07 决定当前/历史用途，09 及其前置提供敏感用途和撤回控制。指定材料来源不额外阻塞召回，因为共用同一事实合同。

- 核查并复用关键词和向量能力，按账户与权限限制候选范围；索引是可重建派生数据，不持有独立事实真相。
- 按任务、用途、时效、当前要求、用户权威及预算筛选，语义相似不证明真实性或扩大权限。
- 保存实际发送的条目、版本、作用与排除原因，连接回答上下文说明；保留完整限定，不截断为误导结论。
- 发送前复查控制与撤回，失效则重编译；索引或模型不可用时做明确有限降级，不把服务失败伪装成无相关记忆。
- 向量和索引版本独立固定，不能随主模型切换静默混用。

## 验收标准

- [x] 同义提问可以找到事实，半小时限制和解释顺序实际改变回答。
- [x] 冲突、过期目标、无关爱好和越用途敏感事实不进入确定性回答。
- [x] 本轮明确要求覆盖长期默认但不改写长期记忆，新主题事实同样可被召回。
- [x] 采用记录对应真正发送的上下文，不记录一套而使用另一套。
- [x] 删除、改用途、关闭使用和账户切换同时约束关键词、向量、缓存及发送前切片。
- [x] 索引重建和故障恢复不复活旧条目，派生数据纳入导出边界说明及账户删除。
- [x] 不默认发送完整概览，预算不足优先舍弃低相关条目而非删关键条件。

## 验证与交付

运行字面不同的召回旅程、正确/错误/无画像配对回答、索引故障与发送前撤回竞态；记录成本与延迟。交付采用记录、用途合同、可解释详情和索引生命周期，供反馈闭环使用。

## 明确不做

不重建通用搜索平台，不以召回频次增强可信度，不注入完整个人档案。

## Comments

2026-10-15：用户回复「按此发布」，确认本次拆分与依赖。已发布为 ready-for-agent，尚无实施记录。

2026-10-16：实施与验证完成（分支 `codex/issue-11-hybrid-purpose-recall`，基点 `fff98a9d`；验证过程先并入 `main@5709a0f6`（16 票），再并入 `main@6b1545e7`（工单 10）。与工单 10 的两处冲突已解决：`atomic.py` 构造参数两票并存、`database.py` 迁移号顺延——工单 10 先行占用 85，本票派生索引迁移改为 86）。

- 交付：新模块 `profiles/recall.py`（版本化派生向量索引：合同四元哈希、阈值 0.55、嵌入批次 10、合同变化退休重建、正文/版本失效不参与检索、稳定降级原因）；`atomic.py` 关系槽位关键词通道 + 语义候选通道（语义命中只作候选，真伪/用途/时效/冲突/低可靠度/本轮明确要求门禁全部照旧，降级时未命中原因如实标注）；`contracts/profile_adoption.py` 增加 `recall_basis`/`recall_degradation_reason`/`recall_index_version`，编译策略 1.2（接受 1.1）；`chat/turn.py` 发送态记录（`adopted_profile_slice`）与冻结编译快照（`adopted_profile_slice_compiled`）拆分，最终门整体未采用时改写为「最终输入预算不足，本轮未采用」，降级披露如实、审计新增 `recall_degraded`/`recall_index_version`；`app_setup_knowledge.py` 复用全局固定 Embedding 端口注入索引；迁移 86 两表 + `lifecycle.catalog` 派生数据登记（删除/备份，不进导出）；`EmbeddingOperation.PROFILE_RECALL`。
- 测试：新增 19 项（`tests/profiles/test_issue11_hybrid_purpose_recall.py` 13、`tests/chat/test_issue11_recall_adoption_record.py` 4、`tests/storage/test_schema_v86_profile_recall_index.py` 2）；责任票行为更新 3 处（journey `recall-academic-control` 转 pass、策略版本 1.2、策略失败清两键）。最终合并态聚焦 `pytest tests/profiles tests/storage` 847 通过（含工单 10 测试）、聊天/评测聚焦 61 通过；全量对 main@6b1545e7（同命令 `-n 4 --ignore=tests/career_plan`）327 failed / 5634 passed / 39 skipped vs 329 / 5615 / 37，逐条差集**分支独有失败 0**（main 独有 2 项为 `test_api_boot` 既有 flaky；跳过 +2 为 worktree 无前端构建产物的 runtime 守卫差异）。
- 走查（确定性 5 段 + 真实向量探针，全部通过）：槽位问句召回修复、语义改写召回与四类门禁优先、显式降级、删除/重建不复活/账户隔离、预算整条裁剪记录一致；真实 Embedding（development + OS 凭据库）：同义对 0.6264/0.5737 ≥ 0.55、无关对 0.1746/0.2488 < 0.55、生产组合端到端语义召回；单批 8 条 0.39 秒，每回合至多 1 次查询向量、无问题 0 次。
- 静态：`mypy src` 141=141、`ruff`（改动文件）新增 0（`database.py` 1 条既有 E501 与 main 一致）、`pip check` 通过。
- 验证记录：`.scratch/profile-memory-redesign/validation/11-hybrid-purpose-recall/verification-record.md`；走查证据 `.../evidence/demo-walkthrough-20261016.txt`，脚本 `.../tools/walk_issue11_hybrid_recall_demo.py`（可复跑）。
- 已知限制：同义对余弦 0.5737 距阈值余量约 0.024，更远改写可能漏召（保守取向），语义质量长跑由 18 票负责；被替代条目同样进入索引以支持 07 回顾用途，扩大回顾范围需复核索引过滤；增量同步只在编译时机发生（读取时仍按当前条目核对，不读旧正文）；升级前旧运行无冻结快照键，重试回落发送态兼容路径。详见验证记录 §6。
