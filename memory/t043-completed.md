---
name: t043-completed
description: T043 完成生命科学与医学高风险领域包
metadata:
  type: project
---

# T043 完成：交付生命科学与医学高风险领域包

## 工作内容

- 新增 `src/science_companion/domain/life_science.py`（`life-science.general-research` v1.0.0）：8 类问题类型（实体身份、注释、关联、机制、表达差异、演化关系、实验重复、数据集解释）；NCBI Datasets/UniProt 来源层级；基因/蛋白标识、关联≠机制、细胞系外推、p 值≠效应量、批次效应、伪重复、旧 accession、未审查注释当定论等确定性规则；病原体增强/危险培养/人体遗传隐私转安全/医学门（H3）。
- 新增 `src/science_companion/domain/medical_high_risk.py`（`medical.high-risk-education` v1.0.0）：8 类问题类型（干预效果、诊断准确性、预后、伤害、筛查、指南推荐、试验状态、患者教育）；WHO 指南手册/ClinicalTrials.gov/系统综述来源层级；PICO 完整性、相对风险不得隐藏绝对效应、试验完成≠结果已发布、地区指南差异人工门；医学禁止场景（个体诊断、处方、剂量调整、停药、急症分流保证、个体预后、替代专业决策）确定性阻断；关键来源状态未知、撤回或证据冲突时高置信发布闭锁；H3 联合门（合资格领域专家 + 安全治理责任人联合确认，Manifest `publication_stage_rules` 与验证报告 `h3_joint_gate` 输出共同声明）。
- 两个包均通过 T040 统一 loader 预检（含能力合同、引用完整性、平台安全下限——篡改测试证明门真实执行）与验证运行时夹具重放（医学 22 夹具、生命科学 18 夹具，0 失败），输出保留 Claim、Evidence、Citation、事实锁引用与验证报告。

## 代码审查修复（T043 审查发现的 bug）

- `_validate_statistics` 死条件 `(A and B and C) or A` ≡ A：p 值与效应量同时声明被误阻断，已修复为仅当 p 值存在且效应量缺失时阻断。
- 相对风险检查依赖 `hides_absolute_effect` 自首开关：未自首的"只有相对风险无绝对效应"claim 放行为 verified，已移除开关，声明相对风险而缺绝对效应即阻断。
- PICO 校验死守卫：仅 intervention_effect 实际检查，诊断/预后/伤害/筛查类型恒报"PICO 字段完整"；已按问题类型映射 PICO 字段集真实检查，assess_evidence 的 pico_completeness 维度与之一致（不适用类型返回 unknown）。
- 医学包来源状态检查只拦 evidence unknown：withdrawn/retracted/superseded/stale 证据实测按已验证放行，已扩展闭锁集合。
- 生命科学包对 evidence unknown 无分支实测 verified：已补 source_status_unknown 闭锁（研究 4.5/8.10：来源状态未知不得按已验证发布）。
- 接线声明未执行的规则：`medical.dose.unit`（缺单位/给药途径的剂量表述阻断，dose_unit_invalid 由死代码变为真实检查）、`life-science.sequence.coordinate`（坐标 1-based/起始≤结束/链方向校验，coordinate_invalid）。
- 死短语"服用…治疗"（字面省略号永不匹配）替换为可执行剂量指令正则；删除 `_validator_ids_for` 冗余分支、`_association_marker` 重复元素；and/or 混合条件加括号。

## 关键实现位置

- `src/science_companion/domain/life_science.py`
- `src/science_companion/domain/medical_high_risk.py`
- `src/science_companion/domain/__init__.py`
- `tests/domain/test_life_science_pack.py`
- `tests/domain/test_medical_high_risk_pack.py`
- `tickets.md`

## 测试结果

- T043 目标测试：33 passed（生命科学 16 + 医学 17）
- domain 测试：63 passed
- 全量测试：879 passed
- mypy（src 门）：通过；Ruff：通过

## 说明

- 已执行 /code-review（Standards + Spec 双轴子代理）并提交 git。
- 后续票据（T046 专家工作台三签、T047 包失效回滚）可直接消费本票的 H3 声明与 `h3_joint_gate` 输出。
