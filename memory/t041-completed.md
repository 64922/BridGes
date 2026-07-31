---
name: t041-completed
description: T041 完成数学与形式证明领域包
metadata:
  type: project
---

# T041 完成：交付数学与形式证明领域包

## 工作内容

- 新增 `src/science_companion/domain/math_formal_proof.py`，实现数学与形式证明领域包。
- Manifest 声明数学范围、排除项、权威来源角色、定义/符号/证明术语、版本兼容、规则、逻辑能力、冲突规则和评测集。
- 证明校验覆盖 Claim Schema、符号域、符号歧义与冲突、前提、步骤依赖、循环论证、推理规则、目标一致性和形式化语义人工门。
- 领域包输出保留 Claim、Evidence、Citation ID、事实锁引用、逐步 trace、验证报告和版本/构建出处字段；模型生成步骤明确不能直接视为证明。
- 夹具覆盖正确证明、缺失前提、循环论证、符号冲突、无法判定、无效推理、定义、符号等价、定理、反例、数值界和形式化语义复核。

## 关键实现位置

- `src/science_companion/domain/math_formal_proof.py`
- `src/science_companion/domain/__init__.py`
- `tests/domain/test_math_formal_proof_pack.py`
- `tickets.md`

## 测试结果

- T041 目标测试：18 passed
- domain 测试：18 passed
- 全量测试：833 passed
- `mypy src`：117 个源文件通过
- T041 目标文件 Ruff：通过
