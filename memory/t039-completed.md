---
name: t039-completed
description: T039 完成因果同步、冲突分支、撤权和删除墓碑
metadata:
  type: project
---

# T039 完成：因果同步、冲突分支、撤权和删除墓碑

## 工作内容

- 新增 `SyncOperation`、`ConflictBranch`、`DeviceAck`、`Tombstone`、`SyncControlSnapshot` 等同步合同。
- 新增控制面优先的 `SyncService`：同步前拉取授权版本、设备状态、密钥时期和墓碑；旧密钥、撤权设备、旧授权版本的提交进入隔离区。
- 未配对设备不能提交；已配对操作使用设备 RSA 公钥验签，操作幂等键重放和冲突占用都会保留原始审计记录。
- 删除操作写入不可逆墓碑；墓碑优先于离线更新，防止删除对象被复活。
- 共享项目同步复用成员授权；科学判断、事实锁、归属、授权等语义字段的并发修改保留本地与远端两个分支，不使用最后写入者获胜；删除与编辑并发也进入人工裁决，裁决会写入新版本和墓碑/操作 Outbox。
- 已接受操作追加到持久化同步出箱，控制面可向同账户副本返回待投递操作。
- 操作日志、对象版本、冲突分支、墓碑、设备确认和控制状态接入 SQLite 命名空间持久化，服务重建后同步边界仍然有效。
- `create_app` 支持 `SCIENCE_COMPANION_DATABASE_URL`；配置数据库时用 `SCIENCE_COMPANION_SECRET_KEY` 加密 SQLite 状态，本地使用 SQLite，生产未配置持久化时就绪检查失败，不再静默回退到内存。
- Qwen 配置沿用 `SCIENCE_COMPANION_QWEN_API_KEY`，真实适配器可按配置注册；评估结果不再把运行时延迟混入确定性重放结果。
- 修复 Windows CLI 帮助输出的 GBK 解码失败和 `python -m` 重复导入警告，并清理本次触及文件中的静态检查问题。

## 关键实现位置

- `src/science_companion/contracts/sync.py`
- `src/science_companion/sync/service.py`
- `src/science_companion/api/sync.py`
- `src/science_companion/persistence.py`
- `src/science_companion/api/main.py`
- `src/science_companion/vault/adapters.py`
- `infra/compose/docker-compose.yml`
- `openapi.json`

## 测试结果

- 全量测试：816 passed
- 类型检查：`mypy src`，110 个源文件通过
- T039、持久化、健康检查、CLI 与 API 相关文件 Ruff 检查通过
- Compose YAML 解析通过

## 后续边界

- 当前持久化适配器是 SQLite JSON 命名空间，适合本地单进程和 Compose 单实例；PostgreSQL 适配器仍需后续任务接入。
- 设备私钥继续只放在内存密钥链适配器中，生产环境应替换为 Windows DPAPI、macOS Keychain 或 Linux Secret Service。
