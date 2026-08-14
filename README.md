# BridGes — 科教智能体

BridGes 是面向长期科学学习、科学表达与成长陪伴的聊天优先应用。当前产品合同、领域词汇和迁移顺序以 [`ADR-0026`](docs/adr/0026-frozen-product-contracts-and-migration-gates.md) 与 [最终 PRD](.scratch/final/PRD.md) 为准。

## 当前产品合同

- 只有日常陪伴和学习模式；首条用户消息提交前选择，提交后由服务端永久锁定，不能在会话内切换。
- 用户用自然语言触发能力路由；用户不安装或管理 SKILL/MCP，内置 SKILL 和内部论文搜索适配器保留。
- 全局知识库是唯一上传和本地检索来源；聊天附件、学习项目文件和局部知识库退役。
- 画像默认自动提取为学业情况、感兴趣的知识、兴趣爱好和阶段目标；首次只显示一次非交互隐私说明，禁止敏感推断，支持修改和撤回。
- 学习证据不足时自动联网；外网失败不得伪造资料、引用或推进教学计划。人味化不得改写确定性文本、引用、代码、公式和精确数据。

旧项目、提醒、SMTP 凭据、用户扩展包和九维画像按 expand–migrate–contract 迁移；账户、会话、历史消息、有效证据、导出和迁移审计保留。兼容窗口的真实调用清零门见 [COMPATIBILITY-GATE.md](.scratch/final/COMPATIBILITY-GATE.md)。

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
```

**源码 `.venv` 路径**（行为与 Conda 一致，不要求安装 Conda）：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

项目需要 Python 3.11 及 Node.js 20 或更高版本（包含 npm）。源码安装完成后，
后续启动命令都应在项目根目录执行。

### 2. 启动

**不需要创建 `.env`**。普通桌面用户直接执行下面的命令即可；首次启动会
自动创建本机持久化配置、数据目录和状态加密密钥，并按需执行 Web 依赖安装
与生产构建。配置默认保存在 Windows `%LOCALAPPDATA%\BridGes`、macOS
`~/Library/Application Support/BridGes` 或 Linux `$XDG_DATA_HOME/BridGes`
（可用 `BRIDGES_HOME` 指定其他目录）。

```bash
BridGes start
```

首次交互式启动的顺序是：使用已安装的 Python/Node/npm → 创建或读取本机配置 → 在
`apps/web` 中按 `package-lock.json` 执行 `npm ci`（需要时）→ 执行
`npm run build` 生成 `.next/standalone/server.js`（需要时）→ 隐藏询问一次
Qwen API Key → 将 Key 保存到操作系统凭据库 → 获取数据目录单实例锁、执行
数据库迁移并启动 **Web、API 与后台执行器**。Key 不写入仓库、`config.json`
或 `.env`；后续启动会复用凭据库中的 Key，不再询问。

本机托管模式会把生成的 `BRIDGES_DATABASE_URL`、状态密钥文件路径等运行时
配置注入 API/worker/scheduler 子进程；Web 构建和 Web 服务不会继承 Qwen Key。
如果终端不是交互式 TTY，系统不会等待输入；只有在操作系统凭据库中也没有
已保存 Key 时，才要求预先提供 `BRIDGES_QWEN_API_KEY` 或
`BRIDGES_QWEN_API_KEY_FILE`。

`BridGes start` 随后会：获取数据目录单实例锁 → 执行数据库迁移 → 启动 **Web、API 与后台执行器** →
等待健康检查 → 输出本地电脑端访问地址。同一数据目录不能启动第二个实例
（会提示先停止已有实例）；进程被强制终止后锁自动释放，可安全重新启动，
已提交数据不会丢失。按 Ctrl+C 会按顺序停止全部子进程并释放锁。

高级运行方式仍可显式选择 profile：

```bash
# CI、服务器或已由环境变量/文件管理配置的生产运行
BridGes start --profile production
# 只启动 Next.js 开发服务器；Qwen Key 仍需通过环境变量或文件引用提供
BridGes start --profile development
```

显式 `production`/`development` 不执行本机托管初始化，也不会询问 Key；请在
启动前设置 `BRIDGES_QWEN_API_KEY` 或 `BRIDGES_QWEN_API_KEY_FILE`，并在使用
持久化数据库时设置 `BRIDGES_DATABASE_URL` 与 `BRIDGES_SECRET_KEY`（或对应
文件引用）。容器继续使用显式生产配置。

启动后访问：

- Web：<http://127.0.0.1:3000>
- API 就绪检查：<http://127.0.0.1:8000/health/ready>
- API 完整健康信息：<http://127.0.0.1:8000/health>

看到 `ready: "pass"` 才表示 API 的必需配置检查通过，业务请求不会因持久化保护而被拒绝。
`/health/ready` 在未就绪时返回 HTTP 503（响应体仍是同一份健康投影），因此
等待就绪的探针（如 Playwright webServer、容器健康检查）只认状态码即可：
数据库 schema 未达当前版本或核心表缺失时不会开始接受业务流量。
BridGes 是**电脑端产品**：只面向桌面浏览器（Windows、Linux、macOS）使用，
不承诺手机、平板或移动浏览器访问，也没有移动适配。

配置持久化数据库后，数据目录会生成 `bridges.db`、`bridges.db-wal`
和 `bridges.db-shm` 文件；这些文件是本地运行数据，不需要手工创建。
本机托管模式首次启动会自动生成并保存 `BRIDGES_SECRET_KEY` 对应的状态密钥文件；
如果使用外部数据库地址，则必须预先同时提供 `BRIDGES_SECRET_KEY`（或文件引用），
用于保护本地状态加密。后台执行器也可以单独启动：`BridGes worker`（执行可恢复的生成、
索引、迁移和清理任务）。旧提醒调度器仅作为兼容期的停用/清理组件，不再创建或
发送用户提醒，兼容窗口结束后按迁移门删除。

联网模型能力（Qwen 文本、结构化输出、OCR、视觉、ASR、TTS、图片、Wan
视频等）由全局百炼运行凭据在启动前统一驱动；普通账户无需也不存在个人百炼
密钥配置。默认 desktop 的 `start` 从操作系统凭据库复用该 Key，显式 profile、
独立 `api`/`worker` 和容器仍从 `BRIDGES_QWEN_API_KEY` 或 `*_FILE` 读取；独立
命令不会自动读取 desktop 的 `config.json`。缺少、为空或无法读取全局 Key 时，
相应命令都在启动边界失败关闭并给出不含秘密的中文配置指引（`BridGes doctor`
同样报告失败）——不注册离线桩或固定样例，也不会静默降级模型。
启动本身不会主动调用 Qwen，实际使用相关功能时才会发起网络请求；若全局
Key 无效、无权限或供应商限流，调用呈现稳定的中文服务配置错误，不会引导
用户访问任何密钥设置页面。模型绑定为固定矩阵（ADR-0009），用户不能切换
模型。所有账户共享同一把全局 Key：共享供应商配额、限流与费用，应用审计
仍按发起账户记录，但不代表供应商侧独立计费（见发布说明）。

### Docker / Podman 部署

Docker/Podman 使用生产模式，**不要求宿主机创建 `.env`**——普通配置由
Compose 环境变量注入，API 容器入口会在数据卷内首次启动时自动生成并持久化
主密钥（也可通过 `-e BRIDGES_SECRET_KEY=...` 显式覆盖）。在项目根目录执行：

```bash
BRIDGES_QWEN_API_KEY=sk-... docker compose -f infra/compose/docker-compose.yml up --build
# 或
podman-compose -f infra/compose/docker-compose.yml up --build
```

**全局百炼 Key 注入**：Compose 将宿主机环境变量 `BRIDGES_QWEN_API_KEY`
透传给 API 与后台执行器（缺失、为空或不可读时两者都在容器启动阶段失败
关闭，Web 因 `depends_on` 健康门不启动，不会出现 Web 正常但 AI 不可用的
半启动状态）；也可以改用 `--env-file` 传入秘密文件，或使用文件挂载方式
（容器/长期部署优先）：把密钥写入宿主机只读文件，取消 `docker-compose.yml`
中注释的 bind mount 示例行，宿主机路径通过 `QWEN_KEY_FILE_HOST` 环境变量
传入、`BRIDGES_QWEN_API_KEY_FILE` 保持指向容器内路径（如
`/run/secrets/qwen_key`），再执行 Compose 启动。

Compose 由同一源码构建 **API、Web 与后台执行器**三个服务，它们
共享同一 `bridges-data` 命名卷：数据库（`bridges.db`）、加密对象库
（`objects/`）与加密凭据（`secret.key`）都保存在该卷中，后续交付的本地索引
文件同样写入同一数据目录——数据、对象、索引与加密凭据在 Docker 与 Podman
下具有一致的卷语义，容器重建不会丢失。Web 容器会等待 API 的
`/health/ready` 通过后再启动；worker 与 API 使用同一全局百炼凭据与入口脚本。
如果 API 一直不健康，优先检查 `BridGes doctor` 输出与密钥配置，
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
  # 旧 scheduler 命令仅在兼容期用于退役清理，不创建或发送提醒
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

未登录访问 Web 根路径会直接进入新版登录页；服务健康状态通过上述 API
健康检查端点查看。

## 配置与密钥引用

显式 `production`/`development`、手动分进程和容器共享同一配置 Schema，环境
变量前缀为 `BRIDGES_`（旧前缀 `SCIENCE_COMPANION_*` 与旧命令入口
`science-companion` 已随 Issue 41 退役）。默认 `desktop` profile 使用同一
字段语义，但将生成的本机配置注入子进程：

```bash
export BRIDGES_ENVIRONMENT=production
export BRIDGES_API_HOST=127.0.0.1
export BRIDGES_API_PORT=8000
export BRIDGES_SECRET_KEY_FILE=/run/secrets/secret_key
# 显式 production/容器的全局百炼运行凭据；desktop 首次启动可交互式输入。
export BRIDGES_QWEN_API_KEY_FILE=/run/secrets/qwen_key
# 本地单进程开发持久化；生产环境必须配置等价的持久化数据库地址。
export BRIDGES_DATABASE_URL=sqlite:///./bridges.db
```

密钥字段支持直接环境变量或 `<NAME>_FILE` 文件引用。详见 `infra/manual/README.md`。
未配置数据库地址时仅进入明确的开发内存模式；生产环境会在就绪检查中失败，避免数据静默丢失。
当前仓库内置的是带 WAL 和 Fernet 状态加密的 SQLite 单实例适配器，适合本地开发和 Compose 单实例；配置数据库时必须同时提供 `BRIDGES_SECRET_KEY` 或文件引用。未接入的 PostgreSQL 地址会明确报错，不会回退到内存。

密钥（全局百炼 Key、密码、加密主密钥以及迁移前遗留的 QQ SMTP 授权码）不进入
普通配置文件、CLI 参数回显、日志或 API 响应。desktop 的全局百炼 Key 保存在
操作系统凭据库，并仅注入 API/worker 服务进程；显式 profile 与容器通过环境变量
或文件引用注入。遗留 SMTP 授权码按 ADR-0026 幂等清除，普通账户不存在百炼或
提醒密钥管理入口。

## 品牌迁移说明

BridGes 已从旧 Science Companion 工作台完成纵向替换：普通用户正式导航
只保留 BridGes 聊天优先产品面，旧项目制教学工作台、空壳页面与旧品牌入口
已退役删除（Issue 41）。旧包名 `science_companion`、旧命令入口
`science-companion` 与旧前缀 `SCIENCE_COMPANION_*` 的迁移兼容层已随退出
Issue 移除，规范入口仅为 `bridges` / `BridGes` / `BRIDGES_*`。历史规划材料
（`.scratch/science-companion-plan/` 与根目录 `tickets.md`）保持只读原样，
仅作为历史记录。

## 数据备份与恢复

账户数据（对话、四维画像、全局知识库、教学计划、设置和迁移审计）保存在本地 `bridges.db`；
历史学习项目、提醒投递和扩展清单只读保留供账户导出，
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
| 进程能启动，但 `/health/ready` 为 `fail`（未就绪时返回 503） | 通常是配置了数据库却没有配置 `BRIDGES_SECRET_KEY`、生产模式没有数据库地址，或数据库 schema 完整性校验未通过（迁移审计表等核心对象缺失）；补齐环境变量或修复数据库后重启 API。 |
| API 返回 `503` 且提示持久化不可用 | 系统为避免数据静默写入内存而主动拒绝业务请求；检查数据库 URL、密钥和数据库目录权限。 |
| 启动即报"未配置全局百炼运行凭据" | 非交互启动或显式 profile 没有环境变量/文件引用，且凭据库中也没有已保存 Key；交互式 desktop 首次启动会隐藏询问 Key，配置后重新执行，Key 轮换后同样重启。 |
| 运行时报网络/鉴权/限流错误 | 全局百炼 Key 无效、无供应商权限或供应商限流；检查启动服务时的 `BRIDGES_QWEN_API_KEY` 配置与百炼账户权限/额度，重启服务后重试。 |
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
- 统一 CLI（`BridGes start`）同步启动 Web、API 与后台执行器；
  手动分进程与容器路径提供同一组进程。
- 所有路径读取同一配置 Schema 和密钥引用规则，均不要求用户创建 `.env`。
- 生产镜像使用标准 Python / Node.js，不检测或要求 Conda。
- `environment.yml` 仅用于本地开发。
- Web 只承诺电脑端使用，不承诺手机、平板或移动浏览器访问。
