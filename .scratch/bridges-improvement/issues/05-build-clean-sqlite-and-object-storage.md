# 05 — 建立干净的 `bridges.db` 与账户隔离加密对象库

Status: ready-for-human
Blocked by: [02 — 建立独立测试状态和稳定基线](./02-stabilize-isolated-test-baseline.md)
Covered requirements: AUTH-03, ACCOUNT-01, DEPLOY-02, IMP-03
ADRs: [ADR-0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [ADR-0014](../../../docs/adr/0014-start-with-clean-bridges-database.md)

## What to build

创建全新的 `bridges.db` 作为 BridGes 权威数据库，并交付迁移、事务、稳定账户归属和本地加密对象库的最小完整链路。测试应能创建两个账户、分别保存记录与对象、重启服务后读取、阻止跨账户访问并完整删除。旧原型数据库不得作为迁移输入或被原位修改。

## Acceptance criteria

- [x] 空数据目录首次启动会事务化创建带版本记录的 `bridges.db`，重复启动不会重复迁移或损坏数据。
- [x] 数据库启用外键与明确事务边界；所有账户所有对象使用不可变内部账户 ID，而非用户名、QQ 邮箱或文件名授权。
- [x] 对象库使用应用生成 ID 与内容哈希保存对象，原文件名仅作元数据；对象静态路径不能绕过授权直接访问。
- [x] 对象内容静态落盘前加密，密钥引用不写入数据库明文、日志或 API 响应。
- [x] 两账户端到端测试覆盖创建、读取、重启恢复、猜测 ID、删除和派生清理，任何数据与对象均不串号。
- [x] 数据库与对象写入失败时保持一致性；孤立对象或待清理记录进入可观察、可重试状态。
- [x] 旧数据库只读保留且无自动导入路径；首次启动不会扫描或吸收旧账户、工作流、Stub 状态和测试数据。
- [x] 运维错误使用中文说明数据目录、权限、迁移版本或对象损坏原因，不输出宿主敏感绝对路径给普通用户。

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

已完成（2026-08-02）：实现与双轴代码审查（Standards + Spec）修复完毕，
完整验证通过（`pytest -k "sqlite or migration or object or isolation"` 112 个
通过、完整测试集 1022 passed、ruff/mypy 对新增与改动文件全绿）。Status
标记 `ready-for-human` 表示等待人工验收。工作内容与 bug 修复总结见提交说明；
核心交付如下：

1. **版本化事务数据库** `src/bridges/storage/database.py`：空数据目录首次启动
   事务化创建带 `schema_meta` 版本记录的 `bridges.db`（外键/WAL/完整同步）；
   重复启动只补齐缺失迁移，幂等且不损坏数据；迁移版本高于程序时报中文错误。
2. **账户隔离加密对象库** `src/bridges/storage/object_store.py` +
   `repository.py`：对象以应用生成 ID 为主键、内容哈希为落盘路径，原文件名
   仅作元数据；对象内容静态落盘前用 `BRIDGES_SECRET_KEY` 派生 Fernet 加密，
   密钥引用不落数据库、不写日志与 API 响应；所有读取按不可变内部账户 ID
   过滤，跨账户访问与不存在的对象返回同一中文错误，杜绝 ID 枚举。
3. **一致性链路**：写入失败时文件与元数据行保持一致的回收语义；删除先标记
   `pending_cleanup`（可观察、可重试，含重试次数与中文原因），成功后整行与
   物理文件完整删除；孤立文件可被 `find_orphans`/`cleanup_orphans` 检测与
   清理。
4. **接线**：`create_app` 配置数据库时初始化 `bridges.db` 并挂接对象仓库，
   健康检查上报 `bridges_storage` 可选依赖；CLI `BridGes migrate` 实际执行
   事务迁移（保留既有 "environment:"/"migrate:" 输出契约）。
5. **旧数据库只读保留**：数据目录中的 `science_companion.db` 字节不变、无
   自动导入路径，首次启动不扫描或吸收旧账户（有测试锁定）。
6. **测试**：`tests/storage/` 18 个测试覆盖迁移幂等/外键/版本回退/损坏报错、
   密文落盘/哈希校验/去重、双账户 E2E（创建/读取/重启恢复/猜 ID/删除与派生
   清理/不串号）、共享内容跨账户删除、失败回滚、待清理可重试与孤儿清理。
