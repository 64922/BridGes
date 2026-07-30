# T040 完成记录：建立领域包协议与验证运行时

对应任务记录：[.scratch/science-companion-plan/issues/18-t040-domain-pack-protocol.md](../.scratch/science-companion-plan/issues/18-t040-domain-pack-protocol.md)

## 交付内容

- 新增 `contracts/domain.py`，定义 `DomainPackManifest`、`DomainRule`、`ValidatorRequirement`、`FixtureCase`、`PackDependencyLock` 以及预检、升级、夹具和运行锁结果合同。
- 新增 `science_companion.domain` 包，提供窄领域协议、Manifest 加载器、规范化 SHA-256 摘要、平台 API 兼容检查、精确依赖锁与循环检查、能力注册表合同校验、包路径安全校验和不可放宽的平台安全下限检查。
- 新增候选验证运行时，按分类、来源计划、元数据/版本状态、结构解析、Claim Schema、证据评估、冲突、措辞约束和 Claim 校验的统一协议流水线重放夹具，并输出通过、闭锁、人工门和失败原因。
- 新增版本目录，保留同一领域包的多个版本；升级要求版本递增，并支持迁移声明、失效策略和受信回滚版本检查。
- 新增 T040 公开接缝测试，覆盖成功加载、能力合同、平台下限拒绝、摘要/引用拒绝、人工门、夹具重放和多版本注册。

## 验证

- `829 passed`
- T040 目标测试：`13 passed`
- `mypy src` 通过（113 个源文件）
- T040 目标文件 Ruff 通过
- Compose/OpenAPI 既有合同测试包含在全量测试中并通过
