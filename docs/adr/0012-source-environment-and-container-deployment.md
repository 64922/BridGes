# 正式部署采用源码环境与容器两条路径

BridGes 不提供 Windows 原生安装包。正式非容器路径要求用户下载项目源码，创建并激活独立 Conda 或 `.venv` 环境，执行 pip 升级并安装后端依赖；Node.js 20+ 与 npm 作为 Web 构建前置条件安装。普通桌面用户进入项目目录执行 `BridGes start`，默认的 `desktop` profile 负责在用户数据目录创建持久化配置和状态密钥，按 `package-lock.json` 按需执行 `npm ci` 与 `npm run build`，再安全询问一次 Qwen Key 并启动服务。Conda 和 `.venv` 必须获得相同的受支持行为。

容器路径通过 Docker Compose 或 Podman 使用同一源码和锁定依赖构建运行；服务器、CI 与需要完全外部配置的源码运行可显式使用 `BridGes start --profile production`，开发者可使用 `--profile development`。两条路径共享迁移、健康检查、数据目录、后台服务与配置语义，均由统一 CLI 或容器入口编排，均不要求用户创建 `.env`。正式运行还必须配置全局百炼运行凭据（ADR-0024）：desktop profile 在交互终端将 Key 保存到操作系统凭据库，非交互启动优先复用已保存 Key、没有时再通过 `BRIDGES_QWEN_API_KEY` 或文件引用注入，显式 profile 与容器路径由环境/Compose 注入；缺少时启动硬门失败关闭。
