# 安全事件 001：疑似泄露的百炼 Key 处置记录

> 本文档为中文安全说明，记录疑似泄露密钥的事件处置、恢复方式与旧数据
> 边界。任何检查输出、日志或文档示例都不得包含秘密值正文。

## 事件概述

开发原型阶段使用的百炼（Qwen）API Key 被怀疑可能泄露。该 Key 仅用于
原型验证，不承载生产流量。处置目标：使旧 Key 失效、把项目从依赖旧 Key
的运行配置中摘除、固化防回归检查，并封存旧原型数据边界。

## 事件处置

1. **供应商侧吊销/轮换（人工动作）**：由密钥持有人在百炼控制台吊销或
   轮换疑似泄露的 Key。本动作不可由代码代理替代；完成时间与操作者以
   不包含密钥正文的记录确认（补记位置：
   `.scratch/bridges-improvement/sealed-legacy-science-companion.md`）。
2. **运行配置摘除**：旧 Key 已从当前运行配置、开发脚本、测试夹具、
   日志样例与文档示例中移除。当前配置只接受通过环境变量或
   `<NAME>_FILE` 秘密文件引用提供的新凭据（见 `src/bridges/config.py`），
   任何载体不读取 `.env`，也不把秘密写入普通配置文件。
3. **防回归检查**：`tests/security/test_secret_scan.py` 对仓库源码、
   配置、脚本与文档执行秘密扫描（百炼 Key、SMTP 授权码、私钥、会话
   令牌等格式），阻止秘密被提交；扫描输出只报告位置，不回显秘密值。
4. **检查输出纪律**：任何检查输出（测试、CLI doctor、健康检查、
   日志）不得回显秘密值；`settings` 的 repr 通过 `SecretStr` 脱敏，
   可观测数据经 `src/bridges/observability/scrubber.py` 清洗。

## 恢复方式

- **新 Key 配置**：通过环境变量 `BRIDGES_QWEN_API_KEY`（或
  `BRIDGES_QWEN_API_KEY_FILE` 文件引用）提供，不写入仓库文件。
- **本地状态解密**：若配置了数据库，`BRIDGES_SECRET_KEY` 是本地加密
  对象库与状态存储的派生密钥来源；更换主密钥会导致既有密文不可读，
  需要按数据目录整体备份/恢复流程处理（密钥文件由容器入口在数据卷
  内自举，见 `apps/api/entrypoint.sh`）。
- **异常终止恢复**：单实例锁由操作系统在进程退出时自动释放，异常
  终止后重新启动不会丢失已提交数据（WAL 保护，见
  `src/bridges/runtime/lock.py` 与 `src/bridges/storage/database.py`）。

## 旧数据不可自动迁移边界

- 旧 Science Companion 数据库（`science_companion.db` 及 WAL/SHM 文件）
  只作只读历史保留，身份信息见封存清单
  （`.scratch/bridges-improvement/sealed-legacy-science-companion.md`）；
  BridGes 初始化不会读取或原位修改该文件，无自动导入路径。
- 旧 `.scratch/science-companion-plan/` 与根目录旧 `tickets.md` 同为
  历史材料，不构成新计划的任务状态。
- 只有未来发现明确的真实用户资产时，才另行设计包含预检、备份、
  迁移报告与失败回滚的一次性导入工具（见
  `docs/adr/0014-start-with-clean-bridges-database.md`）。

## 人工验收项

- [ ] 密钥持有人确认旧 Key 已不可用，并补记完成时间与操作者。
- [ ] 检查封存清单时不得打开、复制或输出任何历史秘密值。
