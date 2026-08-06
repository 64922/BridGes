# BridGes — 科教智能体

长期科学学习与表达伙伴的首个可运行产品骨架（T001）。

## 本地开发

使用 Conda `agent` 环境（由 `environment.yml` 声明）：

评委从零部署请直接按照下方“参赛/评委部署”章节执行。

## 参赛/评委部署（推荐）

以下步骤适用于 Windows、Linux 和 macOS。支持**源码 Conda**、**源码 `.venv`**
两种环境路径，两者安装与启动行为完全一致；高级用户还可使用 Docker/Podman
容器路径（见下文）。项目**不提供 Windows 原生安装包**，也没有自更新器或
系统常驻服务。

### 1. 安装运行环境

**源码 Conda 路径**（Windows PowerShell 用户请先打开 `agent` 环境；
Linux/macOS 用户将 `conda activate agent` 替换为自己的 Python 3.11 环境
激活命令即可）：

```bash
conda env create -f environment.yml   # 已存在 agent 环境时跳过
conda activate agent
python -m pip install --upgrade pip
pip install -e ".[dev]"
cd apps/web
npm install
npm run build
cd ../..
```

**源码 `.venv` 路径**（行为与 Conda 一致，不要求安装 Conda）：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
cd apps/web
npm install
npm run build
cd ../..
```

项目需要 Python 3.11 及 Node.js 20 或更高版本。`npm run build` 会生成 Web
生产构建产物（`.next/standalone`），`BridGes start` 默认启动构建后的 Web；
本地开发可改用 `BridGes start --profile development` 启动 Next.js 开发服务器。
后续启动命令都应在项目根目录执行，否则相对路径 SQLite 数据库可能无法被
正确读取。

### 2. 启动

**不需要创建 `.env`**：未配置的项全部使用安全默认值（本地开发默认
进程内存储；需要持久化或生产部署时通过环境变量覆盖，见“配置与密钥引用”）。

```bash
BridGes start
```

`BridGes start` 会按顺序完成：校验依赖与数据目录权限 → 获取数据目录单实例锁 →
执行数据库迁移 → 同步启动 **Web、API、后台执行器与提醒调度器**四个进程 →
等待健康检查 → 输出本地电脑端访问地址。同一数据目录不能启动第二个实例
（会提示先停止已有实例）；进程被强制终止后锁自动释放，可安全重新启动，
已提交数据不会丢失。按 Ctrl+C 会按顺序停止全部子进程并释放锁。

启动后访问：

- Web：<http://127.0.0.1:3000>
- API 就绪检查：<http://127.0.0.1:8000/health/ready>
- API 完整健康信息：<http://127.0.0.1:8000/health>

看到 `ready: "pass"` 才表示 API 的必需配置检查通过，业务请求不会因持久化保护而被拒绝。
BridGes 是**电脑端产品**：只面向桌面浏览器（Windows、Linux、macOS）使用，
不承诺手机、平板或移动浏览器访问，也没有移动适配。

配置持久化数据库后，数据目录会生成 `bridges.db`、`bridges.db-wal`
和 `bridges.db-shm` 文件；这些文件是本地运行数据，不需要手工创建。
持久化数据库必须同时配置 `BRIDGES_SECRET_KEY`（或文件引用），用于保护
本地状态加密。后台执行器与提醒调度器也可以在需要时单独启动：
`BridGes worker`（周期性清理待删除对象与孤立文件）与 `BridGes scheduler`
（提醒调度：按任务安排把提醒投递到账户 QQ 邮箱，需在账户设置中配置并
验证 QQ 邮箱 SMTP 授权码）。

联网模型能力（Qwen 文本、结构化输出、OCR、视觉、ASR、TTS、图片、Wan
视频等）由全局环境密钥 `BRIDGES_QWEN_API_KEY` 驱动；登录后受保护设置中
配置的账户级百炼密钥用于逐项真实能力探测与知识库 Embedding 检索。**
未配置密钥或供应商不可用时，对应能力明确停用并显示真实不可用状态**
（生产配置不注册离线桩或固定样例，也不会静默降级模型），`BridGes doctor`
会给出中文可操作提示。启动本身不会主动调用 Qwen，实际使用相关功能时才
会发起网络请求。模型绑定为固定矩阵（ADR-0009），用户不能切换模型。

### Docker / Podman 部署

Docker/Podman 使用生产模式，**不要求宿主机创建 `.env`**——普通配置由
Compose 环境变量注入，API 容器入口会在数据卷内首次启动时自动生成并持久化
主密钥（也可通过 `-e BRIDGES_SECRET_KEY=...` 显式覆盖）。在项目根目录执行：

```bash
docker compose -f infra/compose/docker-compose.yml up --build
# 或
podman-compose -f infra/compose/docker-compose.yml up --build
```

Compose 由同一源码构建 **API、Web、后台执行器与提醒调度器**四个服务，它们
共享同一 `bridges-data` 命名卷：数据库（`bridges.db`）、加密对象库
（`objects/`）与加密凭据（`secret.key`）都保存在该卷中，后续交付的本地索引
文件同样写入同一数据目录——数据、对象、索引与加密凭据在 Docker 与 Podman
下具有一致的卷语义，容器重建不会丢失。Web 容器会等待 API 的
`/health/ready` 通过后再启动；worker 与 scheduler 使用与 API 相同的入口脚本
完成密钥自举。如果 API 一直不健康，优先检查 `BridGes doctor` 输出与密钥配置，
以及是否误用了 `BRIDGES_SECRET_KEY_FILE` 的宿主机路径（容器内应改用直接的
`BRIDGES_SECRET_KEY`，或自行挂载密钥文件）。Compose 会将容器内的
`BRIDGES_DATABASE_URL` 固定为挂载卷中的
`sqlite:////var/lib/bridges/bridges.db`。

**Podman 支持范围**：Podman 使用同一份 Compose 配置，不维护第二套运行配置。
受支持的完整命令为 `podman-compose up --build`、`podman-compose down` 与
`podman-compose logs`。若本机 podman-compose 版本不支持 `depends_on` 的
`condition: service_healthy` 方言（老版本常见限制），可改用
`podman-compose up --no-deps web` 后按依赖顺序手动启动，或在 Linux 上直接
使用 `docker compose`；源码路径（`BridGes start`）不受该方言影响。

## 其他启动方式

统一 CLI 分进程：

```bash
BridGes api          # API 进程，http://127.0.0.1:8000
BridGes web          # Web 进程（默认生产构建；--dev 用开发服务器）
BridGes worker       # 后台执行器（清理待删除对象与孤立文件）
BridGes scheduler    # 提醒调度器（按任务安排投递 QQ 邮箱提醒）
```

手动分进程：

```bash
BridGes api        # http://127.0.0.1:8000
cd apps/web && npm run dev   # http://127.0.0.1:3000
```

## 健康检查

```bash
curl http://127.0.0.1:8000/health
```

Web UI 首页读取同一健康投影并展示存活、就绪与降级语义。

## 配置与密钥引用

所有生产运行方式共享同一配置 Schema，环境变量前缀为 `BRIDGES_`（旧前缀
`SCIENCE_COMPANION_*` 与旧命令入口 `science-companion` 已随 Issue 41
退役）：

```bash
export BRIDGES_ENVIRONMENT=production
export BRIDGES_API_HOST=127.0.0.1
export BRIDGES_API_PORT=8000
export BRIDGES_SECRET_KEY_FILE=/run/secrets/secret_key
# 本地单进程开发持久化；生产环境必须配置等价的持久化数据库地址。
export BRIDGES_DATABASE_URL=sqlite:///./bridges.db
```

密钥字段支持直接环境变量或 `<NAME>_FILE` 文件引用。详见 `infra/manual/README.md`。
未配置数据库地址时仅进入明确的开发内存模式；生产环境会在就绪检查中失败，避免数据静默丢失。
当前仓库内置的是带 WAL 和 Fernet 状态加密的 SQLite 单实例适配器，适合本地开发和 Compose 单实例；配置数据库时必须同时提供 `BRIDGES_SECRET_KEY` 或文件引用。未接入的 PostgreSQL 地址会明确报错，不会回退到内存。

密钥（百炼 Key、QQ SMTP 授权码、密码、加密主密钥）不进入普通配置文件、
CLI 参数回显、日志或 API 响应；账户级凭据通过登录后的受保护账户设置配置。

## 品牌迁移说明

BridGes 已从旧 Science Companion 工作台完成纵向替换：普通用户正式导航
只保留 BridGes 聊天优先产品面，旧项目制教学工作台、空壳页面与旧品牌入口
已退役删除（Issue 41）。旧包名 `science_companion`、旧命令入口
`science-companion` 与旧前缀 `SCIENCE_COMPANION_*` 的迁移兼容层已随退出
Issue 移除，规范入口仅为 `bridges` / `BridGes` / `BRIDGES_*`。历史规划材料
（`.scratch/science-companion-plan/` 与根目录 `tickets.md`）保持只读原样，
仅作为历史记录。

## 数据备份与恢复

账户数据（对话、画像、学习项目、提醒、设置）保存在本地 `bridges.db`，
加密对象与凭据保存在同一数据目录。在账户设置 →「数据与隐私」中可以：

- **导出**：下载当前账户的完整数据导出包；
- **备份**：创建当前账户的备份，可在同一设备或迁移后恢复；
- **删除**：删除当前账户及其全部数据（需再次认证）。

备份/恢复只针对 BridGes 正式数据模型（`bridges.db`）；旧 Science Companion
原型数据库（`science_companion.db`）按封存清单保留为只读历史，不参与备份
与恢复。

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| 进程能启动，但 `/health/ready` 为 `fail` | 通常是配置了数据库却没有配置 `BRIDGES_SECRET_KEY`，或生产模式没有数据库地址；补齐环境变量后重启 API。 |
| API 返回 `503` 且提示持久化不可用 | 系统为避免数据静默写入内存而主动拒绝业务请求；检查数据库 URL、密钥和数据库目录权限。 |
| Qwen 功能显示不可用或报网络/鉴权错误 | 未配置账户级百炼密钥或密钥无效、供应商不可用；登录后在账户设置中配置密钥并通过能力探测，页面会显示真实可用/不可用状态，不伪装成功。 |
| Web 无法打开 | 先确认 API 的 `/health/ready` 为 `pass`，再检查 8000 和 3000 端口是否被其他程序占用。 |
| 改了环境变量但配置未生效 | 停止并重新启动 API；同时确认命令是在项目根目录执行。 |

## 测试

```bash
pytest -q
mypy src
cd apps/web && npm run typecheck
```

## 发布评测与发布门

BridGes 提供可复现 A/B 科学评测套件（Issue 40）与发布阈值门（供发行收口使用）：

```bash
BridGes evaluate run        # 运行评测套件（确定性模式，双跑可复现）
BridGes evaluate replay     # 一键重放既有运行，核对运行锁一致
BridGes evaluate blind-review  # 匿名盲评与一致性复核
BridGes evaluate report     # 生成点估计/CI/显著性报告
BridGes evaluate gates      # 按锁定发布阈值判定报告是否达到发行资格
```

安全攻击矩阵与修复证据见 `docs/security/issue39-security-report.md`；评测
发布阈值低于锁定线时 `evaluate gates` 会阻止发行（高风险安全失败、事实门
失败或账户串号均阻止发布）；生产 Stub 由注册表契约测试与静态扫描阻止
（`evaluate gates` 的指标判定不包含 Stub 扫描）。

## 生产运行合同

- 部署范围**仅限**：源码 Conda 环境、源码 `.venv` 环境、Docker/Podman
  Compose。不提供 Windows 原生安装包、自更新器或系统常驻服务。
- 统一 CLI（`BridGes start`）同步启动 Web、API、后台执行器与提醒调度器；
  手动分进程与容器路径提供同一组进程。
- 所有路径读取同一配置 Schema 和密钥引用规则，均不要求用户创建 `.env`。
- 生产镜像使用标准 Python / Node.js，不检测或要求 Conda。
- `environment.yml` 仅用于本地开发。
- Web 只承诺电脑端使用，不承诺手机、平板或移动浏览器访问。
