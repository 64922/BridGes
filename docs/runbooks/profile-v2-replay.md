# profile-auto-v2 安全回放运行手册

本手册用于把历史 profile-auto-v1 的两类记录安全地回放到 v2：

- exhausted 且 last_error 以 client_error_400 开头；
- succeeded 且 attempts = 0。

回放只接受运行时统一配置 BRIDGES_DATABASE_URL（或等价的应用状态）解析出的权威 SQLite。不会把仓库根目录的 bridges.db 当作默认库，也不会在回放过程中隐式执行 schema migration。

## 执行顺序

先停止 API、worker、scheduler 和其他会写入同一数据目录的进程，然后检查权威库：

~~~
BridGes profile-replay inspect
~~~

输出只包含规范化路径、schema 版本、身份摘要、文件大小和写入者状态，不包含账户或消息正文。若 schema 不等于当前支持版本，单独执行：

~~~
BridGes profile-replay schema-upgrade
~~~

schema upgrade 完成后重新执行 inspect。随后创建不可覆盖的备份：

~~~
BridGes profile-replay backup --target .backups/profile-replay-v2.db
~~~

备份必须同时通过 SQLite 完整性检查、hash 校验和临时还原探针。之后先做严格只读 dry-run：

~~~
BridGes profile-replay dry-run
~~~

确认计数和跳过原因后，使用同一份带 manifest 的备份执行正式回放。确认词必须显式提供：

~~~
BridGes profile-replay replay --backup .backups/profile-replay-v2.db --confirm 回放 --drain
~~~

drain 会在 profile-replay-v2 专用队列上运行受监督 worker；不使用旧的 profile-extraction 队列。也可以分开运行：

~~~
BridGes profile-replay replay --backup .backups/profile-replay-v2.db --confirm 回放
BridGes profile-replay worker
~~~

重复 dry-run、入队或 worker 是幂等的：每条 v1 extraction_id 都有独立的 v2 run/task 边界；同一账户、消息、v2 抽取器版本和分类值不会生成重复 observation 或画像记录。

## 安全硬门

以下任一条件出现时，操作失败并保持数据库不变：

- 配置、应用状态和显式候选路径冲突；
- 权威路径不存在、不是 SQLite，或无法确认 schema；
- 存在活动写入者，或锁状态无法确认；
- schema 未先完成显式升级；
- 备份缺失、hash 不匹配、schema 不符或还原探针失败；
- 消息已删除/tombstone、账户或内容被隐私阻止、画像记录已撤回；
- v2 负向门命中引用、第三方、假设/角色扮演、否定或敏感内容；
- 内容发生变化，导致 source hash 不再匹配。

dry-run 和正式报告只保留版本、审计 ID、数据库身份摘要、分类计数与失败原因码，不输出账户 ID、消息 ID 或原始正文。v1 run 和审计记录保留，v2 报告写入 profile_four_dimension_migrations。

## 回滚

回滚前停止所有 BridGes 写入进程并取得维护锁。不要覆盖原库；先把当前库移动到隔离目录，再把已验证备份复制为权威路径，最后重新执行 inspect 和完整性检查。保留原库、备份及 manifest，待人工确认后再清理。
