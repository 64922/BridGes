# 正式部署采用源码环境与容器两条路径

BridGes 不提供 Windows 原生安装包。正式非容器路径要求用户下载项目源码，创建并激活独立 Conda 或 `.venv` 环境，执行 pip 升级并依据项目锁定文件安装后端与前端依赖，随后进入项目目录执行 `BridGes start`；Conda 和 `.venv` 必须获得相同的受支持行为。容器路径通过 Docker Compose 或 Podman 使用同一源码和锁定依赖构建运行。两条路径共享迁移、健康检查、数据目录、后台服务与配置语义，均由统一 CLI 或容器入口编排，均不要求用户创建 `.env`。正式运行还必须配置全局百炼运行凭据（ADR-0024）：源码路径在 `BridGes start` 前设置 `BRIDGES_QWEN_API_KEY` 或文件引用，容器路径由 Compose 注入；缺少时启动硬门失败关闭。
