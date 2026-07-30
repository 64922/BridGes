# T040 建立领域包协议与验证运行时

Type: task
Status: resolved
Blocked by: 09, 11, 16

## Question

建立领域包稳定协议、加载器和候选验证运行时，让来源、规则、校验器、夹具、依赖、版本、迁移、失效、撤权和平台安全下限能够独立登记，并通过统一主接缝运行。实现应覆盖 Manifest Schema、摘要与依赖锁、能力合约、平台安全下限、Claim—Evidence—Wording 覆盖、夹具重放、人工门、升级兼容、迁移、失效传播和受信回滚。

## Answer

已完成 T040：

- 新增 `science_companion.contracts.domain`，提供 `DomainPackManifest`、`DomainRule`、`ValidatorRequirement`、`FixtureCase`、`PackDependencyLock` 以及来源策略、Claim Schema、措辞、迁移、失效、撤权、回滚等结构化合约。
- 新增 `science_companion.domain`，提供领域包 Protocol、Manifest 预检/加载器、版本目录和候选验证运行时。
- 加载器验证 Schema、语义版本、平台 API、SHA-256 摘要、依赖锁闭包、稳定 ID、引用完整性、路径安全、能力注册表和不可放宽的平台安全下限。
- 运行时按生产协议重放夹具，输出通过、失败、闭锁或人工门结果；未知状态和校验器异常不会被降级为成功。
- 升级验证要求兼容范围、major 迁移、历史保留、夹具重放、失效闭锁、撤权闭锁和受信回滚白名单；版本目录保留历史版本并支持精确选择。
- 研究依据：[初始领域包治理](../research/12-initial-domain-pack-governance.md) 和 [科学可信系统](../research/03-scientific-trust-system.md)。

## Verification

- `conda run --no-capture-output -n agent python -m pytest tests/domain/test_domain_runtime.py -q`：13 passed。
- 全量测试：829 passed。
- `mypy src`：通过。
- T040 目标文件 Ruff、`git diff --check` 和公开导入冒烟检查：通过。

## Comments

- 2026-07-30：补充结构化领域策略与升级闭锁，避免关键协议退化为不可验证的自由字典。
