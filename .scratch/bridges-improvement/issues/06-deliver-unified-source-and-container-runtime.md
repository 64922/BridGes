# 06 — 交付源码与容器统一运行合同

Status: ready-for-human
Blocked by: [03 — 展开 BridGes 品牌与包名迁移并移除 `.env` 配置](./03-rename-bridges-and-remove-env-config.md), [05 — 建立干净的 `bridges.db` 与账户隔离加密对象库](./05-build-clean-sqlite-and-object-storage.md)
Covered requirements: DEPLOY-01, DEPLOY-02, DEPLOY-03, DESKTOP-01
ADRs: [ADR-0012](../../../docs/adr/0012-source-environment-and-container-deployment.md), [ADR-0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [ADR-0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

交付同一套可验证运行合同：用户下载源码后可在 Conda 或 `.venv` 环境安装锁定的后端与前端依赖，并在项目目录执行 `BridGes start` 同步启动构建后的 Web、API、后台执行器和提醒调度器；高级用户可从同一源码使用 Docker Compose 或 Podman 部署。两条路径共享数据库迁移、数据目录、健康检查、停止语义和中文错误，不要求 `.env`，也不提供 Windows 原生安装包。

## Acceptance criteria

- [x] 从干净源码按文档创建 Conda 环境、升级 pip、安装后端依赖、安装并构建前端依赖后，`BridGes start` 可启动全部必要进程。
- [x] 从干净源码按文档创建 `.venv` 后执行同一安装与启动旅程，行为与 Conda 一致。
- [x] Compose 可由同一源码构建并启动，Docker 与 Podman 的持久化数据、对象、索引和加密凭据卷语义一致。
- [x] 源码与容器路径均不要求创建 `.env`；缺失用户尚未配置的百炼或 SMTP 凭据不阻止基础服务启动。
- [x] `BridGes start` 校验依赖和目录权限、获取单实例锁、执行迁移、启动各服务、等待健康检查并输出本地电脑端访问地址。
- [x] 任一关键服务失败时整体返回非零退出码并显示可操作中文错误；Ctrl+C 会按顺序停止子进程并释放锁。
- [x] 第二个实例不能对同一数据目录重复启动；异常终止后可以安全恢复，不丢失已提交数据。
- [x] 文档明确仅支持源码 Conda、源码 `.venv` 和 Docker/Podman，不包含 Windows 安装包步骤或暗示。
- [x] Web 产品只承诺电脑端使用，不在部署文档承诺手机访问或移动适配。

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

- 2026-08-02：Issue 06 交付完成（提交消息见 git log）。新增 `src/bridges/runtime/`
  （跨平台数据目录单实例锁、后台执行器、提醒调度器与受监督循环）、`BridGes
  worker`/`BridGes scheduler` 真实进程（替换 T001 桩）、`BridGes start` 统一监督
  编排（依赖与目录校验 → 单实例锁 → 迁移 → 启动 Web/API/后台执行器/提醒调度器 →
  健康检查 → 输出电脑端地址 → 失败非零退出中文报错 → Ctrl+C/SIGBREAK/SIGTERM
  按序停止并释放锁）；compose 新增 worker/scheduler 服务共享同一数据卷与密钥自举；
  文档（README、infra/manual）补齐 `.venv` 旅程、四进程说明、Podman 方言限制与
  仅源码/容器部署、电脑端承诺。验证：1035 测试通过（新增 13 个 runtime 契约测试，
  含空临时数据目录全流程冒烟：启动/迁移/健康/重复启动拒绝/优雅停止/再次启动），
  Conda 与 `.venv` 两环境各 11/11 通过，mypy 0 错误，npm typecheck/build 通过。
  代码审查（Standards+Spec 双轴）修复项见提交信息“bug 修改总结”。
  说明：提醒调度器的到期提醒分发接缝（`dispatch_due_reminders`）在提醒数据表
  （由 Issue 33 交付）存在前如实返回 0 并记录心跳，不做假成功；`start` 对
  worker/scheduler 的健康等待为进程存活校验（无 HTTP 端点），运行期崩溃由监督
  循环兜底整体非零退出。本机无 docker，compose 以 python yaml 解析校验（4 服务、
  卷与命令齐全），未执行真实容器构建。
