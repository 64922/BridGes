# 任务规划：Issue 40 — 建立可复现 A/B 科学评测

状态：已完成并提交（fdeb6a7，2026-08-06）

## 交付内容

- `src/bridges/contracts/evaluation_suite.py`：评测套件契约（套件/案例/运行锁/
  结果/盲评/报告/阈值，约 800 行）
- `src/bridges/evaluation/` 新增 13 个模块：
  - suite_registry.py（版本化注册表 + digest 防静默覆盖 + 失效/取代）
  - suite_data.py（内置 science-baseline@1.0.0：24 案例 × 7 维度，原创数据 +
    许可证记录 + 数据卡）
  - sut.py（6 种被测系统：完整/基线/原创开源参考/三消融）
  - reference_method.py（合法开源参考基线，原创 MIT）
  - executors.py（评测环境：生产缝装配 + 可编程网关 + 本地资产服务器）
  - case_executors.py（各维度执行器：chat/humanizer/教学/生涯/多模态）
  - runner.py（运行锁构建、逐案例隔离执行、双跑对比）
  - metrics.py（七维度确定性指标 + 自动断言 + 否定感知承诺检测）
  - judges.py（确定性裁判 + 固定模型裁判适配器）
  - injection.py（六类注入回归）
  - blind_review.py（匿名化/随机化/一致性/低一致性复核）
  - report.py（点估计/CI/Welch 显著性/失败率/逐切片）
  - gates.py（发布阈值判定，供 Issue 41 接入）
  - repository.py（SQLite 持久化，append-only）
- `src/bridges/storage/database.py`：SCHEMA_VERSION 25 → 26（5 张 eval 表）
- `src/bridges/cli/evaluate.py` + main.py：`BridGes evaluate` 子命令
  （run/replay/blind-review/report/gates）
- `tests/evaluation/`：74 条测试（双跑可复现、六类注入检出、许可证审计、
  匿名盲评、一键重放、报告统计、发布门）

## 验收对照（12 AC + 6 Verification 全部完成）

| AC | 落点 | 验证 |
|----|------|------|
| 1 版本化评测包 | contracts + registry + 运行锁 | test_suite_registry |
| 2 画像闭环 | 3 案例 + profile 指标 | test_metrics + 全案例通过 |
| 3 人味评测 | 4 体裁案例 + humanizer 指标 | 体裁规则真实复核通过 |
| 4 科学评测 | 4 案例 + 高风险切片 | 引用/校准/冲突指标 |
| 5 教学评测 | 3 案例 + 强制联网 | 教学门真实触发 |
| 6 生涯评测 | 3 案例 + 边界 | 否定感知承诺检测 |
| 7 多模态评测 | ASR/TTS/图片/视频/提醒 | 失败注入恢复 |
| 8 对比+消融 | 6 SUT × 7 任务矩阵 | 完整 vs 基线指标显著差 |
| 9 盲评 | 匿名化+一致性 | 低一致性复核 |
| 10 报告 | 统计字段 | Welch 检验测试 |
| 11 版本化追溯 | 锁 digest + append-only | 双跑锁一致 |
| V1 双跑 | compare_double_run | 锁/样本/指标全一致 |
| V2 注入检出 | injection.py | 六类注入全部检出 |
| V3 许可证 | 许可证记录+审计测试 | 无外部项目文本 |
| V4 盲评者 | CLI 盲评流程 | 匿名化测试 |
| V5 失败回溯 | 确定性重放命令 | replay CLI 实测 |
| V6 发布阈值 | gates.py | 低于阈值阻止发行 |

## 已知限制（如实记录）

- 确定性模式用可编程网关（脚本编码"模型质量随上下文变化"的固定行为）；
  真实模型运行可替换网关（运行锁记录网关版本）。
- TTS 可懂度在确定性模式下仅验证管线完整性（数据卡已披露）。
- tests/integration/test_runtime_smoke.py::test_doctor_smoke 在 Windows
  GBK 控制台下失败（既有问题，干净树同样失败，与本次改动无关）。

## 里程碑

- [x] 契约 + 套件注册表 + 内置数据
- [x] 运行器 + SUT 适配器 + 执行器
- [x] 指标 + 裁判 + 注入
- [x] 盲评 + 报告 + 阈值
- [x] 持久化迁移 26 + CLI
- [x] 评测测试 74 条全过
- [x] 全量回归（2067+74 通过，1 个既有 Windows 控制台 flaky 除外）
- [x] /code-review 双轴审查并修复（12 项：死代码/重复/盲评维度/失败率/容差键/稳定性门/报告版本/阈值落库/错误判定/引用校验/完整性门/承诺否定）
- [x] 提交（fdeb6a7）
