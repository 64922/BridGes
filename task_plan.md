# Task Plan — Issue 05：建立干净的 bridges.db 与账户隔离加密对象库

状态：进行中（2026-08-02）

## 目标

实现 `.scratch/bridges-improvement/issues/05-build-clean-sqlite-and-object-storage.md` 的全部
验收标准：版本化事务迁移的 `bridges.db`、不可变内部账户 ID 归属、加密对象库、双账户端到端
隔离测试、可观察可重试的清理状态、旧数据库只读保留、中文运维错误。

## 任务清单

1. [x] 新建 `src/bridges/storage/database.py`：BridgesDatabase（PRAGMA 外键/WAL/FULL、
   版本化事务迁移 schema_meta、transaction() 上下文管理器、中文错误无绝对路径）
   → 验证：17 个存储测试覆盖首次创建/重复启动幂等/版本回退/损坏中文报错
2. [x] 新建 `src/bridges/storage/object_store.py`：EncryptedFileObjectStore（内容哈希路径、
   Fernet 落盘加密、哈希校验、损坏中文报错）
   → 验证：单测覆盖密文落盘、明文不可见、损坏检测、哈希去重
3. [x] 新建 `src/bridges/storage/repository.py`：BridgesObjectRepository（accounts 表、
   objects 表外键、按 account_id 授权、删除标记 pending_cleanup 可重试、孤儿文件
   检测清理）
   → 验证：双账户 E2E 测试（创建/读取/重启/猜 ID/删除/派生清理/不串号）
4. [x] 接线：`create_app` 配置数据库时初始化 bridges.db 并挂接对象仓库；CLI `migrate`
   实际执行事务迁移（保留 "migrate:"/"environment:" 输出契约）
   → 验证：test_cli_contract / test_runtime_smoke / test_persistence_api 不回归（全套 1021 通过）
5. [x] 测试文件 `tests/storage/`（test_database_migration.py / test_encrypted_object_store.py
   / test_account_object_isolation.py），测试名含 sqlite/migration/object/isolation 关键词
   → 验证：pytest -k "sqlite or migration or object or isolation" 111 个通过
6. [x] 旧数据库只读保留验证（数据目录放 science_companion.db，初始化后字节不变、不扫描）
   → 验证：test_legacy_database_preserved_readonly_and_never_scanned_on_migration 通过
7. [x] 全量回归：pytest 全套、ruff check、mypy src
   → 验证：1022 passed（基线 1004 + 18 新增）；mypy src 0 错误；
   ruff 新增文件 0 错误（仓库既有 258 个预存错误与本票无关，为环境 ruff 版本漂移）
8. [x] /code-review 代码审查 → 修复发现的 bug → 复跑验证
   → 验证：修复 5 项（共享内容误删/原始 sqlite 错误外泄/delete 返回旧行/
   Fernet 派生重复/死代码），复跑全绿
9. [x] 更新 issue 05 的 Acceptance criteria 勾选状态 + Comments，提交 git
   → 验证：git log 提交信息包含工作总结与 bug 修改总结

## 状态

已完成（2026-08-02）：1022 passed，issue 05 标记 ready-for-human 等待人工验收。

## 验收命令

```powershell
conda run -n agent python -m pytest -k "sqlite or migration or object or isolation"
conda run -n agent python -m ruff check .
conda run -n agent python -m mypy src
```
