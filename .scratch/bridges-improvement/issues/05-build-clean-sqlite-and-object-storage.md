# 05 — 建立干净的 `bridges.db` 与账户隔离加密对象库

Status: ready-for-agent
Blocked by: [02 — 建立独立测试状态和稳定基线](./02-stabilize-isolated-test-baseline.md)
Covered requirements: AUTH-03, ACCOUNT-01, DEPLOY-02, IMP-03
ADRs: [ADR-0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [ADR-0014](../../../docs/adr/0014-start-with-clean-bridges-database.md)

## What to build

创建全新的 `bridges.db` 作为 BridGes 权威数据库，并交付迁移、事务、稳定账户归属和本地加密对象库的最小完整链路。测试应能创建两个账户、分别保存记录与对象、重启服务后读取、阻止跨账户访问并完整删除。旧原型数据库不得作为迁移输入或被原位修改。

## Acceptance criteria

- [ ] 空数据目录首次启动会事务化创建带版本记录的 `bridges.db`，重复启动不会重复迁移或损坏数据。
- [ ] 数据库启用外键与明确事务边界；所有账户所有对象使用不可变内部账户 ID，而非用户名、QQ 邮箱或文件名授权。
- [ ] 对象库使用应用生成 ID 与内容哈希保存对象，原文件名仅作元数据；对象静态路径不能绕过授权直接访问。
- [ ] 对象内容静态落盘前加密，密钥引用不写入数据库明文、日志或 API 响应。
- [ ] 两账户端到端测试覆盖创建、读取、重启恢复、猜测 ID、删除和派生清理，任何数据与对象均不串号。
- [ ] 数据库与对象写入失败时保持一致性；孤立对象或待清理记录进入可观察、可重试状态。
- [ ] 旧数据库只读保留且无自动导入路径；首次启动不会扫描或吸收旧账户、工作流、Stub 状态和测试数据。
- [ ] 运维错误使用中文说明数据目录、权限、迁移版本或对象损坏原因，不输出宿主敏感绝对路径给普通用户。

## Verification

```powershell
conda run -n agent python -m pytest -k "sqlite or migration or object or isolation"
conda run -n agent python -m ruff check .
conda run -n agent python -m mypy src
```

## Non-goals

- 不迁移旧 Science Companion 业务数据。
- 不引入 PostgreSQL、Redis、S3 或多机存储。
- 不在普通数据导出中包含秘密凭据。

## Blocked by

- [02 — 建立独立测试状态和稳定基线](./02-stabilize-isolated-test-baseline.md)

## Comments

对象库的完整备份/恢复与账户删除流程可在后续治理 Issue 扩展；本 Issue 必须先证明最小持久化和账户隔离闭环。
