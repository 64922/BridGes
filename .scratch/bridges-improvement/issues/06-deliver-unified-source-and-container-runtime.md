# 06 — 交付源码与容器统一运行合同

Status: ready-for-agent
Blocked by: [03 — 展开 BridGes 品牌与包名迁移并移除 `.env` 配置](./03-rename-bridges-and-remove-env-config.md), [05 — 建立干净的 `bridges.db` 与账户隔离加密对象库](./05-build-clean-sqlite-and-object-storage.md)
Covered requirements: DEPLOY-01, DEPLOY-02, DEPLOY-03, DESKTOP-01
ADRs: [ADR-0012](../../../docs/adr/0012-source-environment-and-container-deployment.md), [ADR-0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [ADR-0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

交付同一套可验证运行合同：用户下载源码后可在 Conda 或 `.venv` 环境安装锁定的后端与前端依赖，并在项目目录执行 `BridGes start` 同步启动构建后的 Web、API、后台执行器和提醒调度器；高级用户可从同一源码使用 Docker Compose 或 Podman 部署。两条路径共享数据库迁移、数据目录、健康检查、停止语义和中文错误，不要求 `.env`，也不提供 Windows 原生安装包。

## Acceptance criteria

- [ ] 从干净源码按文档创建 Conda 环境、升级 pip、安装后端依赖、安装并构建前端依赖后，`BridGes start` 可启动全部必要进程。
- [ ] 从干净源码按文档创建 `.venv` 后执行同一安装与启动旅程，行为与 Conda 一致。
- [ ] Compose 可由同一源码构建并启动，Docker 与 Podman 的持久化数据、对象、索引和加密凭据卷语义一致。
- [ ] 源码与容器路径均不要求创建 `.env`；缺失用户尚未配置的百炼或 SMTP 凭据不阻止基础服务启动。
- [ ] `BridGes start` 校验依赖和目录权限、获取单实例锁、执行迁移、启动各服务、等待健康检查并输出本地电脑端访问地址。
- [ ] 任一关键服务失败时整体返回非零退出码并显示可操作中文错误；Ctrl+C 会按顺序停止子进程并释放锁。
- [ ] 第二个实例不能对同一数据目录重复启动；异常终止后可以安全恢复，不丢失已提交数据。
- [ ] 文档明确仅支持源码 Conda、源码 `.venv` 和 Docker/Podman，不包含 Windows 安装包步骤或暗示。
- [ ] Web 产品只承诺电脑端使用，不在部署文档承诺手机访问或移动适配。

## Verification

```powershell
conda run -n agent python -m pytest -k "cli or startup or health or runtime"
conda run -n agent BridGes start --help
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
docker compose config
```

另在空临时数据目录分别执行 Conda、`.venv` 与容器冒烟旅程，验证启动、健康检查、重复启动拒绝和 Ctrl+C/停止后的再次启动。

## Non-goals

- 不提供 Windows 原生安装包、自更新器或系统常驻服务。
- 不要求所有用户使用名为 `agent` 的 Conda 环境；`agent` 仅是本项目开发验证环境。
- 不引入多机编排或移动端部署。

## Blocked by

- [03 — 展开 BridGes 品牌与包名迁移并移除 `.env` 配置](./03-rename-bridges-and-remove-env-config.md)
- [05 — 建立干净的 `bridges.db` 与账户隔离加密对象库](./05-build-clean-sqlite-and-object-storage.md)

## Comments

若 Podman 对 Compose 方言存在差异，必须在文档中明确受支持命令与限制，但不得维护第二套运行配置。
