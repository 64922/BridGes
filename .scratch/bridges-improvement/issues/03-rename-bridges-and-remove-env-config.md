# 03 — 展开 BridGes 品牌与包名迁移并移除 `.env` 配置

Status: ready-for-agent
Blocked by: [02 — 建立独立测试状态和稳定基线](./02-stabilize-isolated-test-baseline.md)
Covered requirements: UI-02, DEPLOY-02, DEPLOY-03, IMP-03
ADRs: [ADR-0012](../../../docs/adr/0012-source-environment-and-container-deployment.md), [ADR-0016](../../../docs/adr/0016-incremental-vertical-replacement.md)

## What to build

以可回滚的展开—迁移方式建立 BridGes 产品名、Python 分发名、命令入口、前端包名、中文运行文案和本地配置边界。先在保持测试可运行的前提下加入新名称并迁移调用方，再由最终退出 Issue 删除旧名称。运行配置改为有明确默认值的应用配置与账户设置，不要求用户创建 `.env`，秘密凭据也不得落入普通配置文件。

## Acceptance criteria

- [ ] 项目元数据、CLI 帮助、网页标题、运行日志与用户可见中文文案统一使用 `BridGes`，大小写与产品定义一致。
- [ ] 提供可调用的 `BridGes` CLI 入口；迁移期间如保留旧入口，必须标记为兼容层且不会形成两套实现。
- [ ] 前后端依赖、测试和构建能够在迁移中的每个提交保持可运行，不进行一次性破坏式全仓重命名。
- [ ] 普通运行配置具有安全默认值或由应用数据目录管理，源码部署与容器部署均不要求用户创建 `.env`。
- [ ] 百炼 Key、QQ SMTP 授权码、密码和加密主密钥不进入普通配置、CLI 参数回显、日志或 API 响应。
- [ ] 缺少尚未配置的外部能力时，CLI 给出中文可操作提示，而不是导入失败或假成功。
- [ ] 文档明确区分“产品品牌迁移”和 Issue 04 的视觉资产设计，本 Issue 不产出临时 Logo。

## Verification

```powershell
conda run -n agent python -m pytest
conda run -n agent python -m ruff check .
conda run -n agent python -m mypy src
conda run -n agent BridGes --help
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

## Non-goals

- 不在本 Issue 设计 Logo、图标或最终页面视觉；这些由 Issue 04 完成。
- 不删除仍被迁移中调用方使用的旧符号。
- 不引入 Windows 安装包或 `.env` 兼容要求。

## Blocked by

- [02 — 建立独立测试状态和稳定基线](./02-stabilize-isolated-test-baseline.md)

## Comments

该 Issue 是宽范围命名迁移的“展开”阶段。旧名称的最终收缩应在所有调用方迁移并通过正式验收后完成。
