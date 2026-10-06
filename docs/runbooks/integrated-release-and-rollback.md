# 集成发布、迁移与回滚说明（工单 43）

日期：2026-10-06。适用提交：`codex/43-integrated-migration-and-release-regression`
（基于 main `a9dda10a`）。本说明覆盖本批（改进工单 21–43）共同状态的发布范围、
迁移事实、验证命令与回滚办法；实现完成与对外部署分开记录，本票不默认部署。

## 1. 发布范围与裁决

| 票 | 范围 | 裁决 | 开放口径 |
| --- | --- | --- | --- |
| 39 | 人味化表达与交流边界 | `not_released` | 保持基线表达，不宣称人味收益 |
| 40 | 上下文连续性与成本 | `released` | 同模型同预算连续性与成本报告范围 |
| 41 | 画像提取、治理与回答改善 | `released_with_limits` | 评测范围开放；小样本只证方向 |
| 42 | 工作流质量、预算与外部门 | `released_with_degradation` | 机制与预算初值开放；外部门逐层降级 |

对账账本 `src/bridges/evaluation/integration_coverage.py` 登记 166 项
（COVERAGE.md 127 条需求映射 + 42 的 39 个场景），裁决台账
`src/bridges/evaluation/release_adjudication.py` 只依据跟踪在库的独立验收
记录，不因合并升级 39 的结论。

## 2. 本批收敛的迁移事实（expand–migrate–contract）

- **数据库**：`SCHEMA_VERSION = 69`；启动迁移前经在线备份 API 落快照
  （`*.backup-before-*`），scoped 连接强制账户 id；历史备份仍可恢复。
- **旧消息**：`messages.module_id` 等历史列只读保留，新路由不改写历史
  行；旧结果（图片/视频/语音、旧检索决策、旧合同产物）继续可读可导出。
- **旧运行**：日常图与学习图各自带 `graph_version`；非空且不等于当前
  版本的运行以 `daily_graph_version_changed` / `study_graph_version_changed`
  安全结束（可重试创建绑定当前版本的新运行），不把新图套旧谱系；
  迁移 49 之前的 `NULL` 版本按首次执行兼容放行。
- **配方/产物契约**：计划步骤引用过期配方或能力版本被
  `RECIPE_VERSION_MISMATCH` 拒绝；`composite-orchestration-v1` 之外的旧
  综合产物不复用，按新契约生成明确新运行；内核按
  `recipe_id/recipe_version/capability_version/schema_version` 判定重用，
  旧收据保留不删除。
- **画像**：旧四维记录经迁移批次台账（`profile_item_migrations` +
  逐条记录）折算原子条目，墓碑与撤回版本可审计；迁移报告支持撤销。

## 3. 验证与运行命令

环境：conda `agent`；联网走用户网络代理。所有命令在仓库根执行。

```powershell
# 覆盖对账 + 放行裁决 + 回滚演练（生成正式报告到 .scratch/2/validation/43-.../）
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='src;.'
python -m scripts.run_issue43_acceptance_reports

# 本票新接缝回归
python -m pytest tests/evaluation/test_issue43_*.py `
  tests/orchestration/test_issue43_compat_seams.py `
  tests/chat/test_improvement43_legacy_graph_guard.py -q

# 完整回归（JUnit 归档；基线失败与 main 逐项一致见报告）
python -m pytest -q --junitxml=.scratch/2/validation/43-.../pytest-full.xml

# 静态检查（本票文件）
python -m ruff check src/bridges/evaluation scripts/run_issue43_acceptance_reports.py
python -m mypy src/bridges/evaluation
```

桌面三视口真实链路（1280×720、1440×900、1920×1080，真实 API + 后台执行器
+ SQLite，零页面 mock）：

```powershell
$env:BRIDGES_PYTHON='C:\Users\33755\anaconda3\envs\agent\python.exe'
$env:PYTHONPATH='<repo>\src'   # Playwright 启动的 API/worker 需要
cd apps/web; npm ci
npx playwright test e2e/issue21-desktop-acceptance.spec.ts --project=chromium
npx playwright test e2e/issue06-stream-replay-consistency.spec.ts --project=chromium
```

## 4. 回滚办法

回滚 = 停新写/策略路径并部署上一版本，**不清库、不丢任务/事件/产物、
不恢复退役能力**。演练模块 `src/bridges/evaluation/rollback_rehearsal.py`
在真实文件数据库上执行 12 项断言（表达式策略资源缺失降级、旧快照重试
复用、画像条目与采用快照保留且资源恢复后可重新采用、事实保护协议常量
与保护区绑定不随提示策略回滚撤销、停新写后库完整且全部登记表可读、
清单不缩水、技能/插件/提醒保持退役且清理幂等）。

```powershell
python -m scripts.run_issue43_acceptance_reports  # 含 rollback-rehearsal.json
```

- 备份恢复路径会重跑退役清理（`tests/lifecycle/test_restore_consistency.py`
  断言恢复后提醒状态为 `retired`）。
- 旧版本运行不会复活失效材料或重置预算：恢复前重查权限、材料/画像、
  任务/小节版本与原预算由各票守卫负责；不兼容配方按第 2 节安全处理。
- 提示策略回滚只影响表达快照选择，不撤销 `fact_protection` 等确定性
  协议修复；可回退到安全基线或旧快照（快照按运行持久化）。

## 5. 已实现 / 待实测清单

已实现并有机制证据：覆盖对账与裁决；R06 日常图版本守卫与错误文案；
旧日常/学习运行安全结束；过期配方/能力与旧综合产物契约拒绝；内核契约
升级重跑保留旧收据；回滚演练；三视口真实模块链路（模块用例 3/3，
1920 首次因高德上游超时重试通过）、流式/终态/重放一致（3/3）。

待实测或环境受限（不包装成功）：

- 39 人味收益：人工盲评票数不足，`not_released`，接口开放但收益不宣称。
- 41 画像真实收益：14 次调用、配对 8/8 只证方向；桌面 E2E 页面断言受限。
- 42 外部门：tieba/GitHub 探针未通过（诚实降级），jobs 声明 partial 未实测，
  arxiv 产品保持摘要层。
- 桌面模型验证路径：「失败验证给出未通过结论」需要先在设置页配置有效
  Qwen 密钥；隔离 E2E 环境无凭据，该 3 个断言与 main 同样失败，属实
  显示「未配置」而不是伪造通过。
- 全量回归 226 项失败/错误在 main `a9dda10a` 全新隔离环境逐项相同，
  属既有基线（评测底座、旧模块、环境占用等），本票未处理。

## 6. 证据索引

- `.scratch/2/validation/43-integrated-migration-and-release-regression/`
  `coverage-reconciliation.json`、`release-adjudication.json`、
  `rollback-rehearsal.json`、`pytest-full.xml`、`pytest-focused.xml`、
  `desktop-three-viewports.log`、`desktop-stream-replay.log`、
  `desktop-1920-modules-retry.log`、`baseline-comparison.json`
- 阻塞票独立验收：`.scratch/2/validation/39-independent/acceptance.md`、
  `40-context-continuity/independent-acceptance.md`、
  `41-independent/acceptance.md`、42 场景/探针产物。
