# 运行与配置手册

BridGes V2 支持默认桌面启动、显式配置的分进程运行以及 Docker／Podman Compose。产品使用与开发入门见[项目 README](../../README.md)。

## 桌面启动

在仓库根目录、Conda `agent` 环境中执行 `BridGes start`。默认 `desktop` profile 自动创建本机配置与数据目录、按需运行 `npm ci` 和 `npm run build`、执行迁移并启动 Web／API／后台执行器。首次交互式启动隐藏询问 Qwen 与 Tavily Key，保存到操作系统凭据库；后续启动复用。当前两个提示都要求输入非空，直接回车会取消启动。无交互终端必须预先提供 Qwen 凭据或已有凭据库记录，此时缺少 Tavily 不阻断启动。

默认状态目录由 `runtime/bootstrap.py` 定义，Windows 为 `%LOCALAPPDATA%\BridGes`，macOS 为 `~/Library/Application Support/BridGes`，Linux 为 `$XDG_DATA_HOME/BridGes`（回退到 `~/.local/share/BridGes`）。可用 `BRIDGES_HOME` 覆盖桌面状态目录。

同一数据目录只能启动一个受监管实例；就绪检查与子进程失败会影响整体启动结果。`Ctrl+C` 停止子进程并释放实例锁。容器通过 `SIGTERM` 平滑停止。

## 配置来源

统一配置定义在 [`src/bridges/config.py`](../../src/bridges/config.py)。应用读取 `BRIDGES_*` 环境变量，**不自动加载 `.env`**。代码仍兼容部分 `SCIENCE_COMPANION_*` 旧前缀，新配置应统一使用 `BRIDGES_*`；旧命令 `science-companion` 已移除。

| 变量 | 说明 | 默认值／要求 |
| --- | --- | --- |
| `BRIDGES_ENVIRONMENT` | 运行环境 | Schema 默认 `development`，桌面初始化默认 `production`。 |
| `BRIDGES_API_HOST` / `BRIDGES_API_PORT` | API 地址与端口 | `127.0.0.1` / `8000`。 |
| `BRIDGES_WEB_PORT` | Web 端口 | `3000`。 |
| `BRIDGES_WEB_DEV` | 单独启动 Web 时是否使用开发服务器 | `true`；`start` 按 profile 选择启动方式。 |
| `BRIDGES_DATABASE_URL` | SQLite 数据库地址 | 桌面自动生成；显式运行需提供。 |
| `BRIDGES_SECRET_KEY` | 本地状态加密密钥 | 桌面自动生成；显式运行需提供并持久保存。 |
| `BRIDGES_CREDENTIAL_BACKEND` | 凭据存储后端 | 本机 `os`；容器 `encrypted-volume`。 |
| `BRIDGES_QWEN_API_KEY` | 全局模型运行凭据 | 正式 API／后台执行器启动必需。 |
| `BRIDGES_TAVILY_API_KEY` | 通用搜索凭据 | 显式运行可选，缺失时相关搜索降级；交互式桌面首启要求输入。 |
| `BRIDGES_AMAP_WEB_SERVICE_KEY` | 高德地点与路线查询 | 通勤按需配置。 |
| `BRIDGES_AMAP_JS_API_KEY` / `BRIDGES_AMAP_SECURITY_JS_CODE` | 地图展示与代理校验 | 与 Web Service Key 分开配置。 |
| `BRIDGES_QWEN_REGION` | Qwen 服务区域 | `cn-beijing`。 |
| `BRIDGES_QWEN_WORKSPACE_ID` | Qwen 工作空间 | 可选。 |
| `BRIDGES_SESSION_COOKIE_SECURE` | 仅通过 HTTPS 发送 Cookie | `false`；使用 HTTPS 部署时按实际配置。 |
| `BRIDGES_ALLOWED_ORIGINS` | CSRF 允许来源，逗号分隔 | 默认推导；容器显式允许本机 Web 来源。 |
| `API_BASE_URL` | Next.js `/api` 转发目标 | 本机 `http://127.0.0.1:8000`；Compose `http://api:8000`。 |

`SECRET_KEY`、`DATABASE_URL`、Qwen／Tavily／高德等秘密类字段也支持 `<变量名>_FILE`，指向内容为单个配置值的 UTF-8 文件。文件引用优先于对应直接值；不要把凭据写进仓库或提交包含密钥的配置。

PowerShell 文件引用示例（文件须事先准备）：

```powershell
$env:BRIDGES_SECRET_KEY_FILE = "C:\bridges-secrets\state-key.txt"
$env:BRIDGES_DATABASE_URL_FILE = "C:\bridges-secrets\database-url.txt"
$env:BRIDGES_QWEN_API_KEY_FILE = "C:\bridges-secrets\qwen-key.txt"
```

数据库地址文件内容示例为 `sqlite:///C:/bridges-data/bridges.db`。API 与后台执行器必须指向同一数据库、使用同一状态密钥与凭据后端。

## 设置页与配置更新

设置页支持验证并替换 Qwen、Tavily、高德凭据，输入框不回显旧明文。Qwen 主模型手填精确 ID，元数据核对和真实能力探测成功后才保存，失败保留原值。主模型运行配置保存在数据库，影响之后创建的轮次；在途运行保留原模型锁，向量模型不随之改变。

Qwen 凭据在 API 进程内可就地轮换；其他进程在重启时读取新凭据。环境变量／密钥文件属于部署输入，使用这些输入时也要同步更新部署来源，避免重启后仍读到旧值。变更环境变量后重新创建相关容器，变更挂载文件后重启相关服务。详见 [ADR-0031](../../docs/adr/0031-runtime-qwen-credential-and-main-model-activation.md)。

## 开发与分进程运行

`BridGes start --profile development` 和 `--profile production` 不执行桌面初始化。先提供上述数据库、状态密钥与 Qwen 凭据；首次准备前端时执行 `npm ci`。

```powershell
# 同时启动 API、前端开发服务器和后台执行器
BridGes start --profile development
```

也可在三个已配置相同环境的终端分别运行：

```powershell
BridGes api
BridGes web --dev
BridGes worker
```

单独的 `api`／`worker`／`doctor`／`migrate` 不读取桌面 `config.json`。直接执行 `npm run dev` 时要在 `apps/web` 目录操作；使用 `start --profile production` 前须完成 `npm run build`。

`API_BASE_URL` 被 Next.js 的 `rewrites` 在构建时读取。变更生产 API 地址必须重新构建前端，不能只改 Web 启动时的环境变量。

## Docker / Podman

[`docker-compose.yml`](../compose/docker-compose.yml)编排四个进程：

- `api`：FastAPI，`/health/ready` 返回 `ready: pass` 后其他服务才启动。
- `web`：Next.js standalone，不挂载账户数据，不注入模型／搜索／地图服务端凭据。
- `worker`：后台执行器，共享 API 的数据库、配置凭据与加密凭据后端。
- `scheduler`：旧提醒兼容清理，不创建或发送新提醒。

API、后台执行器和兼容清理进程共享 `bridges-data` 卷。入口脚本在卷内生成并保存状态密钥，三个进程使用 `encrypted-volume` 凭据后端。镜像使用 Python 3.11 与 Node.js 24。

先在宿主机环境提供 `BRIDGES_QWEN_API_KEY`，按需设置 Tavily／高德变量，再在仓库根目录运行：

```powershell
docker compose -f infra/compose/docker-compose.yml up --build
# 或
podman-compose -f infra/compose/docker-compose.yml up --build
```

如使用 `BRIDGES_QWEN_API_KEY_FILE`，先在 Compose 的 **API 与 worker 两处**启用只读文件挂载，将 `QWEN_KEY_FILE_HOST` 设置为宿主机绝对路径，将 `BRIDGES_QWEN_API_KEY_FILE` 设置为容器内路径 `/run/secrets/qwen_key`。其他 `_FILE` 引用也需在这两个服务挂载相应文件；仅设置路径变量不会把宿主机文件带入容器。

Compose 已转发 Qwen、Tavily 与高德的直接值和 `_FILE` 引用。自定义区域、工作空间等其他配置时，需在相关服务的 `environment` 中显式添加。应用不需要 `.env`；Compose 自身的变量插值规则独立于应用配置。

Web Dockerfile 在构建时将 `API_BASE_URL` 设为 `http://api:8000`；自定义部署可通过构建参数覆盖，API 地址改变后重建 Web 镜像。容器重建保留命名数据卷，升级时不要执行删除数据卷的操作。

## 诊断与边界

```powershell
BridGes doctor
BridGes migrate
```

健康端点：[存活](http://127.0.0.1:8000/health/live)、[就绪](http://127.0.0.1:8000/health/ready)、[降级状态](http://127.0.0.1:8000/health/degraded)。启动边界检查必需凭据可读取，不做真实模型能力验证；供应商验证由设置页或专项验收执行。

只承诺个人电脑上的桌面浏览器体验，不提供原生安装包、自更新器或移动端适配。生产镜像使用标准运行时；`environment.yml` 只管理本地开发工具，Python 依赖仍以 `pyproject.toml` 为准。
