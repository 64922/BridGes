# 手动分进程运行示例

BridGes 支持以下四种生产运行合同骨架：

- 手动分进程（本目录示例）
- 统一 CLI：`BridGes start`
- Docker：`docker compose -f infra/compose/docker-compose.yml up`
- Podman：`podman-compose -f infra/compose/docker-compose.yml up`

四种方式读取同一配置 Schema 和密钥引用规则。

## 统一配置 Schema

所有运行方式都通过 `BRIDGES_*` 环境变量读取配置（迁移期同时接受旧前缀
`SCIENCE_COMPANION_*`，新前缀优先）。**任何载体都不读取、不要求创建
`.env` 文件**；未配置的项使用下方安全默认值。

常用变量：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `BRIDGES_ENVIRONMENT` | 运行环境标识 | `development` |
| `BRIDGES_API_HOST` | API 监听地址 | `127.0.0.1` |
| `BRIDGES_API_PORT` | API 端口 | `8000` |
| `BRIDGES_WEB_PORT` | Web 端口 | `3000` |
| `BRIDGES_WEB_DEV` | Web 是否使用 Next.js dev | `true` |
| `BRIDGES_SESSION_COOKIE_SECURE` | Cookie 是否强制 secure | `false` |
| `BRIDGES_ALLOWED_ORIGINS` | CSRF 来源校验允许来源（逗号分隔，如 `http://localhost:3000`） | 自动推导（Host/环回） |

密钥类变量（如 `BRIDGES_SECRET_KEY`、`BRIDGES_DATABASE_URL`、
`BRIDGES_QWEN_API_KEY`）既可以直接设置，也推荐通过文件引用：

```bash
export BRIDGES_SECRET_KEY_FILE=/run/secrets/secret_key
export BRIDGES_DATABASE_URL_FILE=/run/secrets/database_url
```

文件引用的后缀为 `<NAME>_FILE`，其内容会被读取并赋值给对应字段。生产环境
（Docker / Podman / systemd）应优先使用文件引用，避免把密钥写入普通环境变量。
密钥不进入普通配置文件、CLI 参数回显、日志或 API 响应。

## 手动分进程

终端 1（API）：

```bash
export BRIDGES_ENVIRONMENT=production
export BRIDGES_API_HOST=127.0.0.1
export BRIDGES_API_PORT=8000
BridGes api
```

终端 2（Web）：

```bash
export BRIDGES_WEB_DEV=true
export BRIDGES_WEB_PORT=3000
export API_BASE_URL=http://127.0.0.1:8000
cd apps/web
npm run dev
```

## 统一 CLI

```bash
BridGes start
# 本地开发（Next.js 开发服务器，需先 npm install）
BridGes start --profile development
```

`start` 默认以生产 profile 同步启动构建后的 **Web、API、后台执行器与提醒
调度器**四个进程。启动前会校验依赖与数据目录权限、获取数据目录单实例锁、
执行数据库迁移，然后等待 API `/health/ready` 与 Web 就绪并输出本地电脑端
访问地址。任一关键服务失败时整体非零退出并显示可操作中文错误；收到
`Ctrl+C`（Windows 还支持 `Ctrl+Break`，容器内为 `SIGTERM`）后按顺序停止
子进程并释放锁。同一数据目录不能重复启动第二个实例；进程异常终止后锁自动
释放，可安全恢复。`serve` 是历史同实现别名，与 `start` 指向同一实现。

后台进程也可以单独运行：

```bash
BridGes worker       # 后台执行器：周期性清理待删除对象与孤立文件
BridGes scheduler    # 提醒调度器：提醒功能由后续版本交付，当前周期心跳
```

## 诊断、迁移与健康检查

四种运行方式都支持相同的 CLI 语义：

```bash
BridGes doctor
BridGes migrate
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
curl http://127.0.0.1:8000/health/degraded
```

## 生产合同说明

- 部署范围仅限源码 Conda 环境、源码 `.venv` 环境与 Docker/Podman Compose；
  不提供 Windows 原生安装包、自更新器或系统常驻服务。
- 生产镜像使用标准 Python / Node.js，不依赖 Conda `agent`。
- `environment.yml` 仅用于本地开发环境声明。
- Web、API、后台执行器和提醒调度器在不同载体中均保持独立进程边界；统一
  CLI 只是监管入口，容器路径由 Compose 编排同一组进程。
- Web 只承诺电脑端使用，不承诺手机、平板或移动浏览器访问。
- `science-companion` 是迁移期兼容入口，与 `BridGes` 指向同一实现，由退出 Issue 删除。
