# 43 — 完成兼容迁移、全路径回归与可恢复交付

**What to build:** 四个方向的改进共同运行，历史可读导出、旧运行安全处理、新功能达到各自质量门，回滚保留全部合法数据。

**Blocked by:** 39 — 盲评有人味表达收益与交流边界；40 — 验证同模型同预算的连续性与成本；41 — 评测画像提取、治理与实际回答改善；42 — 验证工作流质量、预算和外部真实可得性

**Status:** ready-for-agent

**优先级：** P0

## 背景与需求

单票通过不能保证共同状态/流式/预算/撤回和历史迁移接缝一致；集成不能用清库或恢复退役入口回滚。

## 实施前必读

- [AGENTS.md](../../../AGENTS.md)、[CONTEXT.md](../../../CONTEXT.md)、[本批执行说明](../README.md)；先核对阻塞票已实施并验收，不能只看其状态字段。
- [docs/workflow/delivery-and-validation.md](../../../docs/workflow/delivery-and-validation.md)。
- [docs/workflow/orchestration.md](../../../docs/workflow/orchestration.md)。
- [docs/上下文工程/改进方案.md](../../../docs/上下文工程/改进方案.md)。
- [docs/人味化/实施方案.md](../../../docs/人味化/实施方案.md)。
- [docs/用户画像/讨论记录.md](../../../docs/用户画像/讨论记录.md)。
- [docs/用户画像/提取时机与回答应用流程.md](../../../docs/用户画像/提取时机与回答应用流程.md)。
- [docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)。
- [docs/adr/0031-runtime-qwen-credential-and-main-model-activation.md](../../../docs/adr/0031-runtime-qwen-credential-and-main-model-activation.md)。

## 任务内容

1. 对照本批 COVERAGE 的全部决策/问题/脚本/场景完成最终对账，检查各票实现和真实验收，不以文档或 ready 状态宣布上线。
2. 收敛 expand–migrate–contract：旧消息模块标识、新路由、旧结果只读、节点谱系、旧题/判定与新评分、画像旧记录/墓碑均有版本解释；无新消费者前不删兼容读取。
3. 不兼容配方旧运行安全结束或迁移为明确新运行，不把新图套旧谱系；新运行恢复前重查权限、材料/画像、任务/小节版本及原预算。
4. 验证每票自己完成新增状态迁移、备份/导出、删除、审计与失败恢复；共同 Schema/API 生成合同同步，不把遗漏留到此票临时重建。
5. 完整跨模块与学习回归、账户隔离、停止/迟到、终态/SSE/正文一致和三个桌面视口真实 API/执行器验收；全部自然语言来源接入策略/模板。
6. 质量报告依人味帮助/分寸非劣、上下文硬门/连续性、画像实际改善、编排/外部上线门分别裁决；样本不足或必要门失败只开放已通过范围。
7. 回滚停新写/策略路径并保留新旧任务、事件和产物，不清库、不丢任务、不恢复退役媒体/提醒/插件。提示策略回滚不撤销确定性保护/协议修复。
8. 最终补正式文档、迁移/运行/回滚说明与已实现/待实测清单，写明真实限制和恢复办法；发布或部署操作按用户当前授权处理，本票不默认要求对外部署。

## 跨票接缝与责任

只负责最终组合/兼容与放行验证；共享迁移/注册表/生成合同由集成人员顺序合入，禁止以最终票承接前票未完成实现。

开发与验证使用 conda `agent`。保持现有账户隔离、首条消息后的模式固定、会话附件与全局知识库分域及历史可读/导出。新增持久状态须在本票内完成适用的版本化迁移、备份/导出、删除、审计和失败恢复，不留给最终集成票补做。仅修改本票必要接缝；不恢复退役创作、提醒、用户插件或项目工作台。

## 验收标准

- [ ] 全部覆盖项有实现/验收落点，画像未决规格已按用户确认处理，无静默遗漏。
- [ ] 旧消息/结果/判定/来源可读可导出，新旧迁移对账与回滚可验证。
- [ ] 不兼容旧运行安全处理，恢复不复活失效材料或重置预算。
- [ ] 跨账户/硬条件/重复判定/停止非法写入等发布不变量全通过。
- [ ] 三个桌面视口真实链路和全自然语言路径通过，终态/流式/重放一致。
- [ ] 各效果/成本报告和外部门真实可信，未通过范围明确停用/降级而不包装成功。

## 验证与交付证据

执行正式回归、迁移/恢复/回滚演练和覆盖对账，保留实际报告；实现完成与发行/部署分开记录。

记录实际代码/合同版本、运行环境、测试及其限制。确定性模型/工具响应只能证明机制，真实模型体验和外部可得性分别按评测票验证。本票完成时补充实现说明、接口/迁移变化与验证结果，维护阻塞消费者可用的接缝；设计文档和历史基线通过数不能充当本次实施通过证据。

## 实施记录（2026-10-06）

分支 `codex/43-integrated-migration-and-release-regression`（基于 main `a9dda10a`）。
开工前逐票核对 39/40/41/42 已合入 main 的实际验收记录与产物，未发现前票未完成的
实现；本票只收敛集成接缝与最终对账。

### 交付内容

- 生产接缝（R06）：`src/bridges/chat/graph.py` 为日常图补版本守卫——
  `run.graph_version` 非空且不等于 `daily-parent-v2` 时以
  `daily_graph_version_changed` 安全失败（可重试创建绑定当前版本的新运行），
  不再把新图套旧谱系；`None`（迁移 49 前）按首次执行兼容放行。错误文案登记
  `error.daily_graph_version_changed`（`state_copy/catalog.py`），重试码加入
  `chat/turn.py`。
- 覆盖对账：`src/bridges/evaluation/integration_coverage*.py` 登记 166 项
  （COVERAGE.md 127 条需求映射 + 42 的 39 个场景），逐项绑定负责票与可执行
  证据锚点；`validate_coverage_reconciliation` 与来源文档逐行对账，任何一边
  缺失即失败。
- 回滚演练：`src/bridges/evaluation/rollback_rehearsal.py` 在真实文件数据库上
  执行 12 项断言（策略资源缺失降级/旧快照复用/画像保留与恢复采用/确定性
  保护不撤销/停新写不缩水/退役能力不复活）。
- 放行裁决：`src/bridges/evaluation/release_adjudication.py` 依独立验收记录
  分别裁决，39 保持 `not_released`，42 按外部门降级登记，探针产物与场景报告
  一致性参与校验。
- 报告脚本：`scripts/run_issue43_acceptance_reports.py` 生成
  `.scratch/2/validation/43-integrated-migration-and-release-regression/`
  `coverage-reconciliation.json`、`release-adjudication.json`、
  `rollback-rehearsal.json`。
- 测试：`tests/evaluation/test_issue43_coverage_reconciliation.py`、
  `test_issue43_release_adjudication.py`、`test_issue43_rollback_rehearsal.py`、
  `test_issue43_natural_language_adoption.py`、
  `tests/orchestration/test_issue43_compat_seams.py`（过期配方/能力版本拒绝、
  旧综合产物契约不复用）、`tests/chat/test_improvement43_legacy_graph_guard.py`。
- 文档：`docs/runbooks/integrated-release-and-rollback.md`（发布范围/迁移事实/
  运行命令/回滚办法/已实现-待实测清单/证据索引）。

### 验证结果

- 覆盖对账：166/166，`problems=0`；裁决台账通过（39 not_released；40 released；
  41 released_with_limits；42 released_with_degradation）。
- 回滚演练：12/12 通过（报告留存）；`tests/lifecycle/test_restore_consistency.py`
  同时证明恢复会重跑退役清理。
- 本票新增测试 15 passed；`tests/state_copy` 30 passed；重点套件
  （evaluation/state_copy/lifecycle/orchestration/kernel/storage 迁移/聊天接缝）
  514 passed，9 failed+2 与 main 基线逐项相同。
- 全量回归：5607 passed / 226 failed+errors / 39 skipped；main `a9dda10a`
  全新隔离目录 5592 passed / 226 failed+errors / 39 skipped，失败集合逐项
  相同（`baseline-comparison.json`），分支只多出 15 项新测试全部通过。
- 桌面三视口（1280×720、1440×900、1920×1080，真实 API+执行器+SQLite，零
  页面 mock）：`issue21-desktop-acceptance` 8 passed / 4 failed，其中 1920×1080
  六模块用例因高德上游超时失败、单独重试通过；3 项「模型验证」断言因隔离
  E2E 环境无 Qwen 凭据失败（页面如实显示「未配置」，main 同样 3 项失败）。
  `issue06-stream-replay-consistency` 3/3 通过（终态/SSE/重放逐字一致）。
- 静态检查：本票生产/测试文件 ruff 通过（`graph.py` 2 个 N818 与
  evaluation 2 个既有文件错误为 main 基线）；mypy 本票新模块无新增错误
  （`graph.py`/`service.py` 等既有错误与 main 逐行相同，仅行号位移）。
- 自然语言来源审计：注册表 216 条覆盖六模块+学习且结构校验通过；39 正式
  路径清单的接缝与证据文件全部存在；`daily_graph_version_changed` 已登记。

### 接口/迁移变化

- 新增错误码 `daily_graph_version_changed` 与固定文案；旧日常图运行行为由
  「可能继续执行新图」改为「安全失败并可重试」，历史消息/运行保留。
- 无数据库 schema 迁移（`SCHEMA_VERSION=69` 不变）；无生成合同变化。

### 限制与未关闭项

- 39 人味收益保持 `not_released`（人工硬门 4 项失败、帮助/分寸有效票不足），
  不因合并改写；41 小样本只证方向；42 外部门 tieba/GitHub 降级、jobs 声明
  partial 未实测、arxiv 保持摘要层。
- 桌面「模型验证失败结论」需先在设置页配置有效 Qwen 密钥；隔离 E2E 环境无
  凭据，3 个视口断言与 main 同样失败，未伪造通过。
- 全量 226 项失败/错误为既有基线（评测底座、旧模块、Windows 占用等），
  本票未处理；非本票引入。
- 图片/视频/语音与离线邮件等非生成来源未登记进状态文案注册表（已记录
  缺口，随覆盖账本保留）。

### 复现

- 环境：Windows；conda `agent`（`C:\Users\33755\anaconda3\envs\agent\python.exe`）；
  非 ASCII 输出设 `PYTHONIOENCODING=utf-8`；Playwright 需
  `BRIDGES_PYTHON` 指向 conda python、`PYTHONPATH=<repo>\src`。
- 命令：`python -m scripts.run_issue43_acceptance_reports`；本票 pytest 套件；
  全量 pytest（JUnit 归档）；`npx playwright test e2e/issue21-desktop-acceptance.spec.ts`
  与 `issue06-stream-replay-consistency.spec.ts`。

### 提交

- 分支提交：`a93a215c`（实现与文档）；本记录与验证产物另行提交。本票不合并、
  不推送，等待独立验收。

