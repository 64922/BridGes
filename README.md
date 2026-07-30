# Science Companion — 科教智能体

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
执行，否则 `.env` 和相对路径 SQLite 数据库可能无法被正确读取。

### 2. 创建根目录 `.env`

复制下面的配置到项目根目录的 `.env` 文件中；其中尖括号内容需要替换：

```dotenv
SCIENCE_COMPANION_ENVIRONMENT=development
SCIENCE_COMPANION_DATABASE_URL=sqlite:///./science_companion.db
SCIENCE_COMPANION_SECRET_KEY=<随机生成的本地持久化密钥>
SCIENCE_COMPANION_QWEN_API_KEY=<你的通义千问测试 API Key>
SCIENCE_COMPANION_QWEN_FORCE_STUB=false
```

生成持久化密钥（不要使用 Qwen API Key 代替）：

```bash
# 上一步已激活 agent；也可以在任意 Python 3.11 环境中执行
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

把命令输出复制到 `SCIENCE_COMPANION_SECRET_KEY=` 后面。这个密钥用于保护本地
SQLite 状态，数据库产生后不要更换，否则旧数据将无法解密。`.env` 已被 Git 忽略，
不要把真实 API Key 或持久化密钥提交到仓库。

`SCIENCE_COMPANION_QWEN_API_KEY` 是联网模型能力所需的密钥；配置后并将
`SCIENCE_COMPANION_QWEN_FORCE_STUB` 设为 `false`，文本、结构化输出、OCR、视觉、
ASR 和 TTS 等能力才会使用 Qwen。启动本身不会主动调用 Qwen，实际使用相关功能时
才会发起网络请求。若评委机器无法联网，可删除 Qwen Key 或将该开关设为 `true`，
系统仍可启动并运行离线桩能力。

### 3. 启动并检查

```bash
science-companion serve
```

启动后访问：

- Web：<http://127.0.0.1:3000>
- API 就绪检查：<http://127.0.0.1:8000/health/ready>
- API 完整健康信息：<http://127.0.0.1:8000/health>

看到 `ready: "pass"` 才表示 API 的必需配置检查通过，业务请求不会因持久化保护而被拒绝。
首次正常运行后，项目根目录会生成 `science_companion.db`、`science_companion.db-wal`
和 `science_companion.db-shm` 文件；这些文件是本地运行数据，不需要手工创建。

### Docker / Podman 部署

Docker/Podman 使用生产模式，因此必须先按上面的步骤创建 `.env`，至少配置
`SCIENCE_COMPANION_DATABASE_URL` 和 `SCIENCE_COMPANION_SECRET_KEY`。在项目根目录执行：

```bash
docker compose -f infra/compose/docker-compose.yml up --build
# 或
podman-compose -f infra/compose/docker-compose.yml up --build
```

API 数据库会保存在 Compose 命名卷 `api-data` 中，容器重建不会丢失。Web 容器会等待
API 的 `/health/ready` 通过后再启动。如果 API 一直不健康，优先检查 `.env` 是否位于
项目根目录、密钥是否非空，以及是否误用了 `SCIENCE_COMPANION_SECRET_KEY_FILE`
的宿主机路径（容器内应改用直接的 `SCIENCE_COMPANION_SECRET_KEY`，或自行挂载密钥文件）。
Compose 会将容器内的 `SCIENCE_COMPANION_DATABASE_URL` 固定为挂载卷中的
`sqlite:////var/lib/science-companion/science_companion.db`；根目录 `.env` 中的相对路径
主要用于本地进程启动，不需要为 Compose 改成容器路径。

## 其他启动方式

手动分进程：

```bash
science-companion api        # http://127.0.0.1:8000
cd apps/web && npm run dev   # http://127.0.0.1:3000
```

## 健康检查

```bash
curl http://127.0.0.1:8000/health
```

Web UI 首页读取同一健康投影并展示存活、就绪与降级语义。

## 配置与密钥引用

所有生产运行方式共享同一配置 Schema，环境变量前缀为 `SCIENCE_COMPANION_`：

```bash
export SCIENCE_COMPANION_ENVIRONMENT=production
export SCIENCE_COMPANION_API_HOST=127.0.0.1
export SCIENCE_COMPANION_API_PORT=8000
export SCIENCE_COMPANION_SECRET_KEY_FILE=/run/secrets/secret_key
# 本地单进程开发持久化；生产环境必须配置等价的持久化数据库地址。
export SCIENCE_COMPANION_DATABASE_URL=sqlite:///./science_companion.db
```

密钥字段支持直接环境变量或 `<NAME>_FILE` 文件引用。详见 `infra/manual/README.md`。
未配置数据库地址时仅进入明确的开发内存模式；生产环境会在就绪检查中失败，避免数据静默丢失。
当前仓库内置的是带 WAL 和 Fernet 状态加密的 SQLite 单实例适配器，适合本地开发和 Compose 单实例；配置数据库时必须同时提供 `SCIENCE_COMPANION_SECRET_KEY` 或文件引用。未接入的 PostgreSQL 地址会明确报错，不会回退到内存。

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| 进程能启动，但 `/health/ready` 为 `fail` | 通常是配置了数据库却没有配置 `SCIENCE_COMPANION_SECRET_KEY`，或生产模式没有数据库地址；补齐 `.env` 后重启 API。 |
| API 返回 `503` 且提示持久化不可用 | 系统为避免数据静默写入内存而主动拒绝业务请求；检查数据库 URL、密钥和数据库目录权限。 |
| Qwen 功能报网络或鉴权错误 | 检查 API Key 是否有效、`SCIENCE_COMPANION_QWEN_FORCE_STUB=false` 是否生效，以及评委机器是否能访问 Qwen 服务；这不会影响 API 进程启动。 |
| Web 无法打开 | 先确认 API 的 `/health/ready` 为 `pass`，再检查 8000 和 3000 端口是否被其他程序占用。 |
| 改了 `.env` 但配置未生效 | 停止并重新启动 API；同时确认命令是在项目根目录执行。 |

## 测试

```bash
pytest -q
mypy src
cd apps/web && npm run typecheck
```

## 生产运行合同

- 生产支持手动分进程、统一 CLI、Docker、Podman 四种路径。
- 四种路径读取同一配置 Schema 和密钥引用规则。
- 生产镜像使用标准 Python / Node.js，不检测或要求 Conda。
- `environment.yml` 仅用于本地开发。
