# BridGes — 科教智能体

长期科学学习与表达伙伴的首个可运行产品骨架（T001）。

## 本地开发

使用 Conda `agent` 环境（由 `environment.yml` 声明）：

评委从零部署请直接按照下方“参赛/评委部署”章节执行。

## 参赛/评委部署（推荐）

以下步骤适用于 Windows、Linux 和 macOS。Windows PowerShell 用户请先打开
`agent` 环境；Linux/macOS 用户将 `conda activate agent` 替换为自己的 Python 3.11
环境激活命令即可。

### 1. 安装运行环境

在项目根目录执行：

```bash
conda env create -f environment.yml   # 已存在 agent 环境时跳过
conda activate agent
pip install -e ".[dev]"
cd apps/web
npm install
cd ..
```

项目需要 Python 3.11 及 Node.js 20 或更高版本。后续启动命令都应在项目根目录
执行，否则相对路径 SQLite 数据库可能无法被正确读取。

### 2. 启动

**不需要创建 `.env`**：未配置的项全部使用安全默认值（本地开发默认
进程内存储；需要持久化或生产部署时通过环境变量覆盖，见“配置与密钥引用”）。

```bash
BridGes start
```

启动后访问：

- Web：<http://127.0.0.1:3000>
- API 就绪检查：<http://127.0.0.1:8000/health/ready>
- API 完整健康信息：<http://127.0.0.1:8000/health>

看到 `ready: "pass"` 才表示 API 的必需配置检查通过，业务请求不会因持久化保护而被拒绝。

配置持久化数据库后，项目根目录会生成 `bridges.db`、`bridges.db-wal`
和 `bridges.db-shm` 文件；这些文件是本地运行数据，不需要手工创建。
持久化数据库必须同时配置 `BRIDGES_SECRET_KEY`（或文件引用），用于保护
本地状态加密。

联网模型能力（Qwen 文本、结构化输出、OCR、视觉、ASR、TTS 等）需要
`BRIDGES_QWEN_API_KEY`；未配置时使用离线桩能力，`BridGes doctor` 会给出
中文可操作提示。启动本身不会主动调用 Qwen，实际使用相关功能时才会发起
网络请求。

### Docker / Podman 部署

Docker/Podman 使用生产模式，**不要求宿主机创建 `.env`**——普通配置由
Compose 环境变量注入，API 容器入口会在数据卷内首次启动时自动生成并持久化
主密钥（也可通过 `-e BRIDGES_SECRET_KEY=...` 显式覆盖）。在项目根目录执行：

```bash
docker compose -f infra/compose/docker-compose.yml up --build
# 或
podman-compose -f infra/compose/docker-compose.yml up --build
```

API 数据库会保存在 Compose 命名卷 `bridges-data` 中，容器重建不会丢失。
Web 容器会等待 API 的 `/health/ready` 通过后再启动。如果 API 一直不健康，
优先检查 `BridGes doctor` 输出与密钥配置，以及是否误用了 `BRIDGES_SECRET_KEY_FILE`
的宿主机路径（容器内应改用直接的 `BRIDGES_SECRET_KEY`，或自行挂载密钥文件）。
Compose 会将容器内的 `BRIDGES_DATABASE_URL` 固定为挂载卷中的
`sqlite:////var/lib/bridges/bridges.db`。

## 其他启动方式

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

所有生产运行方式共享同一配置 Schema，环境变量前缀为 `BRIDGES_`（迁移期
同时接受旧前缀 `SCIENCE_COMPANION_*`，新前缀优先）：

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

本仓库正在从旧的 Science Companion 工作台纵向替换为 BridGes。产品品牌
迁移（包名、命令入口、网页标题、运行日志与用户可见中文文案统一为
`BridGes`）由本阶段 Issue 完成；**Logo 与视觉资产设计属于后续视觉 Issue**，
本阶段不产出临时 Logo 或最终页面视觉。旧包名 `science_companion` 与旧命令
入口 `science-companion` 在迁移期作为兼容层保留（与 `bridges` / `BridGes`
指向同一实现），由退出 Issue 删除。

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| 进程能启动，但 `/health/ready` 为 `fail` | 通常是配置了数据库却没有配置 `BRIDGES_SECRET_KEY`，或生产模式没有数据库地址；补齐环境变量后重启 API。 |
| API 返回 `503` 且提示持久化不可用 | 系统为避免数据静默写入内存而主动拒绝业务请求；检查数据库 URL、密钥和数据库目录权限。 |
| Qwen 功能报网络或鉴权错误 | 检查 API Key 是否有效、`BRIDGES_QWEN_FORCE_STUB=false` 是否生效，以及评委机器是否能访问 Qwen 服务；这不会影响 API 进程启动。 |
| Web 无法打开 | 先确认 API 的 `/health/ready` 为 `pass`，再检查 8000 和 3000 端口是否被其他程序占用。 |
| 改了环境变量但配置未生效 | 停止并重新启动 API；同时确认命令是在项目根目录执行。 |

## 测试

```bash
pytest -q
mypy src
cd apps/web && npm run typecheck
```

## 生产运行合同

- 生产支持手动分进程、统一 CLI、Docker、Podman 四种路径。
- 四种路径读取同一配置 Schema 和密钥引用规则。
- 任何路径都不要求用户创建 `.env`。
- 生产镜像使用标准 Python / Node.js，不检测或要求 Conda。
- `environment.yml` 仅用于本地开发。
