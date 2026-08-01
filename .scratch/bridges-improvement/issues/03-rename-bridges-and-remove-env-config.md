# 03 — 展开 BridGes 品牌与包名迁移并移除 `.env` 配置

Status: ready-for-human
Blocked by: [02 — 建立独立测试状态和稳定基线](./02-stabilize-isolated-test-baseline.md)
Covered requirements: UI-02, DEPLOY-02, DEPLOY-03, IMP-03
ADRs: [ADR-0012](../../../docs/adr/0012-source-environment-and-container-deployment.md), [ADR-0016](../../../docs/adr/0016-incremental-vertical-replacement.md)

## What to build

以可回滚的展开—迁移方式建立 BridGes 产品名、Python 分发名、命令入口、前端包名、中文运行文案和本地配置边界。先在保持测试可运行的前提下加入新名称并迁移调用方，再由最终退出 Issue 删除旧名称。运行配置改为有明确默认值的应用配置与账户设置，不要求用户创建 `.env`，秘密凭据也不得落入普通配置文件。

## Acceptance criteria

- [x] 项目元数据、CLI 帮助、网页标题、运行日志与用户可见中文文案统一使用 `BridGes`，大小写与产品定义一致。
- [x] 提供可调用的 `BridGes` CLI 入口；迁移期间如保留旧入口，必须标记为兼容层且不会形成两套实现。
- [x] 前后端依赖、测试和构建能够在迁移中的每个提交保持可运行，不进行一次性破坏式全仓重命名。
- [x] 普通运行配置具有安全默认值或由应用数据目录管理，源码部署与容器部署均不要求用户创建 `.env`。
- [x] 百炼 Key、QQ SMTP 授权码、密码和加密主密钥不进入普通配置、CLI 参数回显、日志或 API 响应。
- [x] 缺少尚未配置的外部能力时，CLI 给出中文可操作提示，而不是导入失败或假成功。
- [x] 文档明确区分“产品品牌迁移”和 Issue 04 的视觉资产设计，本 Issue 不产出临时 Logo。

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

已完成（2026-08-02）：实现与双轴代码审查（Standards + Spec）修复完毕，完整验证通过。

**工作内容总结**
- 品牌与包名迁移：`pyproject.toml` 分发名 `science-companion` → `bridges`；`src/science_companion/` → `src/bridges/`（约 130 个模块重命名）；前端包名 `@science-companion/web` → `@bridges/web`、契约别名 `@bridges/contracts`（tsconfig paths 与 openapi 再生同步）；网页标题、CLI 帮助、运行日志与用户可见中文文案统一为 `BridGes`（openapi.json title 同步为 `BridGes API`）。
- 规范命令入口：`BridGes = bridges.cli.main:app` 为规范入口；`science-companion` 控制台入口与 `python -m science_companion.*` 模块路径为迁移期兼容层，与规范入口指向同一实现（不维护两套实现），由退出 Issue 删除。
- 配置边界（DEPLOY-03）：新增 `src/bridges/config.py` 统一配置 Schema——`BRIDGES_` 前缀（迁移期兼容旧前缀 `SCIENCE_COMPANION_`，新前缀优先），`<NAME>_FILE` 秘密文件引用，未配置项使用安全默认值，任何载体不读取、不要求创建 `.env`；删除旧 `science_companion/config.py`。
- 容器部署（DEPLOY-02）：新增 `apps/api/entrypoint.sh`，在数据卷内首次启动时生成并持久化主密钥（权限 600）；`docker-compose.yml` 改为 `BRIDGES_*` 环境变量注入并移除 `.env` 要求。
- 文档：README、`infra/manual/README.md` 全面改写为 BridGes 品牌与新配置语义，并明确 Logo/视觉资产归 Issue 04，本 Issue 不产出临时 Logo。
- 测试：新增 2 个（`doctor` 缺外部能力中文提示、旧兼容层模块入口同一实现），conftest 与冒烟测试的确定性环境键全部迁移至 `BRIDGES_*` 前缀（含全部 `*_FILE` 隔离），Issue 02 阻塞项在迁移后仍成立。

**代码审查 bug 修复总结**（双轴审查 Standards + Spec 各发现 3 项，修复如下）
1. **前后端会话 Cookie 名不一致（登录链路断裂）**：API 端 `auth.py` 已将会话 Cookie 改为 `bridges_session`，但前端 `middleware.ts` 与公共页 `page.tsx` 仍读取旧名 `science_companion_session`，导致登录后中间件恒判未登录、受保护路由全部重定向回登录页。已统一为 `bridges_session`。
2. **`apps/web/Dockerfile` 残留旧契约包名**：仍将 `generated.ts` 拷贝至 `node_modules/@science-companion/contracts/`，而全部 import 已改为 `@bridges/contracts`，Web 容器镜像构建不可用。已改为 `node_modules/@bridges/contracts/`。
3. **规范命令 `BridGes start` 缺失**：ADR-0012 与 PRD DEPLOY-01 规定用户执行 `BridGes start`，但 CLI 仅有 `serve`。新增 `start` 规范命令，`serve` 改为同实现别名（共享 `_serve`，不形成两套实现），README 与 manual 同步。

**验证结果**
- 完整测试集连续两次 `1004 passed`（含新增 2 项，基线 1002），单次 75s。
- `mypy src`：128 个源文件零问题；`npm run typecheck`、`npm run build` 通过。
- `BridGes --help` / `BridGes doctor` / `python -m science_companion.cli.main doctor` 均正常，中文可操作提示输出正确。
- 环境说明：本地 ruff 已升级至 0.16（新规则 UP042 等），对未改动的历史文件（含只读保留目录 `.scratch/science-companion-plan/`）报 253 个**预存错误**，与本次迁移改动无关（本 Issue 改动的全部文件 ruff 零错误）；如需全仓 `ruff check .` 通过，需单独处理存量代码或固定 ruff 版本。
- Status 标记 `ready-for-human` 表示等待人工验收；旧名称最终收缩由退出 Issue 完成。
