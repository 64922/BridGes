# 手动分进程运行示例

T008 支持以下四种生产运行合同骨架：

- 手动分进程（本目录示例）
- 统一 CLI：`science-companion serve`
- Docker：`docker compose -f infra/compose/docker-compose.yml up`
- Podman：`podman-compose -f infra/compose/docker-compose.yml up`

四种方式读取同一配置 Schema 和密钥引用规则。

## 统一配置 Schema

所有运行方式都通过 `SCIENCE_COMPANION_*` 环境变量读取配置。

常用变量：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `SCIENCE_COMPANION_ENVIRONMENT` | 运行环境标识 | `development` |
| `SCIENCE_COMPANION_API_HOST` | API 监听地址 | `127.0.0.1` |
| `SCIENCE_COMPANION_API_PORT` | API 端口 | `8000` |
| `SCIENCE_COMPANION_WEB_PORT` | Web 端口 | `3000` |
| `SCIENCE_COMPANION_WEB_DEV` | Web 是否使用 Next.js dev | `true` |
| `SCIENCE_COMPANION_SESSION_COOKIE_SECURE` | Cookie 是否强制 secure | `false` |

密钥类变量（如 `SCIENCE_COMPANION_SECRET_KEY`、`SCIENCE_COMPANION_DATABASE_URL`、`SCIENCE_COMPANION_QWEN_API_KEY`）既可以直接设置，也推荐通过文件引用：

```bash
export SCIENCE_COMPANION_SECRET_KEY_FILE=/run/secrets/secret_key
export SCIENCE_COMPANION_DATABASE_URL_FILE=/run/secrets/database_url
```

文件引用的后缀为 `<NAME>_FILE`，其内容会被读取并赋值给对应字段。生产环境（Docker / Podman / systemd）应优先使用文件引用，避免把密钥写入普通环境变量。

## 手动分进程

终端 1（API）：

```bash
export SCIENCE_COMPANION_ENVIRONMENT=production
export SCIENCE_COMPANION_API_HOST=127.0.0.1
export SCIENCE_COMPANION_API_PORT=8000
science-companion api
```

终端 2（Web）：

```bash
export SCIENCE_COMPANION_WEB_DEV=true
export SCIENCE_COMPANION_WEB_PORT=3000
export API_BASE_URL=http://127.0.0.1:8000
cd apps/web
npm run dev
```

## 统一 CLI

```bash
science-companion serve
# 或生产模式
science-companion serve --profile production
```

`serve` 会以独立子进程启动 API 与 Web，收到 `Ctrl+C` 后先 `SIGTERM`、超时后再 `SIGKILL`。

## 诊断、迁移与健康检查

四种运行方式都支持相同的 CLI 语义：

```bash
science-companion doctor
science-companion migrate
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
curl http://127.0.0.1:8000/health/degraded
```

## 生产合同说明

- 生产镜像使用标准 Python / Node.js，不依赖 Conda `agent`。
- `environment.yml` 仅用于本地开发环境声明。
- Web、API 和 worker 在不同载体中均保持独立进程边界；统一 CLI 只是监管入口。
