---
name: t042-completed
description: T042 完成物理与化学实验测量领域包
metadata:
  type: project
---

# T042 完成：交付物理与化学实验测量领域包

## 工作内容

- 新增 `src/science_companion/domain/physics_chemistry.py`，实现物理与化学实验测量领域包。
- Manifest 声明物理/化学范围、排除项、权威来源角色（BIPM SI、NIST CODATA、IUPAC Gold Book）、问题类型、Claim Schema、措辞策略、规则、逻辑能力、冲突规则和评测集。
- 校验覆盖单位量纲一致性、温度换算规则、ppm 基准、测量不确定度、误差/不确定度区分、有效数字、CODATA 年份、化学方程式原子守恒、反应条件、质量/重量概念、mol/molecule 概念和危险实验安全门。
- 领域包输出保留 Claim、Evidence、Citation ID、事实锁引用、验证报告和版本/构建出处字段；模型生成步骤明确不能直接视为已验证实验结论。
- 夹具覆盖正确测量、单位换算、摄氏温标误用、质量/重量混淆、mol/molecule 混淆、过期 CODATA 常数、化学方程式不平衡、危险实验建议（含危险机理）、证据冲突、ppm 基准缺失、误差/不确定度混淆、有效数字夸大和缺失不确定度。

## 代码审查修复（T042 审查发现的 bug）

- 危险实验安全门补覆盖 `mechanism_under_conditions`（原仅 experimental_comparison，危险机理可绕过安全门放行）。
- 补 `evidence_conflict` 检测（支持+反驳证据并存进入 conflicted），conflicted/人工门状态真实可达。
- 删除 CODATA 不同年份误判冲突（spec：不同调整年份不是真冲突，未声明年份由 codata_year_outdated 处理）。
- 规则 applies_to 与执行路径对齐（unit.dimension 覆盖 quantity/constant、measurement.uncertainty 覆盖 calibration/comparison、mol_molecule/mass_weight 收紧到 measurement_result）。
- 新增 ppm 基准缺失、误差/不确定度混淆两条规则与夹具（研究 5.2 夹具清单补齐）。
- assess_evidence 输出 uncertainty_declared/conditions_specified 维度（与 Manifest evidence_dimensions 一致）；删除 human_reasons 死参数与死条件。

## 关键实现位置

- `src/science_companion/domain/physics_chemistry.py`
- `src/science_companion/domain/__init__.py`
- `tests/domain/test_physics_chemistry_pack.py`
- `tickets.md`

## 测试结果

- T042 目标测试：12 passed
- 全量测试：846 passed
- `mypy src/science_companion/domain/physics_chemistry.py src/science_companion/domain/__init__.py`：通过
