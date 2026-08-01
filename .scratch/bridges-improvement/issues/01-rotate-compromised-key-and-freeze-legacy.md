# 01 — 轮换疑似泄露的百炼 Key 并封存旧原型

Status: ready-for-human
Blocked by: None
Covered requirements: IMP-03, MODEL-02
ADRs: [ADR-0005](../../../docs/adr/0005-per-account-qwen-key-and-capability-probes.md), [ADR-0014](../../../docs/adr/0014-start-with-clean-bridges-database.md), [ADR-0016](../../../docs/adr/0016-incremental-vertical-replacement.md)

## What to build

完成实现前的安全止血和历史边界固化：由密钥持有人在百炼控制台轮换或吊销疑似泄露的旧 Key，确认项目、测试、日志和文档不再依赖该值；为旧 Science Companion 数据库生成带时间戳、哈希和来源说明的只读封存清单。后续 BridGes 使用全新数据库，旧数据库、旧 `.scratch/science-companion-plan/` 与旧 `tickets.md` 只作历史参考，绝不自动迁移、认领或更新状态。

## Acceptance criteria

- [ ] 密钥持有人已在供应商侧吊销或轮换疑似泄露的 Key，并以不包含密钥正文的记录确认完成时间和操作者。
- [ ] 旧 Key 已从当前运行配置、开发脚本、测试夹具、日志样例和文档示例中移除；任何检查输出都不得回显秘密值。
- [ ] 代码库具备秘密扫描或等价检查，能够阻止百炼 Key、SMTP 授权码、密码和会话令牌被提交。
- [ ] 旧数据库已生成只读封存清单，记录文件名、大小、哈希和封存时间；没有真实账户、上传材料或有效画像被声明为待迁移数据。
- [ ] BridGes 初始化不会读取或原位修改旧数据库，也不会修改旧 Wayfinder `.scratch` 与根目录旧 `tickets.md`。
- [ ] 安全文档使用中文，明确说明事件处置、恢复方式和旧数据不可自动迁移的边界。

## Verification

```powershell
conda run -n agent python -m pytest -k "credential or secret or legacy"
conda run -n agent python -m ruff check .
git status --short
```

人工验证：由密钥持有人在百炼控制台确认旧 Key 已不可用；检查封存清单时不得打开、复制或输出任何历史秘密值。

## Non-goals

- 不恢复、清洗或迁移旧原型业务数据。
- 不把新 Key 写入 `.env`、源代码、Issue、测试或日志。
- 不修改旧 `.scratch/science-companion-plan/` 和旧 `tickets.md`。

## Blocked by

None - can start immediately.

## Comments

该 Issue 需要密钥持有人完成供应商控制台操作，因此状态为 `ready-for-human`。代码代理可以完成扫描、封存清单和防回归测试，但不能替代真实吊销动作。
