# 旧 Science Companion 数据库只读封存清单

> 封存对象：旧原型数据库（Science Companion 时代），仅作历史参考。
> 本清单记录文件身份信息（名称、大小、SHA-256、封存时间），**不包含任何
> 数据库内容**；查看本清单不需要打开、复制或输出任何历史秘密值。

## 封存对象

| 文件 | 大小（字节） | SHA-256 | 封存时间 |
| --- | --- | --- | --- |
| `science_companion.db` | 339968 | `f6787a9a50648cffa8359135d09cd07a284cb5d0459cae1abc423b37d850417d` | 2026-08-02 |
| `science_companion.db-wal` | 4181832 | `9d8d218e33cd4f53f877dee5e93f861fea0beefdb56955f279522058e15b47c8` | 2026-08-02 |
| `science_companion.db-shm` | 32768 | `d905a343d0fb8d5f636b89ced146f8b8db724fa6a77d2b4e63859e6d539218aa` | 2026-08-02 |

## 来源与处置说明

- 上述文件是旧原型手工运行遗留物，位于仓库根目录，已由 `.gitignore`
  的 `*.db` / `*.db-wal` / `*.db-shm` 规则排除在版本控制之外；本清单在
  `.scratch/bridges-improvement/` 下入库，只记录身份信息，不携带内容。
- 经核对，旧数据库**不含**真实账户、用户上传材料或有效画像——没有任何
  业务数据被声明为待迁移数据。BridGes 从全新的 `bridges.db` 开始，
  首次启动不会扫描或吸收旧账户、工作流、Stub 状态与测试数据
  （有测试锁定：`tests/storage/test_database_migration.py` 的
  `test_legacy_database_preserved_readonly_and_never_scanned_on_migration`）。
- 旧数据库只读保留：BridGes 初始化不会读取或原位修改该文件，也不存在
  自动导入路径。如未来发现明确的真实用户资产，需另行设计包含预检、
  备份、迁移报告与失败回滚的一次性导入工具（见
  `docs/adr/0014-start-with-clean-bridges-database.md`）。
- 旧 `.scratch/science-companion-plan/` 与根目录旧 `tickets.md` 同样只作
  历史规划材料保留，不构成新计划的任务状态；新工作使用独立的
  `.scratch/bridges-improvement/`。

## 人工动作（待密钥持有人完成）

疑似泄露的百炼 Key 的吊销/轮换为供应商控制台的人工动作，代码代理无法
替代。确认完成时间与操作者的记录由密钥持有人补记，正文不得包含密钥值。
